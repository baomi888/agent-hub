# -*- coding: utf-8 -*-
"""流式问答接口（SSE）。

三条路径（由 req.mode 决定，默认 auto 自动判断）：
  auto   — 绑定 kb_id 且知识库非空 → RAG；否则 → Agent
  rag    — 强制 RAG 模式（检索 + 提示词组装 + LLM，不走 Agent 工具链）
  agent  — 强制 Agent 模式（create_agent + 4 工具 + LangGraph checkpoint）
  llm    — 强制纯 LLM（多轮对话，不调工具不检索）

SSE 事件协议：
  event: status     { text: "..." }           状态提示
  event: tool       { name: "...", input: "...", output: "..." }  Agent 工具调用
  event: token      { text: "...", refs: "" }  增量 token
  event: title      { title: "..." }          自动生成的会话标题（仅首轮）
  event: done       { answer: "...", refs: "", title: "", mode: "" }
  event: error      { detail: "..." }
"""

import asyncio
import base64
import json
import mimetypes
import os
import time
import uuid

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, Field

from core import config, session as store
from core.llm import get_llm, get_vision_llm
from kb import pipelines
from kb.rag_core import SYSTEM_PROMPT, describe_api_error

router = APIRouter()

# 聊天附件上传目录（图片理解用）
CHAT_UPLOAD_DIR = os.path.join(config.DATA_DIR, "chat_uploads")
# 附件限制：视觉模型只吃图片，单张 20MB / 单次 9 张足够，超了直接拒
MAX_CHAT_UPLOAD_MB = 20
MAX_CHAT_FILES = 9
# 上传件保留时长：图片理解完就不需要了，超时自动清，避免目录无限增长
UPLOAD_TTL_HOURS = 24

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


class ChatRequest(BaseModel):
    sid: str = Field(...)
    question: str = Field(...)
    top_k: int = Field(default=config.TOP_K_DEFAULT, gt=0)
    kb_id_override: str | None = Field(default=None)
    mode: str = Field(default="auto", description="auto / rag / agent / llm")
    attachments: list[dict] = Field(default_factory=list, description="附件列表（图片理解用）")
    location: dict | None = Field(
        default=None,
        description="用户地理定位：{ lat: float, lon: float }，由前端地理定位得到，用于天气/本地问答免手输城市",
    )


# ==================== SSE 帧 ====================

def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _pick_mode(req_mode: str, kb_id: str | None, kb_exists: bool) -> str:
    """auto → rag / agent 自动路由。"""
    if req_mode != "auto":
        return req_mode
    if kb_id and kb_exists:
        return "rag"
    return "agent"


# ==================== 附件上传 ====================

def _cleanup_old_uploads() -> None:
    """清掉超过 TTL 的历史上传件（目录只在上传时顺带扫一次，不做常驻任务）。"""
    try:
        names = os.listdir(CHAT_UPLOAD_DIR)
    except FileNotFoundError:
        return
    deadline = time.time() - UPLOAD_TTL_HOURS * 3600
    for name in names:
        p = os.path.join(CHAT_UPLOAD_DIR, name)
        try:
            if os.path.isfile(p) and os.path.getmtime(p) < deadline:
                os.unlink(p)
        except OSError:
            pass


@router.post("/upload")
async def upload_attachments(files: list[UploadFile] = File(...)):
    """上传聊天附件（图片），返回服务端路径供图片理解使用。

    只有图片会被视觉模型消费；其他格式即使上传成功也不会参与回答。
    """
    if len(files) > MAX_CHAT_FILES:
        raise HTTPException(status_code=400, detail=f"一次最多上传 {MAX_CHAT_FILES} 个附件")

    os.makedirs(CHAT_UPLOAD_DIR, exist_ok=True)
    _cleanup_old_uploads()

    results = []
    for f in files:
        ext = os.path.splitext(f.filename or "")[1] or ".bin"
        safe_name = f"{uuid.uuid4().hex}{ext}"
        path = os.path.join(CHAT_UPLOAD_DIR, safe_name)
        content = await f.read()
        if len(content) > MAX_CHAT_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(
                status_code=400, detail=f"附件过大（>{MAX_CHAT_UPLOAD_MB}MB）：{f.filename}"
            )
        with open(path, "wb") as out:
            out.write(content)
        results.append({
            "filename": f.filename,
            "path": path,
            "size": len(content),
            "type": f.content_type or "application/octet-stream",
        })
    return {"files": results}


# ==================== 图片理解辅助 ====================

