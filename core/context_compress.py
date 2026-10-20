"""上下文压缩（P0-2）：压缩块落库 + 最近 K 轮原文，替代滑动窗口。

为什么做
--------
原来用 `MAX_HISTORY_TURNS=10` 的滑动窗口：**每轮窗口都在滑，system 之后的第一个
消息每轮都变，KV 缓存前缀永远不命中**；同时早期轮次被整段丢弃（"失忆"）。
换成"压缩块（稳定前缀）+ 最近 K 轮原文（滚动尾巴）"后，压缩事件之间前缀**不变**，
缓存才真正能命中。

方案骨架来自 CORE-RAG 的"性能驱动压缩"，但**压缩对象不同**
（它压检索文档，这里压对话历史），所以只迁移四条原则：
  性能即奖励 / 宁可不压（安全阀）/ 时机在两轮之间且批量压 / 硬规则兜底。

本文件按评审意见实现的三个关键点（原方案会裸奔的地方）
------------------------------------------------------
1. **L0 分级安全阀，零 LLM 成本，冷启动即可用**
   原方案的安全阀依赖 judged 质量，而评估集建好之前没有质量信号 → 安全阀是真空的。
   更糟的是"置 NULL 回退全量"在上下文本来就接近 token 硬顶时会死锁。
   所以改成四级（见 `apply_safety_valve`）：
     L0 硬规则校验：压缩块丢失实体（URL/ID/待办/目标）→ 拒收
     L1 回退上一版压缩块：有 prev 就用 prev，绝不回到全量
     L2 扩大 keep_turns：没有 prev 时把 K 从 6 提到 12，压缩块只吃更老的部分
     L3 硬截断：仍超硬顶则按 token 预算从最旧原文截断，压缩块保留
2. **压缩块自身有预算**，否则它会变成新的膨胀源
   超 `MAX_SUMMARY_CHARS` 时做**二级压缩**：把最早的段折叠成一句概述。
3. **增量分段**：压缩块内部按 `[COMPRESSED-n]` 分段，新压缩事件**只追加不改旧段**，
   这样前缀在压缩事件时只增长、不重写（评审指出"低频还不够，增量才真正稳前缀"）。

并发：`compressed_upto` 的写入用乐观锁（见 `commit_summary`），
失败就放弃本轮压缩、下次再试——不会出现"压缩块 + 原文重复进上下文"。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from core import config

# ==================== 配置（都有默认值，可被 config 覆盖）====================

MAX_SUMMARY_CHARS = int(getattr(config, "COMPRESS_MAX_SUMMARY_CHARS", 4000))
# 触发压缩的阈值（占 MAX_CONTEXT_CHARS 的比例）
TRIGGER_RATIO = float(getattr(config, "COMPRESS_TRIGGER_RATIO", 0.8))
# 默认保留最近 K 轮原文
KEEP_TURNS = int(getattr(config, "COMPRESS_KEEP_TURNS", 6))
# L2 扩容后的 K
KEEP_TURNS_EXPANDED = int(getattr(config, "COMPRESS_KEEP_TURNS_EXPANDED", 12))
# 压缩块最大段数，超过就折叠最早的段
MAX_SEGMENTS = int(getattr(config, "COMPRESS_MAX_SEGMENTS", 12))

SEG_PREFIX = "[COMPRESSED-"


# ==================== 实体抽取（L0 的原料，零 LLM）====================

# URL / UUID / 长 hex / 文件名 / 待办关键词
_RE_URL = re.compile(r"https?://[^\s<>\"')]+", re.I)
_RE_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_RE_HEX = re.compile(r"\b[0-9a-f]{16,}\b", re.I)
_RE_FILE = re.compile(
    r"\b[\w\-]+\.(?:pdf|docx?|xlsx?|pptx?|txt|md|csv|json|png|jpe?g|zip|py|ts|js)\b", re.I
)
# 待办/目标类关键词（中文为主，覆盖"结论/约束/必须/截止"等硬信息）
_RE_TODO = re.compile(
    r"(待办|TODO|待完成|需要做|下一步|截止|deadline|必须|不要|注意|目标|计划)", re.I
)


def extract_entities(text: str) -> set[str]:
    """抽取"压缩时绝不能丢"的硬实体。纯正则，零 LLM 成本。

    L0 校验用：压缩块必须保留这些实体，否则拒收。
    抽取范围刻意保守（只取高置信度的 URL/ID/文件名/待办关键词），
    宁可漏检也不要误报——误报会让压缩几乎总被拒。
    """
    if not text:
        return set()
    found: set[str] = set()
    for rx in (_RE_URL, _RE_UUID, _RE_HEX, _RE_FILE):
        found.update(m.group(0) for m in rx.finditer(text))
    found.update(m.group(0) for m in _RE_TODO.finditer(text))
    return found


def missing_entities(source_text: str, summary_text: str) -> list[str]:
    """压缩块里缺了哪些硬实体（L0 的判据）。"""
    need = extract_entities(source_text)
    if not need:
        return []
    have = summary_text or ""
    return sorted(e for e in need if e not in have)


# ==================== 压缩块的结构 ====================


@dataclass
class SummaryBlock:
    """压缩块：分段存储，增量追加不改旧段。"""

    segments: list[str] = field(default_factory=list)

    # ---- 序列化 ----
    def render(self) -> str:
        """渲染成落库文本：每段一行 `[COMPRESSED-n] ...`。"""
        return "\n".join(f"{SEG_PREFIX}{i + 1}] {seg}" for i, seg in enumerate(self.segments))

    @classmethod
    def parse(cls, text: str | None) -> SummaryBlock:
        """从落库文本还原。解析失败时退回"整块一段"，不丢内容。"""
        if not text:
            return cls([])
        segs: list[str] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            m = re.match(r"^\[COMPRESSED-\d+\]\s*(.*)$", line)
            if m:
                segs.append(m.group(1).strip())
            elif segs:
                # 上一条的续行（压缩块里通常不会有，稳妥处理）
                segs[-1] = (segs[-1] + " " + line).strip()
            else:
                segs.append(line)
        return cls(segs)

    # ---- 预算管理 ----
    @property
    def chars(self) -> int:
        return len(self.render())

    def append(self, segment: str) -> str:
        """追加一段（**不改旧段**）。超预算或超段数时折叠最早的段。"""
        segment = (segment or "").strip()
        if segment:
            self.segments.append(segment)
        self._enforce_budget()
        return self.render()

    def _enforce_budget(self) -> None:
        """压缩块自身的预算：段数超限 / 字符超限时折叠最早的部分。

        折叠而不是直接丢弃：保留"更早的对话涉及 X/Y/Z 主题"这一类概述，
        否则模型会以为更早的内容从未发生过（评审提到的"膨胀源"问题）。
        """
        # 段数超限：把最早的几段折成一段
        if len(self.segments) > MAX_SEGMENTS:
            fold_n = len(self.segments) - MAX_SEGMENTS + 1
            folded = " ".join(self.segments[:fold_n])
            overview = f"[早期内容已折叠] {folded[:300]}"
            self.segments = [overview] + self.segments[fold_n:]
        # 字符超限：从头折叠直到进预算
        guard = 0
        while self.chars > MAX_SUMMARY_CHARS and len(self.segments) > 1 and guard < 50:
            guard += 1
            first = self.segments[0]
            if first.startswith("[早期内容已折叠]"):
                # 已经折叠过：继续压缩这一段本身
                self.segments[0] = "[早期内容已折叠] " + first[:200]
                if len(self.segments) == 1:
                    break
                self.segments.pop(1)
            else:
                self.segments[0] = f"[早期内容已折叠] {first[:200]}"
                self.segments.pop(1)


# ==================== 安全阀（分级，替代二值回退）====================

# 动作名
ACT_ACCEPT = "accept"  # 接受新压缩块
ACT_REJECT_KEEP_OLD = "keep_old"  # L0 失败且没有 prev：保留旧块（或继续用原文）
ACT_ROLLBACK = "rollback"  # L1：回退到上一版压缩块
ACT_EXPAND_KEEP = "expand_keep"  # L2：扩大 keep_turns
ACT_TRUNCATE = "truncate"  # L3：硬截断原文


@dataclass
class ValveDecision:
    action: str
    reason: str
    keep_turns: int


def apply_safety_valve(
    source_text: str,
    new_summary: str,
    prev_summary: str | None,
    *,
    est_tokens_after: int | None = None,
    hard_limit_tokens: int | None = None,
    base_keep_turns: int = KEEP_TURNS,
) -> ValveDecision:
    """分级安全阀。**不调用任何 LLM**，所以冷启动阶段就能生效。

    顺序就是优先级：L0 → L1 → L2 → L3。返回一个决策，由调用方执行。
    """
    # ---------- L0：硬规则校验（实体保留）----------
    lost = missing_entities(source_text, new_summary)
    if lost:
        if prev_summary:
            return ValveDecision(
                ACT_ROLLBACK,
                f"L0 实体丢失（{', '.join(lost[:3])}）→ 回退上一版压缩块",
                base_keep_turns,
            )
        return ValveDecision(
            ACT_EXPAND_KEEP,
            f"L0 实体丢失（{', '.join(lost[:3])}）且无上一版 → 扩大 keep_turns 重压",
            KEEP_TURNS_EXPANDED,
        )

    # ---------- L3 预判：token 仍超硬顶 ----------
    if (
        est_tokens_after is not None
        and hard_limit_tokens is not None
        and est_tokens_after > hard_limit_tokens
    ):
        # 有 prev 也救不了 token（prev 可能更长），直接硬截断
        return ValveDecision(
            ACT_TRUNCATE,
            f"压缩后仍超硬顶（{est_tokens_after} > {hard_limit_tokens}）→ 硬截断最旧原文",
            base_keep_turns,
        )

    return ValveDecision(ACT_ACCEPT, "通过 L0 校验且未超硬顶", base_keep_turns)


def commit_summary(
    owner: str,
    sid: str,
    new_summary: str,
    upto_message_id: int,
    expected_upto: int | None,
) -> bool:
    """乐观锁写入压缩块。返回 False 表示并发冲突（放弃本轮压缩）。

    为什么用乐观锁：压缩是回答结束后的异步动作，下一轮请求可能先到。
    如果两边都认为自己该写 `compressed_upto`，就会出现
    "压缩块 + 被压缩的原文同时进上下文"的重复（评审指出的竞态）。
    以 `compressed_upto` 做 CAS：不匹配就放弃，下次压缩再试。
    """
    from core import session as store

    with store._get_conn() as conn:  # noqa: SLF001 —— 同模块内的既定用法
        cur = conn.execute(
            "UPDATE conversations SET compressed_summary = ?, compressed_upto = ?, "
            "compressed_summary_prev = COALESCE(compressed_summary, compressed_summary_prev) "
            "WHERE id = ? AND owner_id = ? AND COALESCE(compressed_upto, 0) = COALESCE(?, 0)",
            (new_summary, upto_message_id, sid, owner, expected_upto),
        )
        return cur.rowcount > 0


def load_summary(owner: str, sid: str) -> tuple[SummaryBlock, int]:
    """读取当前压缩块与 `compressed_upto`。"""
    block, upto, _prev = load_summary_state(owner, sid)
    return block, upto


def load_summary_state(owner: str, sid: str) -> tuple[SummaryBlock, int, str | None]:
    """读取 (当前压缩块, compressed_upto, 上一版压缩块)。

    多返回一个 prev，是因为 L1 安全阀要拿它来回退；`load_summary` 保持二元签名
    不变（调用方只关心"现在是什么"），需要回退依据的写入侧才用这个。
    """
    from core import session as store

    with store._get_conn() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT compressed_summary, compressed_summary_prev, compressed_upto "
            "FROM conversations WHERE id = ? AND owner_id = ?",
            (sid, owner),
        ).fetchone()
    if not row:
        return SummaryBlock([]), 0, None
    return (
        SummaryBlock.parse(row["compressed_summary"]),
        int(row["compressed_upto"] or 0),
        row["compressed_summary_prev"],
    )


# ==================== 冷启动：抽取式压缩（零 LLM）====================


def extractive_compress(text: str, max_chars: int | None = None) -> str:
    """冷启动用的抽取式压缩：规则提句，不调模型。

    规则（刻意保守，评审说"中文规则提取准确率一般，冷启动没安全阀"）：
      - 保留含硬实体的句子（URL/ID/文件名/待办关键词）——这些绝不能丢
      - 保留首句与末句（首句通常是问题/意图，末句通常是结论）
      - 其余按长度预算截断
    """
    limit = max_chars or MAX_SUMMARY_CHARS // 2
    if not text:
        return ""
    if len(text) <= limit:
        return text
    # 按中英文句末切句
    sents = [s.strip() for s in re.split(r"(?<=[。！？!?；;])\s*|\n+", text) if s.strip()]
    if not sents:
        return text[:limit]
    keep: list[str] = []
    if sents:
        keep.append(sents[0])
    for s in sents:
        if extract_entities(s) and s not in keep:
            keep.append(s)
    if sents and sents[-1] not in keep:
        keep.append(sents[-1])
    out = ""
    for s in keep:
        if len(out) + len(s) + 1 > limit:
            break
        out = (out + " " + s).strip()
    return out or text[:limit]


def build_compressed_segment(
    old_messages: list[dict],
    compressor: Callable[[str], str] | None = None,
) -> tuple[str, str]:
    """把一批旧轮次压成**一段**（返回 (段文本, 原始文本)）。

    compressor 可注入：默认走抽取式（零 LLM，冷启动安全）；
    配置了 LLM 摘要时可注入 LLM 版本。这样压缩策略与执行时机解耦。
    """
    raw = "\n".join(f"{m.get('role')}: {m.get('content', '')}" for m in old_messages)
    seg = (compressor or extractive_compress)(raw)
    return seg, raw


# ==================== 组装上下文（压缩块 + 最近 K 轮）====================


@dataclass
class ContextPlan:
    """一次组装的结论：给调用方的"怎么拼上下文"说明。"""

    summary_text: str
    keep_message_id_from: int  # 只保留 id > 该值的原文
    keep_turns: int
    decision: ValveDecision | None = None
    truncated_from_id: int | None = None
    # 过滤后真正要喂给模型的原文（id > compressed_upto，且只留最近 keep_turns 轮）。
    # 放在这里而不是让调用方自己再过滤一次：过滤规则（以 compressed_upto 为准）
    # 只能有一处实现，否则迟早两边算得不一样。
    kept: list[dict] = field(default_factory=list)


def plan_context(
    owner: str,
    sid: str,
    messages: list[dict],
    *,
    keep_turns: int = KEEP_TURNS,
) -> ContextPlan:
    """规划上下文：压缩块 + 最近 K 轮原文。

    **以 `compressed_upto` 为准过滤原文**（评审建议）：
    只取 `id > compressed_upto` 的消息，这样即使压缩还没完成、
    也不会把"本该被压缩的旧轮次"和压缩块同时塞进上下文。
    """
    summary, upto = load_summary(owner, sid)
    kept = [m for m in messages if int(m.get("id") or 0) > upto]
    # 最近 keep_turns 轮：一轮 = user + assistant
    if keep_turns > 0:
        kept = kept[-(keep_turns * 2) :]
    first_id = int(kept[0]["id"]) if kept else 0
    return ContextPlan(
        summary_text=summary.render(),
        keep_message_id_from=first_id,
        keep_turns=keep_turns,
        kept=kept,
    )


# ==================== 估算与触发 ====================


def estimate_tokens(text: str) -> int:
    """粗估 token：中文约 1 字 1 token，英文约 4 字符 1 token。

    只在**触发压缩**这种"差一点无所谓"的场景用；不追求精确。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return cjk + max(1, other // 4)


def should_compress(total_chars: int, max_context_chars: int | None = None) -> bool:
    """是否该触发压缩：超过阈值比例才压（批量低频，不每轮压）。

    `total_chars` 必须是**全量历史**的字符数，不是窗口内——
    窗口内字符数会随 `MAX_HISTORY_TURNS` 封顶而恒定，永远到不了阈值，
    压缩就永远不会触发（接线时踩过这个坑）。
    """
    limit = max_context_chars or int(
        getattr(config, "COMPRESS_MAX_CONTEXT_CHARS", None)
        or getattr(config, "MAX_CONTEXT_CHARS", 12000)
    )
    return total_chars > limit * TRIGGER_RATIO


# ==================== 接入请求路径（读写两侧）====================
#
# 此前 `context_compress` 只有一个"库"，没人调用：`plan_context` 写好了却没接进
# `api/chat.py`，也没有任何地方**写**压缩块。这一段就是补上这两头。
#
# 三个必须说清的设计决定：
#
# 1. **压缩块放在历史最前面，不塞 system prompt**
#    Agent 路径的 system prompt 在 `build_agent` 里随图编译固化，且图按
#    (has_kb, 场景, 激活集) 做 LRU 缓存；把压缩块塞进 system 会让缓存键多出一个
#    随会话变化的维度 —— 每个会话都编译一张新图，缓存直接失效。
#    而压缩块本身是**稳定前缀**（只在压缩事件时增长），放在历史最前面同样不破 KV 缓存。
#
# 2. **用一对 human→ai 包住，而不是裸插一条 human**
#    部分 chat template 对"连续两条 human"敏感（P0-1 踩过）；而且必须显式写明
#    "这不是用户的新提问"，否则模型会把摘要内容当成本轮诉求去执行。
#
# 3. **没有压缩块时，原样返回全量历史**
#    这是最容易写错的地方：`plan_context` 无条件按最近 K 轮截断，
#    若在"还没压缩过"的短会话上也套 K 轮窗口，等于把 30 轮窗口悄悄改成 6 轮 ——
#    功能倒退。所以下面先判 `summary_text` 为空就整段直通。

# 压缩块前置说明（写死在代码里，属于行为契约，不做成配置项）
PRELUDE_HEAD = (
    "【早期对话压缩摘要】以下是本会话**更早**内容的系统压缩摘要，不是用户的新提问，"
    "也不是待办指令。摘要可能丢失细节：需要细节时如实说明或向用户确认，不要编造。\n\n"
)
PRELUDE_ACK = "已了解早期对话的压缩摘要，后续回答会参考它。"


def _clean(msgs: list[dict]) -> list[dict]:
    """只留 user / assistant 且内容非空，转成 {role, content}。"""
    return [
        {"role": m["role"], "content": m["content"]}
        for m in msgs
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]


def assemble_history(
    owner: str,
    sid: str,
    raw_messages: list[dict],
    *,
    keep_turns: int | None = None,
) -> list[dict]:
    """把会话原文组装成"喂给模型的历史"：压缩块前置 + 最近 K 轮原文。

    所有模式（vision / hybrid / agent / rag / llm）都走这一个函数，
    避免五处各写一份、日后改规则漏掉其中一处。

    返回 `[{role, content}, ...]`，调用方自己转成 HumanMessage/AIMessage 或 dict。
    压缩层任何异常都**降级为原样返回**：压缩是优化，不能成为对话失败的原因。
    """
    if not getattr(config, "COMPRESS_ENABLED", True):
        return _clean(raw_messages)
    try:
        plan = plan_context(
            owner, sid, raw_messages, keep_turns=KEEP_TURNS if keep_turns is None else keep_turns
        )
    except Exception:  # noqa: BLE001 —— 压缩失败不许影响对话
        return _clean(raw_messages)
    if not plan.summary_text:
        # 还没压缩过：原样直通（绝不能在这里套 K 轮窗口，见上面第 3 条）
        return _clean(raw_messages)
    out = [
        {"role": "user", "content": PRELUDE_HEAD + plan.summary_text},
        {"role": "assistant", "content": PRELUDE_ACK},
    ]
    out.extend(_clean(plan.kept))
    return out


def maybe_compress(
    owner: str,
    sid: str,
    *,
    keep_turns: int | None = None,
    compressor: Callable[[str], str] | None = None,
    force: bool = False,
    max_context_chars: int | None = None,
) -> dict:
    """回答结束后调用：够长就把"最近 K 轮之前"的原文压成一段落库。

    为什么是"回答结束后"：压缩要在两轮之间做（书第 2 章），
    回答过程中压会让本轮上下文形状中途变化；而且用户已经看到答案了，
    这一轮压不压不影响体验。

    返回 `{"ok", "reason", "upto", "action"}`，调用方只记日志，不据此做业务判断。
    """
    from core import session as store

    if not getattr(config, "COMPRESS_ENABLED", True):
        return {"ok": False, "reason": "disabled", "upto": 0}
    kt = KEEP_TURNS if keep_turns is None else keep_turns

    try:
        # limit_turns=None：阈值必须按**全量历史**算（见 should_compress 的说明）
        raw = store.get_messages(owner, sid, limit_turns=None, include_system=False)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"read_failed: {e}", "upto": 0}
    if not raw:
        return {"ok": False, "reason": "empty", "upto": 0}

    block, upto, prev = load_summary_state(owner, sid)
    total_chars = sum(len(m.get("content") or "") for m in raw)
    if not force and not should_compress(total_chars, max_context_chars):
        return {"ok": False, "reason": "below_threshold", "upto": upto}

    pending = [m for m in raw if int(m.get("id") or 0) > upto]
    old = pending[: -kt * 2] if kt > 0 else pending
    if not old:
        return {"ok": False, "reason": "nothing_to_compress", "upto": upto}

    seg, seg_raw = build_compressed_segment(old, compressor)
    if not seg:
        return {"ok": False, "reason": "compressor_empty", "upto": upto}

    # 增量：在旧块上追加一段（不改旧段，前缀只增长）
    new_block = SummaryBlock(list(block.segments))
    new_text = new_block.append(seg)

    recent = pending[-kt * 2 :] if kt > 0 else []
    est_after = estimate_tokens(new_text) + estimate_tokens(
        "\n".join(m.get("content") or "" for m in recent)
    )
    hard_limit = int(
        getattr(config, "COMPRESS_MAX_CONTEXT_CHARS", None)
        or getattr(config, "MAX_CONTEXT_CHARS", 12000)
    )
    decision = apply_safety_valve(
        seg_raw,
        new_text,
        prev,
        est_tokens_after=est_after,
        hard_limit_tokens=hard_limit,
        base_keep_turns=kt,
    )

    # L2：实体丢失且无上一版可回退 → 扩大 keep_turns 重压一次（压得更少、更保守）。
    # 只重试一次：扩到 KEEP_TURNS_EXPANDED 后条件不再成立，不会无限递归。
    if decision.action == ACT_EXPAND_KEEP and decision.keep_turns != kt:
        return maybe_compress(
            owner,
            sid,
            keep_turns=decision.keep_turns,
            compressor=compressor,
            force=True,
            max_context_chars=max_context_chars,
        )
    # L1：有上一版就回退——即**保持库里现状不动**（不是把 prev 写回去，
    # 那会让 compressed_upto 倒退，已压缩的原文又会重新进上下文）
    if decision.action == ACT_ROLLBACK:
        return {"ok": False, "reason": decision.reason, "upto": upto, "action": decision.action}

    new_upto = int(old[-1].get("id") or 0)
    ok = commit_summary(owner, sid, new_text, new_upto, expected_upto=upto)
    return {
        "ok": ok,
        "reason": decision.reason if ok else "race_lost（并发压缩，本轮放弃）",
        "upto": new_upto if ok else upto,
        "action": decision.action,
    }

