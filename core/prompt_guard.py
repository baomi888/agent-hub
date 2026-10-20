"""变异护栏（GEPA 的第 ② 层「结构性校验」，不信任模型自觉）。

为什么护栏必须落在结构上
------------------------
提示词自进化最危险的失败不是"改得没用"，而是"改得越权"：
一段变异只要把 `kb_id` 写回提示词，或者删掉「工具报错不得编造」这条，
模型就会重新获得一个本被服务端注入口堵死的越权入口
（`agent/builder.py` 里 kb_id 不进提示词的原始设计）。
这类变异**在评测集上很可能得分更高**（更"敢答"），所以不能指望指标把它拦下来，
只能在这里按行结构硬拒。

四道闸门（全部不通过直接丢该候选）
--------------------------------
1. **黑名单**：基线里出现过的安全条目，候选里必须原样还在（删/改一律拒）。
2. **越权词**：新增文本不许出现 `kb_id` / 「忽略上文」这类注入话术。
3. **改动面**：add+remove+modify 的总行数 ≤ `PROMPT_EVO_MAX_DIFF_LINES`
   （KV 前缀稳定 + 回归面可控，方案 §2 的 tie-break 也依赖这个计数）。
4. **整体塌缩**：候选长度不得低于基线的 50%，且必须保留该 path 的必备语气词
   （示弱模板少了「必须遵守 / 宁可」就不再是示弱模板）。
"""

from __future__ import annotations

import json
import re
from typing import Any

from core import config

ALLOWED_ACTIONS = ("add", "modify", "remove")

# 安全条目：出现在基线里就必须在候选里原样存在。按 path 分开维护——
# rag_note 的模板没有工具定义，共用一份黑名单会误伤。
BLACKLIST: dict[str, tuple[str, ...]] = {
    "agent": (
        "你是一个智能问答助手",
        "工具返回错误时如实向用户解释，绝不编造",
        "不要用相同参数重复调用该工具",
        "kb_id",
        "【当前会话已绑定知识库】",
        "【当前会话没有绑定知识库】",
        "  - 从知识库中检索资料",
    ),
    "rag_note_low": ("不要编",),
    "rag_note_medium": ("不要编",),
}

# 各 path 的必备语气词：示弱模板被改到没有示弱语义，等于把 M2 指标做废。
REQUIRED_TOKENS: dict[str, tuple[str, ...]] = {
    "rag_note_low": ("必须遵守", "宁可"),
    "rag_note_medium": ("必须遵守", "宁可"),
}

# add 的插入锚点必须在这行**之后**：系统角色首句 + 工具定义属于前缀地基，
# 不许往里伸手。agent path 用它；rag_note 模板没有分节，留空表示不限制。
ANCHOR_FLOOR: dict[str, str] = {"agent": "行为准则："}

# 新增文本里出现这些词一律拒（越权 / 提示词注入话术）。
FORBIDDEN_IN_NEW = (
    "kb_id",
    "忽略上文",
    "忽略以上",
    "system prompt",
    "系统提示词改为",
)

MIN_LEN_RATIO = 0.5


class GuardError(ValueError):
    """候选 diff 越界。message 直接进日志与接口报错，要写清哪一条。"""


def max_lines() -> int:
    try:
        return max(1, int(getattr(config, "PROMPT_EVO_MAX_DIFF_LINES", 6) or 6))
    except (TypeError, ValueError):
        return 6


def _count_lines(text: str) -> int:
    return len([ln for ln in (text or "").splitlines() if ln.strip()])


def _occurrences(haystack: str, needle: str) -> int:
    if not needle:
        return 0
    return haystack.count(needle)


def _line_index(lines: list[str], needle: str) -> int:
    for i, ln in enumerate(lines):
        if needle in ln:
            return i
    return -1


def _check_new_text(path: str, text: str) -> None:
    low = (text or "").lower()
    for bad in FORBIDDEN_IN_NEW:
        if bad.lower() in low:
            raise GuardError(f"新增文本含越权/注入词「{bad}」")
    for bad in BLACKLIST.get(path, ()):
        if bad.lower() in low:
            raise GuardError(f"新增文本不得复述黑名单条目「{bad}」")