def _has_image(attachments: list[dict]) -> bool:
    """附件中是否包含图片。"""
    for a in attachments:
        t = (a.get("type") or "").lower()
        if t.startswith("image/"):
            return True
    return False


def _image_to_data_url(path: str) -> str:
    """把本地图片转为 base64 data URL（供视觉模型读取）。"""
    mime, _ = mimetypes.guess_type(path)
    mime = mime or "image/png"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:{mime};base64,{b64}"


def _build_vision_user_message(question: str, attachments: list[dict]) -> HumanMessage:
    """组装多模态用户消息：文本 + 图片。"""
    content: list[dict] = [{"type": "text", "text": question or "请描述这张图片"}]
    for a in attachments:
        t = (a.get("type") or "").lower()
        if not t.startswith("image/"):
            continue
        path = a.get("path")
        if not path or not os.path.exists(path):
            continue
        content.append({
            "type": "image_url",
            "image_url": {"url": _image_to_data_url(path)},
        })
    return HumanMessage(content=content)


# ==================== 流式输出 ====================

class _Acc:
    """流式累计器：异步生成器没法 return 值，用对象把结果带出去。"""

    def __init__(self) -> None:
        self.text = ""
        self.error: str | None = None


async def _stream_llm(llm, messages, request: Request, acc: _Acc):
    """逐 token 产出增量 SSE。

    早期实现每帧回传"累计全文"，千 token 的回答会产生 O(n²) 的传输量；
    这里只发 delta，由前端自己累加。
    """
    try:
        async for chunk in llm.astream(messages):
            if await request.is_disconnected():
                return
            delta = chunk.content or ""
            if isinstance(delta, list):
                # 多模态 content list，只取文本
                delta = "".join(
                    p.get("text", "")
                    for p in delta
                    if isinstance(p, dict) and p.get("type") == "text"
                )
            if not isinstance(delta, str) or not delta:
                continue
            acc.text += delta
            yield _sse("token", {"text": delta})
    except Exception as e:
        acc.error = describe_api_error(e)
        yield _sse("error", {"detail": acc.error})


# ==================== 主路由 ====================

