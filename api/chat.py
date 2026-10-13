# -*- coding: utf-8 -*-
from __future__ import annotations
"""流式问答接口（SSE）。

三条路径（由 req.mode 决定，默认 auto 自动判断）：
  auto   — 绑定 kb_id 且知识库非空 → RAG；否则 → Agent
  rag    — 强制 RAG 模式（检索 + 提示词组装 + LLM，不走 Agent 工具链）
  agent  — 强制 Agent 模式（create_agent + 4 工具 + LangGraph checkpoint）
  llm    — 强制纯 LLM（多轮对话，不调工具不检索）

SSE 事件协议：
  event: status     { text: "..." }           状态提示
  event: tool       { name: "...", input: "...", output: "..." }  Agent 工具调用
  event: sources    { items: [...] }           引用溯源条目（含每条 strength）
  event: grounding  { strength: "...", best: 0.31, weak: true }
                   检索依据强度。weak=true 时前端在回答上方挂「依据较弱」提示条，
                   数据来源是 kb/rag_core.py::assess_grounding()
  event: token      { text: "...", refs: "" }  增量 token
  event: title      { title: "..." }          自动生成的会话标题（仅首轮）
  event: done       { answer: "...", refs: "", title: "", mode: "", message_id: 123 }
                    message_id 是 assistant 消息在 messages 表里的 id，赞踩按它落库
  event: error      { detail: "..." }
"""

import asyncio
import base64
import json
import mimetypes
import os
import time
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, Field

from core import config, quota, session as store, upload
from core.auth import get_current_user
from core.llm import get_llm, get_vision_llm
from core import scenarios as scenarios_mod
from kb import pipelines
from kb.rag_core import SYSTEM_PROMPT, assess_grounding, describe_api_error, grounding_note, rewrite_query

# 默认要登录：整组路由统一挂鉴权，新增端点时不会漏
router = APIRouter(dependencies=[Depends(get_current_user)])

# 聊天附件上传目录（图片理解用）
CHAT_UPLOAD_DIR = os.path.join(config.DATA_DIR, "chat_uploads")
# 附件限制：视觉模型只吃图片，单张 20MB / 单次 9 张足够，超了直接拒
MAX_CHAT_UPLOAD_MB = 20
MAX_CHAT_FILES = 9
# 只收这些扩展名的图片：视觉模型只消费图片，别的格式传进来也是白传（还占磁盘）
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
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
    top_k: int = Field(default=config.TOP_K_DEFAULT, gt=0, le=config.TOP_K_MAX)
    kb_id_override: str | None = Field(default=None)
    mode: str = Field(default="auto", description="auto / rag / agent / llm")
    scenario: str | None = Field(
        default=None,
        description="场景预设：kaoyan(考研复习) / notes(课程笔记) / paper(论文写作)。"
        "非空时拼进 system prompt，非白名单值按未选处理。",
    )
    attachments: list[dict] = Field(default_factory=list, description="附件列表（图片理解用）")
    location: dict | None = Field(
        default=None,
        description="用户地理定位。两种形态：{ lat, lon }（浏览器定位，精度高）或 "
        "{ city }（公网 http 下浏览器定位不可用，由后端按来源 IP 推断）。"
        "有坐标时后端会逆地理补 city，用于天气/本地问答免手输城市",
    )


# ==================== SSE 帧 ====================

def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# 允许进入路由表的对话模式。白名单之外一律按 auto 自动路由，
# 避免模板目录 / 前端误带脏值直接穿透到下游分支。
VALID_MODES = ("auto", "rag", "agent", "llm")


