"""SSE 帧的唯一编码处：事件 → 线上字节。

为什么单独一个模块
------------------
分支逻辑只该产出「发生了什么」（token / 引用 / 依据 / 工具调用），
不该知道帧叫什么名字、payload 里字段叫 ``items`` 还是 ``detail``。
改造前这些散在 ``api/chat.py`` 的 59 处 ``_sse(...)`` 里：换协议（比如改 WebSocket）
要改 59 个地方，漏一个不会报错——前端只是少渲染一块，而且没人会注意到。

现在帧名与字段形状只出现在本模块，调用方写 ``yield sse.status("正在检索…")``。
想换编码（WebSocket / JSON Lines）只改 ``_encode`` 一处。

帧格式与前端 ``frontend/src/lib/sse.ts`` 的解析器对齐：
``event: <name>\\ndata: <json>\\n\\n``（``ensure_ascii=False``，中文不转义）。
"""

from __future__ import annotations

import json

# 截断提示的固定文案：只说"被截断"用户不知道该怎么办，所以带一句可操作的话。
TRUNCATION_HINT = "回答达到长度上限被截断，内容可能不完整。可以回复「继续」让它接着写。"


def _encode(event: str, data: dict) -> str:
    """唯一的编码实现。换传输协议只需要改这里。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ---------------- 通用帧 ----------------


def status(text: str) -> str:
    """状态条（覆盖式：连发两条用户只看得到最后一条）。"""
    return _encode("status", {"text": text})


def token(text: str) -> str:
    """增量正文。只发 delta，前端自己累加。"""
    return _encode("token", {"text": text})


def error(detail: str) -> str:
    """可展示的错误文案（已由 describe_api_error 归一化）。"""
    return _encode("error", {"detail": detail})


def title(text: str) -> str:
    """会话标题（仅首轮生成时发）。"""
    return _encode("title", {"title": text})


def truncation(reason: str | None) -> str:
    """截断提示帧。reason 取上游的 finish_reason。"""
    return _encode("truncation", {"reason": reason or "length", "hint": TRUNCATION_HINT})


# ---------------- 检索相关 ----------------


def sources(items: list) -> str:
    """引用溯源条目（每条带 strength，前端据此标「强/中/弱」）。"""
    return _encode("sources", {"items": items})


def grounding(data: dict) -> str:
    """依据强度。字段由 kb.rag_core.assess_grounding 决定，这里不做翻译。

    ⚠️ 必须赶在第一个 token 之前发出：前端要在回答上方挂「依据较弱」提示条，
    等 done 再发就晚了（用户是边流边读的）。
    """
    return _encode("grounding", data)


# ---------------- 执行轨迹（agent / hybrid）----------------


def tool(payload: dict) -> str:
    """工具调用或返回（name + input/output 二选一）。"""
    return _encode("tool", payload)


def approval(payload: dict) -> str:
    """高风险工具待确认卡片。token 只能由 /api/chat/approval 签发，不在这里。"""
    return _encode("approval", payload)


def workflow_node(payload: dict) -> str:
    """工作流节点进出（时间轴）。output_key 已由调用方剔除。"""
    return _encode("workflow_node", payload)


def mode_switch(decision: dict) -> str:
    """引擎切换：规则名 + 理由 + from/to。"""
    return _encode("mode_switch", decision)


def hybrid_start(payload: dict) -> str:
    """混合执行起跑（带 run_id，人工切换按它寻址）。"""
    return _encode("hybrid_start", payload)


# ---------------- 收尾 ----------------


def done(payload: dict) -> str:
    """终帧。字段由调用方决定（各分支的 done 字段集不同是已知问题，

    统一收尾后应只保留一份；在那之前这里不做 schema 校验，避免误伤。
    """
    return _encode("done", payload)