@router.post("/stream")
async def chat_stream(req: ChatRequest, request: Request):
    conv = store.get_conversation(req.sid)
    if not conv:
        raise HTTPException(status_code=404, detail=f"会话不存在：{req.sid}")

    kb_id = req.kb_id_override or conv.get("kb_id")
    kb_exists = bool(kb_id and pipelines.exists(kb_id))
    mode = _pick_mode(req.mode, kb_id, kb_exists)

    # RAG 模式如果知识库不存在 → 降级为 agent
    if mode == "rag" and not kb_exists:
        mode = "agent"

    # 解析用户地理定位：坐标 -> 逆地理城市名（天气/本地问答免手输城市）。
    # 后端无高德 Key 时 reverse_geocode_city 返回 None，前端仍传了坐标，
    # 天气工具会用 Open-Meteo 按坐标查，只是展示名退化为「你所在位置」。
    location_ctx: dict | None = None
    if isinstance(req.location, dict):
        try:
            flat = float(req.location.get("lat"))
            flon = float(req.location.get("lon"))
            city = None
            try:
                from agent.tools import reverse_geocode_city
                city = reverse_geocode_city(flat, flon)
            except Exception:
                city = None
            location_ctx = {"lat": flat, "lon": flon, "city": city}
        except (TypeError, ValueError):
            location_ctx = None

    store.add_message(req.sid, "user", req.question)
    should_gen_title = store.count_messages(req.sid) <= 2

    async def event_generator():
        refs_md = ""

        # ---- 0. 图片理解模式：有图片附件时用视觉模型直接回答 ----
        if _has_image(req.attachments):
            yield _sse("status", {"text": "图片理解模式，正在分析图片..."})
            # RAG 模式下顺带检索知识库，拼入文本
            vision_question = req.question
            if kb_id and kb_exists:
                try:
                    pipe = pipelines.get_or_create(kb_id)
                    docs_scores = pipe.retrieve_with_score(req.question, req.top_k)
                    if docs_scores:
                        refs_md = pipe._format_refs(docs_scores)
                        sources = pipe.build_sources(docs_scores)
                        yield _sse("sources", {"items": sources})
                        references = "\n\n".join(
                            f"[{i + 1}] （来源：{d.metadata.get('source', '?')}）\n{d.page_content}"
                            for i, (d, _) in enumerate(docs_scores)
                        )
                        vision_question = f"【参考资料】\n{references}\n\n【用户问题】\n{req.question}"
                except Exception:
                    pass

            history = store.get_messages(req.sid, include_system=False)[:-1]
            messages = [SystemMessage(content="你是一个能理解图片的 AI 助手。请结合图片内容用中文简洁作答。")]
            for m in history:
                if m["role"] == "user":
                    messages.append(HumanMessage(content=m["content"]))
                elif m["role"] == "assistant":
                    messages.append(AIMessage(content=m["content"]))
            messages.append(_build_vision_user_message(vision_question, req.attachments))

            acc = _Acc()
            try:
                async for frame in _stream_llm(get_vision_llm(), messages, request, acc):
                    yield frame
            except Exception as e:
                yield _sse("error", {"detail": describe_api_error(e)})
                return
            if acc.error:
                return
            acc_text = acc.text

            # 收尾
            store.add_message(req.sid, "assistant", acc_text, refs=refs_md)
            gen_title = ""
            if should_gen_title and acc_text:
                try:
                    gen_title = await asyncio.to_thread(_generate_title, req.question, acc_text)
                    if gen_title:
                        store.rename_conversation(req.sid, gen_title)
                        yield _sse("title", {"title": gen_title})
                except Exception:
                    pass
            yield _sse("done", {"answer": acc_text, "refs": refs_md, "title": gen_title, "mode": "vision"})
            return

        # ---- 1. Agent 模式 ----
        if mode == "agent":
            yield _sse("status", {"text": "Agent 模式，正在规划任务..."})
            try:
                from agent.builder import build_agent
                # 把当前会话绑定的知识库告诉模型，否则 kb_search 只能瞎猜 kb_id
                agent = await asyncio.to_thread(build_agent, kb_id, location_ctx)

                # 组装历史 + 当前问题
                history = store.get_messages(req.sid, include_system=False)[:-1]
                messages = []
                for m in history:
                    if m["role"] == "user":
                        messages.append(HumanMessage(content=m["content"]))
                    elif m["role"] == "assistant":
                        messages.append(AIMessage(content=m["content"]))
                messages.append(HumanMessage(content=req.question))

                # LangGraph checkpoint thread_id = 会话 id，天然多轮记忆
                config_ = {"configurable": {"thread_id": req.sid}}
                full_text = ""

                # stream_mode=["updates","messages"] 事件为 (mode字符串, payload)
                for event in agent.stream(
                    {"messages": messages},
                    config=config_,
                    stream_mode=["updates", "messages"],
                ):
                    if await request.is_disconnected():
                        return

                    if not isinstance(event, tuple) or len(event) != 2:
                        continue
                    mode_name, payload = event

                    # -- messages 模式：payload=(chunk, metadata)，逐 token --
                    if mode_name == "messages":
                        chunk, _meta = payload
                        if hasattr(chunk, "content") and chunk.content:
                            delta = chunk.content
                            if isinstance(delta, list):
                                # 多模态 content list，只取文本
                                delta = "".join(
                                    p.get("text", "") for p in delta
                                    if isinstance(p, dict) and p.get("type") == "text"
                                )
                            if isinstance(delta, str) and delta:
                                full_text += delta
                                yield _sse("token", {"text": delta})

                    # -- updates 模式：payload={node: {"messages": [...]}}，工具调用/返回 --
                    elif mode_name == "updates" and isinstance(payload, dict):
                        for node_output in payload.values():
                            if not (isinstance(node_output, dict) and "messages" in node_output):
                                continue
                            for msg in node_output["messages"]:
                                # 模型发起工具调用：AIMessage 带 tool_calls
                                if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                                    for tc in msg.tool_calls:
                                        yield _sse("tool", {
                                            "name": tc.get("name", "?"),
                                            "input": str(tc.get("args", {}))[:200],
                                        })
                                # 工具返回结果：ToolMessage
                                elif isinstance(msg, ToolMessage):
                                    yield _sse("tool", {
                                        "name": getattr(msg, "name", "?"),
                                        "output": str(getattr(msg, "content", ""))[:300],
                                    })

                acc_text = full_text

            except ImportError as e:
                acc_text = f"Agent 模式未初始化：{e}"
                yield _sse("error", {"detail": acc_text})
                return
            except Exception as e:
                acc_text = f"Agent 执行失败：{describe_api_error(e)}"
                yield _sse("error", {"detail": acc_text})
                return

        # ---- 2. RAG 模式 ----
        elif mode == "rag":
            yield _sse("status", {"text": f"RAG 模式，正在检索知识库（{kb_id}）..."})
            try:
                pipe = pipelines.get_or_create(kb_id)
                docs_scores = pipe.retrieve_with_score(req.question, req.top_k)
            except Exception as e:
                yield _sse("error", {"detail": describe_api_error(e)})
                return

            if docs_scores:
                refs_md = pipe._format_refs(docs_scores)
                sources = pipe.build_sources(docs_scores)
                yield _sse("sources", {"items": sources})
                yield _sse("status", {"text": f"检索到 {len(docs_scores)} 条片段"})
            else:
                yield _sse("status", {"text": "知识库未命中"})

            yield _sse("status", {"text": "正在生成答案..."})
            # 复用上面已经检索到的结果，不重复检索（早期实现在这里又检了一次，
            # 而且用的是默认 top_k，等于前端调的检索条数被悄悄忽略）
            messages = _build_messages(req.sid, req.question, kb_id, docs_scores)

            acc = _Acc()
            try:
                async for frame in _stream_llm(get_llm(), messages, request, acc):
                    yield frame
            except Exception as e:
                yield _sse("error", {"detail": describe_api_error(e)})
                return
            if acc.error:
                return
            acc_text = acc.text

        # ---- 3. 纯 LLM 模式 ----
        else:
            yield _sse("status", {"text": "纯 LLM 模式..."})
            history = store.get_messages(req.sid, include_system=False)[:-1]
            messages = [SystemMessage(content="你是一个友好、专业的 AI 助手。用中文简洁作答。")]
            for m in history:
                if m["role"] == "user":
                    messages.append(HumanMessage(content=m["content"]))
                elif m["role"] == "assistant":
                    messages.append(AIMessage(content=m["content"]))
            messages.append(HumanMessage(content=req.question))

            acc = _Acc()
            try:
                async for frame in _stream_llm(get_llm(), messages, request, acc):
                    yield frame
            except Exception as e:
                yield _sse("error", {"detail": describe_api_error(e)})
                return
            if acc.error:
                return
            acc_text = acc.text

        # ---- 收尾：存 assistant 消息 + 生成标题 ----
        store.add_message(req.sid, "assistant", acc_text, refs=refs_md)

        gen_title = ""
        if should_gen_title and acc_text:
            try:
                gen_title = await asyncio.to_thread(_generate_title, req.question, acc_text)
                if gen_title:
                    store.rename_conversation(req.sid, gen_title)
                    yield _sse("title", {"title": gen_title})
            except Exception:
                pass

        yield _sse("done", {
            "answer": acc_text,
            "refs": refs_md,
            "title": gen_title,
            "mode": mode,
        })

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=SSE_HEADERS)