def _pick_mode(req_mode: str, kb_id: str | None, kb_exists: bool) -> str:
    """auto → rag / agent 自动路由；其它值先过白名单再放行。"""
    if req_mode in VALID_MODES and req_mode != "auto":
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
async def upload_attachments(files: list[UploadFile] = File(...)) -> dict:
    """上传聊天附件（图片），返回服务端路径供图片理解使用。

    只收图片；其他格式即使上传成功也不会参与回答，直接拒绝省磁盘省流量。
    """
    if len(files) > MAX_CHAT_FILES:
        raise HTTPException(status_code=400, detail=f"一次最多上传 {MAX_CHAT_FILES} 个附件")

    # 整批先校验再落盘：有一张不合格就整个拒绝，
    # 否则前面几张写好了、后面报错，会留下一半的孤儿文件
    rejected = [f.filename for f in files if not _is_supported_image(f.filename, f.content_type)]
    if rejected:
        raise HTTPException(
            status_code=400,
            detail=f"仅支持图片附件（jpg/jpeg/png/gif/webp/bmp），已拒绝：{rejected[0]}",
        )

    os.makedirs(CHAT_UPLOAD_DIR, exist_ok=True)
    _cleanup_old_uploads()

    results = []
    for f in files:
        ext = os.path.splitext(f.filename or "")[1].lower() or ".jpg"
        safe_name = f"{uuid.uuid4().hex}{ext}"
        path = os.path.join(CHAT_UPLOAD_DIR, safe_name)
        # 分块写盘：以前是 await f.read() 整份读进内存再判大小，
        # 1GB 的附件在 2G 内存的机器上等判出来时进程已经被 OOM killer 带走了
        size = await upload.save_stream(f, path, MAX_CHAT_UPLOAD_MB * 1024 * 1024)
        results.append({
            "filename": f.filename,
            "path": path,
            "size": size,
            "type": f.content_type or "application/octet-stream",
        })
    return {"files": results}


def _is_supported_image(filename: str | None, content_type: str | None) -> bool:
    """扩展名 + content-type 双保险判断是否为可接受的图片。"""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in _IMAGE_EXTS:
        return False
    if content_type:
        return content_type.lower().startswith("image/")
    return True


# ==================== 图片理解辅助 ====================

def _has_image(attachments: list[dict]) -> bool:
    """附件中是否包含图片。"""
    for a in attachments:
        t = (a.get("type") or "").lower()
        if t.startswith("image/"):
            return True
    return False


def _resolve_upload_path(path: str) -> str | None:
    """只允许读取聊天上传目录内的文件。

    attachments[].path 完全由客户端提供，直接 open() 等于开放任意本地文件读取
    （甚至可以把 .env / sessions.db 喂给第三方视觉模型）。上传时返回的 path
    都在 CHAT_UPLOAD_DIR 下，realpath 前缀校验足以挡住目录穿越。
    """
    try:
        root = os.path.realpath(CHAT_UPLOAD_DIR)
        real = os.path.realpath(path)
        if real == root or real.startswith(root + os.sep):
            return real
    except (TypeError, ValueError, OSError):
        return None
    return None


def _image_to_data_url(path: str) -> str:
    """把本地图片转为 base64 data URL（供视觉模型读取）。

    大图先压到长边 1024 再编码：20MB 原图直接 base64 发 qwen-vl-plus
    既慢又易被拒收。Pillow 不可用时退回原图，不引入硬依赖。
    """
    data, mime = _load_image_bytes(path)
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def _load_image_bytes(path: str) -> tuple[bytes, str]:
    """读取图片字节；Pillow 可用时压缩（长边 ≤1024，JPEG q85）。"""
    try:
        import io

        from PIL import Image

        with Image.open(path) as im:
            if im.mode in ("RGBA", "P", "LA"):
                im = im.convert("RGB")
            im.thumbnail((1024, 1024))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=85)
            return buf.getvalue(), "image/jpeg"
    except Exception:
        pass
    mime, _ = mimetypes.guess_type(path)
    with open(path, "rb") as f:
        return f.read(), mime or "image/png"


def _build_vision_user_message(question: str, attachments: list[dict]) -> HumanMessage:
    """组装多模态用户消息：文本 + 图片。"""
    content: list[dict] = [{"type": "text", "text": question or "请描述这张图片"}]
    for a in attachments:
        t = (a.get("type") or "").lower()
        if not t.startswith("image/"):
            continue
        path = a.get("path")
        resolved = _resolve_upload_path(path) if isinstance(path, str) else None
        if not resolved or not os.path.exists(resolved):
            continue
        content.append({
            "type": "image_url",
            "image_url": {"url": _image_to_data_url(resolved)},
        })
    return HumanMessage(content=content)