def apply_diff(
    path: str,
    base_text: str,
    ops: list[dict],
    *,
    limit_lines: int | None = None,
) -> dict:
    """校验并落地一组结构化 diff。

    ops 每条形如::

        {"action": "add",    "anchor": "  9. 闲聊…", "new_text": "  10. …", "rationale": "…"}
        {"action": "modify", "old_text": "原文一行", "new_text": "改写后", "rationale": "…"}
        {"action": "remove", "old_text": "原文一行", "rationale": "…"}

    返回 ``{"text": 新提示词, "changed_lines": int, "ops": [...]}``。
    任何越界抛 :class:`GuardError`。
    """
    if not isinstance(ops, list) or not ops:
        raise GuardError("diff 为空")
    base = base_text or ""
    limit = max_lines() if limit_lines is None else max(1, int(limit_lines))

    base_lines = base.splitlines()
    changed = 0
    text = base
    applied: list[dict] = []

    # 先做"改动面"体检：改多了连逐条校验的必要都没有，直接拒
    for op in ops:
        if not isinstance(op, dict):
            raise GuardError("diff 条目必须是对象")
        action = str(op.get("action") or "").strip()
        if action not in ALLOWED_ACTIONS:
            raise GuardError(f"不支持的 action: {action}")
        changed += _count_lines(op.get("new_text", ""))
        if action in ("modify", "remove"):
            changed += _count_lines(op.get("old_text", ""))
    if changed > limit:
        raise GuardError(f"改动行数 {changed} 超过上限 {limit}（KV 前缀与回归面约束）")

    for op in ops:
        action = op["action"]
        if action == "add":
            anchor = str(op.get("anchor") or "").strip()
            new_text = str(op.get("new_text") or "")
            if not anchor:
                raise GuardError("add 缺少 anchor")
            if _occurrences(text, anchor) != 1:
                raise GuardError(f"anchor 不唯一或不存在：{anchor}")
            floor = ANCHOR_FLOOR.get(path)
            if floor:
                idx_anchor = _line_index(text.splitlines(), anchor)
                idx_floor = _line_index(text.splitlines(), floor)
                if idx_floor < 0 or idx_anchor < idx_floor:
                    raise GuardError(f"add 只能落在「{floor}」之后的区域（角色首句/工具定义不可动）")
            _check_new_text(path, new_text)
            # 插在锚点**所在整行**之后：锚点是行内片段，直接插在片段后面
            # 会把原文从中间劈开（"9. 闲聊…" 变成 "9. 闲聊…  10. 新增…"）。
            start = text.index(anchor)
            line_end = text.find("\n", start)
            if line_end < 0:
                line_end = len(text)
            text = text[:line_end] + "\n" + new_text.rstrip("\n") + text[line_end:]
            applied.append(op)
            continue

        old_text = str(op.get("old_text") or "")
        new_text = str(op.get("new_text") or "")
        if not old_text.strip():
            raise GuardError(f"{action} 缺少 old_text")
        if action == "modify" and not new_text.strip():
            raise GuardError("modify 的 new_text 不能为空（要删就用 remove）")
        if _occurrences(text, old_text) != 1:
            raise GuardError(f"old_text 不唯一或不存在：{old_text[:40]}")
        for bad in BLACKLIST.get(path, ()):
            if bad in old_text:
                raise GuardError(f"触碰黑名单条目「{bad}」（安全约束不可删改）")
        if action == "modify":
            _check_new_text(path, new_text)
            text = text.replace(old_text, new_text, 1)
        else:
            text = text.replace(old_text, "", 1)
        applied.append(op)

    # ---- 事后体检：黑名单原样在、必备语气词在、长度没塌 ----
    for bad in BLACKLIST.get(path, ()):
        if bad in base and bad not in text:
            raise GuardError(f"黑名单条目被间接删掉「{bad}」")
    for tok in REQUIRED_TOKENS.get(path, ()):
        if tok in base and tok not in text:
            raise GuardError(f"缺少必备语气词「{tok}」（示弱模板不可弱化成正常作答）")
    if len(text.strip()) < len(base.strip()) * MIN_LEN_RATIO:
        raise GuardError("候选长度不足基线的 50%（整体塌缩）")
    if text.strip() == base.strip():
        raise GuardError("候选与基线相同（空改动）")
    # 收敛空白行：连续 3 个以上空行压成 1 个（add 操作容易留空洞）
    text = re.sub(r"\n{3,}", "\n\n", text)

    return {"text": text, "changed_lines": changed, "ops": applied}


def parse_llm_output(raw: str) -> list[dict]:
    """把模型输出解析成 ops 列表。解析失败抛 GuardError。

    只接受 JSON（可带 ```json 围栏）。**不做"猜意图"的宽松解析**：
    变异本身是低频动作，解析失败重试一轮的成本远低于误解析出越界 diff 的风险。
    """
    s = (raw or "").strip()
    if not s:
        raise GuardError("模型输出为空")
    fence = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)
    if fence:
        s = fence.group(1).strip()
    try:
        data = json.loads(s)
    except ValueError:
        # 兜底：截取第一个 { 到最后一个 }（模型常在前后加解释）
        i, j = s.find("{"), s.rfind("}")
        if i < 0 or j <= i:
            i, j = s.find("["), s.rfind("]")
        if i < 0 or j <= i:
            raise GuardError("模型输出不是 JSON") from None
        try:
            data = json.loads(s[i : j + 1])
        except ValueError:
            raise GuardError("模型输出不是合法 JSON") from None

    if isinstance(data, dict):
        for key in ("ops", "mutations", "diffs", "changes"):
            if isinstance(data.get(key), list):
                return [d for d in data[key] if isinstance(d, dict)]
        if "action" in data:
            return [data]
        raise GuardError("JSON 里没有 ops/mutations 数组")
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    raise GuardError("模型输出的 JSON 顶层类型不支持")


def summarize_ops(ops: list[dict]) -> str:
    """一行人类可读摘要（日志 / 接口用）。"""
    parts = []
    for op in ops or []:
        act = op.get("action", "?")
        target = (op.get("anchor") or op.get("old_text") or "")[:24]
        parts.append(f"{act}:{target}")
    return "; ".join(parts)


def guard_ok(path: str, base_text: str, ops: list[dict]) -> tuple[bool, Any]:
    """便捷封装：返回 (是否通过, 结果或错误文案)。不做异常控制流。"""
    try:
        return True, apply_diff(path, base_text, ops)
    except GuardError as e:
        return False, str(e)