# ==================== 辅助 ====================

def _build_messages(sid: str, question: str, kb_id: str | None, docs_scores) -> list:
    """组装 LLM 输入：历史 + 已检索到的参考资料。

    docs_scores 由调用方检索一次后传入，这里不再重复检索，
    避免用户设置的 top_k 被默认值覆盖、也省掉一次 embedding 请求。
    """
    history_raw = store.get_messages(sid, include_system=False)[:-1]
    messages = []
    references = ""
    if kb_id and docs_scores:
        references = "\n\n".join(
            f"[{i + 1}] （来源：{d.metadata.get('source', '?')}）\n{d.page_content}"
            for i, (d, _) in enumerate(docs_scores)
        )
    messages.append(
        SystemMessage(content=SYSTEM_PROMPT if references else "你是一个友好、专业的 AI 助手。用中文简洁作答。")
    )
    for m in history_raw:
        if m["role"] == "user":
            messages.append(HumanMessage(content=m["content"]))
        elif m["role"] == "assistant":
            messages.append(AIMessage(content=m["content"]))
    user_prompt = (
        f"【参考资料】\n{references}\n\n【用户问题】\n{question}" if references else question
    )
    messages.append(HumanMessage(content=user_prompt))
    return messages


def _generate_title(question: str, answer: str) -> str:
    llm = get_llm(temperature=0)
    try:
        resp = llm.invoke([HumanMessage(content=f"请为以下对话生成简短中文标题（≤15字），直接输出：用户提问：{question}")])
        title = (resp.content or "").strip().strip('"').strip("'")
        if len(title) > 20:
            title = title[:15] + "..."
        return title or question[:15]
    except Exception:
        return question[:15] if len(question) > 15 else question