# ==================== 流式输出 ====================

class _Acc:
    """流式累计器：异步生成器没法 return 值，用对象把结果带出去。"""

    def __init__(self) -> None:
        self.text = ""
        self.error: str | None = None


async def _stream_llm(llm, messages, request: Request, acc: _Acc) -> None:
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
async def chat_stream(req: ChatRequest, request: Request, uid: str = Depends(get_current_user)) -> StreamingResponse:
    # 限流 + 每日问答额度。放在 event_generator 外面是对的：
    # 流一旦开始，响应头（200）就发出去了，那时候再想回 429 已经来不及，
    # 只会变成一条断掉的 SSE 流，前端读不出任何提示。
    # 先验会话再扣费：无效请求（会话不存在）不该消耗每日问答额度
    conv = await asyncio.to_thread(store.get_conversation, uid, req.sid)
    if not conv:
        raise HTTPException(status_code=404, detail=f"会话不存在：{req.sid}")

    await asyncio.to_thread(quota.consume, uid, "ask")

    kb_id = req.kb_id_override or conv.get("kb_id")
    # 前端可以塞任意 kb_id（kb_id_override），这里必须认一次主。
    # 不是自己的库按"没绑"处理：题照答，但不带别人的资料。
    if kb_id and pipelines.owner_of(kb_id) != uid:
        kb_id = None
    # exists() → peek() 要读 sqlite，是同步 I/O，别卡住事件循环
    kb_exists = bool(kb_id and await asyncio.to_thread(pipelines.exists, uid, kb_id))
    mode = _pick_mode(req.mode, kb_id, kb_exists)

    # RAG 模式如果知识库不存在 → 降级为 agent
    if mode == "rag" and not kb_exists:
        mode = "agent"

    # 解析用户地理定位，两种来源都要接住（天气/本地问答免手输城市）：
    #   ① 浏览器定位（HTTPS / localhost 才可用）→ 带 lat/lon，精度最高
    #   ② 公网 http 下浏览器 geolocation 不可用 → 前端改调 /api/geo/locate
    #      由服务端按来源 IP 兜底，只有 city、没有坐标
    # 以前只认 ①：拿不到 lat/lon 就整条置 None，② 的结果会被白白丢掉。
    location_ctx: dict | None = None
    if isinstance(req.location, dict):
        raw_city = req.location.get("city")
        city = raw_city.strip() if isinstance(raw_city, str) else ""
        try:
            flat = float(req.location.get("lat"))
            flon = float(req.location.get("lon"))
            have_xy = True
        except (TypeError, ValueError):
            flat = flon = 0.0
            have_xy = False

        if have_xy:
            # 有坐标：逆地理补城市名。后端没高德 Key 时返回 None，
            # 天气工具会退到 Open-Meteo 按坐标查，只是展示名变成「你所在位置」。
            if not city:
                try:
                    from agent.tools import reverse_geocode_city

                    rg = await asyncio.to_thread(reverse_geocode_city, flat, flon)
                    city = rg.strip() if isinstance(rg, str) else ""
                except Exception:
                    city = ""
            location_ctx = {"lat": flat, "lon": flon, "city": city}
        elif city:
            location_ctx = {"city": city}

    await asyncio.to_thread(store.add_message, uid, req.sid, "user", req.question)
    should_gen_title = (await asyncio.to_thread(store.count_messages, uid, req.sid)) <= 2

    async def event_generator() -> None:
        refs_md = ""

        # 标题只依赖用户问题（提示词里答案根本没用到），所以一开始就和答案流式
        # 生成并行跑：收尾时它多半已经算完，省掉原来在 done 之前干等的一次
        # LLM 往返（约 1~2s）。错误/中断提前 return 时结果丢弃，标题保持默认。
        title_task: asyncio.Task | None = None
        if should_gen_title:
            title_task = asyncio.create_task(
                asyncio.to_thread(_generate_title, req.question)
            )
            _BG_TASKS.add(title_task)
            title_task.add_done_callback(_BG_TASKS.discard)

        # ---- 0. 图片理解模式：有图片附件时用视觉模型直接回答 ----
        if _has_image(req.attachments):
            yield _sse("status", {"text": "图片理解模式，正在分析图片..."})
            history = store.get_messages(uid, req.sid, include_system=False)[:-1]
            # RAG 模式下顺带检索知识库，拼入文本
            vision_question = req.question
            vision_system = "你是一个能理解图片的 AI 助手。请结合图片内容用中文简洁作答。"
            vision_system += scenarios_mod.get_scenario_prompt(req.scenario)
            if kb_id and kb_exists:
                try:
                    # 同步检索会堵事件循环（embed_query 实测约 250ms），必须丢线程
                    pipe = await asyncio.to_thread(pipelines.get_or_create, uid, kb_id)
                    query = await asyncio.to_thread(rewrite_query, req.question, history)
                    docs_scores = await asyncio.to_thread(
                        pipe.retrieve_with_score, query, req.top_k
                    )
                    if docs_scores:
                        refs_md = pipe._format_refs(docs_scores)
                        sources = pipe.build_sources(docs_scores)
                        yield _sse("sources", {"items": sources})
                        # 图片理解也走同一套依据判定，提示条口径保持一致
                        g = assess_grounding(docs_scores)
                        yield _sse("grounding", g)
                        references = "\n\n".join(
                            f"[{i + 1}] （来源：{d.metadata.get('source', '?')}）\n{d.page_content}"
                            for i, (d, _) in enumerate(docs_scores)
                        )
                        vision_question = f"【参考资料】\n{references}\n\n【用户问题】\n{req.question}"
                        vision_system += grounding_note(g)
                except Exception:
                    yield _sse("status", {"text": "知识库检索失败，已仅按图片作答"})

            messages = [SystemMessage(content=vision_system)]
            for m in history:
                if m["role"] == "user":
                    messages.append(HumanMessage(content=m["content"]))
                elif m["role"] == "assistant":
                    messages.append(AIMessage(content=m["content"]))
            messages.append(_build_vision_user_message(vision_question, req.attachments))

            acc = _Acc()
            try:
                async for frame in _stream_llm(get_vision_llm(max_tokens=2048), messages, request, acc):
                    yield frame
            except Exception as e:
                yield _sse("error", {"detail": describe_api_error(e)})
                return
            if acc.error:
                return
            acc_text = acc.text

            # 收尾：标题并行任务多半已完成，这里 await 基本零等待
            msg_id = await asyncio.to_thread(store.add_message, uid, req.sid, "assistant", acc_text, refs=refs_md)
            gen_title = await _take_title(title_task, acc_text)
            if gen_title:
                await asyncio.to_thread(store.rename_conversation, uid, req.sid, gen_title)
                yield _sse("title", {"title": gen_title})
            # message_id 回给前端：赞踩要按消息 id 落库，前端自己生成的 uid 后端不认
            yield _sse("done", {
                "answer": acc_text,
                "refs": refs_md,
                "title": gen_title,
                "mode": "vision",
                "message_id": msg_id,
            })
            return

        # ---- 1. Agent 模式 ----
        if mode == "agent":
            yield _sse("status", {"text": "Agent 模式，正在规划任务..."})
            try:
                from agent.builder import build_agent
                # 只告诉模型"有没有绑库"，不告诉它是哪个库：
                # kb_id 走下面的 configurable 注入，模型没有机会改写它
                agent = await asyncio.to_thread(build_agent, bool(kb_id and kb_exists), location_ctx, req.scenario)

                # 组装历史 + 当前问题
                history = (await asyncio.to_thread(store.get_messages, uid, req.sid, include_system=False))[:-1]
                messages = []
                for m in history:
                    if m["role"] == "user":
                        messages.append(HumanMessage(content=m["content"]))
                    elif m["role"] == "assistant":
                        messages.append(AIMessage(content=m["content"]))
                messages.append(HumanMessage(content=req.question))

                # LangGraph checkpoint thread_id = 会话 id，天然多轮记忆。
                # owner_id / kb_id 一并注入：工具内部只认这里的值，
                # 模型在参数里怎么编 kb_id 都影响不到真实检索目标。
                # recursion_limit 兜底：工具返回"不可用"时模型反复重试是纯烧钱，
                # 硬限制整轮的工具步数（LLM 往返 + 工具执行都占步数）
                config_ = {
                    "recursion_limit": 10,
                    "configurable": {
                        "thread_id": req.sid,
                        "owner_id": uid,
                        "kb_id": kb_id if (kb_id and kb_exists) else None,
                    },
                }
                full_text = ""

                # stream_mode=["updates","messages"] 事件为 (mode字符串, payload)
                # 必须走 astream：同步 stream() 会把整个事件循环堵住，
                # 一次 Agent 回答十几秒，期间建库/上传/切会话全部排队
                async for event in agent.astream(
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
            # 追问（"那它呢"）先结合上下文改写成独立问题再检索；不需要/失败时原样返回。
            # 用改写后的问题当检索词，检索结果缓存的键也随之正确，不会跨上下文串结果。
            history = store.get_messages(uid, req.sid, include_system=False)[:-1]
            query = await asyncio.to_thread(rewrite_query, req.question, history)
            if query != req.question:
                yield _sse("status", {"text": f"已结合上下文改写检索词：{query}"})
            try:
                pipe = await asyncio.to_thread(pipelines.get_or_create, uid, kb_id)
                docs_scores = await asyncio.to_thread(pipe.retrieve_with_score, query, req.top_k)
            except Exception as e:
                yield _sse("error", {"detail": describe_api_error(e)})
                return

            grounding: dict | None = None
            if docs_scores:
                refs_md = pipe._format_refs(docs_scores)
                sources = pipe.build_sources(docs_scores)
                yield _sse("sources", {"items": sources})
                # 依据强度单独发一帧：前端要在回答上方挂"依据较弱"的提示条，
                # 不能等 done——用户是边流边读的，提示必须赶在正文前面出现。
                grounding = assess_grounding(docs_scores)
                yield _sse("grounding", grounding)
                if grounding.get("weak"):
                    yield _sse("status", {"text": "检索相似度偏低，已提醒模型不要硬答"})
                else:
                    yield _sse("status", {"text": f"检索到 {len(docs_scores)} 条片段"})
            else:
                yield _sse("status", {"text": "知识库未命中"})

            yield _sse("status", {"text": "正在生成答案..."})
            # 复用上面已经检索到的结果，不重复检索（早期实现在这里又检了一次，
            # 而且用的是默认 top_k，等于前端调的检索条数被悄悄忽略）
            messages = await _build_messages(uid, req.sid, req.question, kb_id, docs_scores, grounding, req.scenario, history)

            acc = _Acc()
            try:
                async for frame in _stream_llm(get_llm(max_tokens=2048), messages, request, acc):
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
            history = (await asyncio.to_thread(store.get_messages, uid, req.sid, include_system=False))[:-1]
            messages = [SystemMessage(content="你是一个友好、专业的 AI 助手。用中文简洁作答。" + scenarios_mod.get_scenario_prompt(req.scenario))]
            for m in history:
                if m["role"] == "user":
                    messages.append(HumanMessage(content=m["content"]))
                elif m["role"] == "assistant":
                    messages.append(AIMessage(content=m["content"]))
            messages.append(HumanMessage(content=req.question))

            acc = _Acc()
            try:
                async for frame in _stream_llm(get_llm(max_tokens=2048), messages, request, acc):
                    yield frame
            except Exception as e:
                yield _sse("error", {"detail": describe_api_error(e)})
                return
            if acc.error:
                return
            acc_text = acc.text

        # ---- 收尾：存 assistant 消息 + 取回并行生成的标题 ----
        msg_id = await asyncio.to_thread(store.add_message, uid, req.sid, "assistant", acc_text, refs=refs_md)

        gen_title = await _take_title(title_task, acc_text)
        if gen_title:
            await asyncio.to_thread(store.rename_conversation, uid, req.sid, gen_title)
            yield _sse("title", {"title": gen_title})

        # message_id 回给前端：赞踩要按消息 id 落库，前端自己生成的 uid 后端不认
        yield _sse("done", {
            "answer": acc_text,
            "refs": refs_md,
            "title": gen_title,
            "mode": mode,
            "message_id": msg_id,
        })

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=SSE_HEADERS)


# ==================== 辅助 ====================

async def _build_messages(
    owner: str, sid: str, question: str, kb_id: str | None, docs_scores,
    grounding: dict | None = None, scenario: str | None = None, history=None
) -> list:
    """组装 LLM 输入：历史 + 已检索到的参考资料。

    docs_scores 由调用方检索一次后传入，这里不再重复检索，
    避免用户设置的 top_k 被默认值覆盖、也省掉一次 embedding 请求。

    grounding 是调用方算好的依据强度判定。weak=true 时追加一段"别硬答"的约束——
    系统提示词里的通用规则太软，模型看到 prompt 里真有几段资料，
    哪怕相似度只有 0.3 也会顺着往下编；单独再压一遍才压得住。
    history 同理：调用方（RAG 路径做查询改写时）已取过就直接复用，不再重复查库。
    """
    history_raw = (
        history if history is not None
        else (await asyncio.to_thread(store.get_messages, owner, sid, include_system=False))[:-1]
    )
    messages = []
    references = ""
    if kb_id and docs_scores:
        references = "\n\n".join(
            f"[{i + 1}] （来源：{d.metadata.get('source', '?')}）\n{d.page_content}"
            for i, (d, _) in enumerate(docs_scores)
        )
    if references:
        system = SYSTEM_PROMPT + grounding_note(grounding or {}) + scenarios_mod.get_scenario_prompt(scenario)
    else:
        system = "你是一个友好、专业的 AI 助手。用中文简洁作答。" + scenarios_mod.get_scenario_prompt(scenario)
    messages.append(SystemMessage(content=system))
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


def _generate_title(question: str) -> str:
    """用用户问题生成一个 ≤15 字的会话标题。

    只看问题、不看答案：提示词里本来就没用到答案。去掉答案参数后，标题生成
    就能和答案流式生成**并行**跑（见 chat_stream 里的 title_task），
    收尾时基本零等待，不必再在 done 之前干等一次 LLM 往返。

    两道成本护栏（2026-10-13）：
      - 输入截到前 200 字：首问若是粘贴的长文档，完整问题会原样进标题 prompt，
        一次标题生成就吃掉几千 token 输入，而标题只反映开头主题，截断不损质量；
      - max_tokens=32：标题只要 ≤15 字，给输出兜底，防止模型跑偏成复述正文。
    """
    llm = get_llm(temperature=0, max_tokens=32)
    try:
        q = (question or "")[:200]
        resp = llm.invoke([HumanMessage(content=f"请为以下对话生成简短中文标题（≤15字），直接输出：用户提问：{q}")])
        title = (resp.content or "").strip().strip('"').strip("'")
        if len(title) > 20:
            title = title[:15] + "..."
        return title or question[:15]
    except Exception:
        return question[:15] if len(question) > 15 else question


async def _take_title(task: "asyncio.Task | None", acc_text: str) -> str:
    """取回并行生成的标题；无任务 / 无答案 / 失败都返回空串。"""
    if task is None or not acc_text:
        return ""
    try:
        return await task
    except Exception:
        return ""


# 后台任务强引用：任务完成前被 GC 会报 "Task was destroyed but it is pending"。
# 完成后自动移除，避免集合无限增长。
_BG_TASKS: set = set()
