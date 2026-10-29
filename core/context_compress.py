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

import json
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

# v2 折叠开关：False 时完全回到 v1 行为（折最早段，不看 salience / staged）。
FOLD_BY_SALIENCE = bool(getattr(config, "COMPRESS_V2_FOLD", True))


# ==================== v2：分流与 salience（零 LLM，规则信号）====================
#
# 设计出自《建议报告_2026-10-26_对话提取与记忆压缩.md》(v2) §4.3/4.4/4.5：
# 压缩段需要 kind / salience / staged 等结构化元数据，才能回答两个问题：
#   1) 这段知识值不值得沉淀进知识库（链③准入）
#   2) 预算不够时先折谁（链②折叠：只折已 staged 的最低 salience 段）
# 全部规则信号、零 LLM 成本，冷启动即可用。

# kind 先验权重：约束/待办/偏好/纠正 > 目标 > 结论 > 事实 > 其它
KIND_WEIGHTS: dict[str, float] = {
    "约束": 1.0,
    "待办": 1.0,
    "偏好": 1.0,
    "纠正": 1.0,
    "目标": 0.9,
    "结论": 0.8,
    "事实": 0.6,
    "其它": 0.3,
}

# 情感强度信号（报告 §4.5：用高置信度规则，不引情感模型）
_RE_CORRECTION = re.compile(r"(不对|不是这个|我说的是|你理解错了|重新说|搞错了)")
_RE_MEMORIZE = re.compile(r"(记住|别忘了|以后都|记一下|务必记)")
_RE_ABSOLUTE = re.compile(r"(必须|绝对|千万|一定|永远|再也|无论如何)")
_RE_NEGATIVE = re.compile(r"(不要|别|禁止|不许|不能|不可以)")
_RE_PREFERENCE = re.compile(r"(我喜欢|我不喜欢|我偏好|我习惯|我讨厌|我倾向)")
_RE_GOAL = re.compile(r"(目标是|我打算|我的目标|准备考|计划在|争取)")

# salience 四信号权重（报告 §4.4 公式）
_W_TYPE, _W_SINGLETON, _W_OUTLIER, _W_EMOTION = 0.4, 0.25, 0.2, 0.15


def classify_text(text: str) -> tuple[str, float]:
    """零 LLM 分流：返回 (kind, emotion)。

    kind 是"这段话属于哪类知识"（决定 type_weight 与入库候选资格），
    emotion 是 0~1 的强调强度（纠正/显式记住最高）。规则顺序即优先级：
    纠正 > 显式记住 > 约束/待办 > 偏好 > 目标 > 事实（硬实体）> 其它。
    """
    if not text:
        return "其它", 0.0
    if _RE_CORRECTION.search(text):
        return "纠正", 1.0
    if _RE_MEMORIZE.search(text):
        # 显式要求记住：kind 归"事实"（内容本身未必是约束），emotion 顶格
        return "事实", 1.0
    if _RE_ABSOLUTE.search(text) or _RE_NEGATIVE.search(text):
        return "约束", 0.8
    if _RE_TODO.search(text):
        return "待办", 0.7
    if _RE_PREFERENCE.search(text):
        return "偏好", 0.6
    if _RE_GOAL.search(text):
        return "目标", 0.5
    if extract_entities(text):
        return "事实", 0.3
    return "其它", 0.0


def singleton_ratio(text: str, corpus: str) -> float:
    """首现且唯一信号（报告 §4.4）：段内实体在全会话只出现一次的比例。

    "只说一次"意味着没有别的段落能兜住它——折掉就真的丢了。
    corpus 必须包含 text 自身（否则所有实体都"只出现一次"）。
    """
    ents = extract_entities(text)
    if not ents:
        return 0.0
    unique = sum(1 for e in ents if corpus.count(e) <= 1)
    return unique / len(ents)


def jaccard_sim(a: str, b: str) -> float:
    """字符 2-gram Jaccard 相似度。零依赖近似，与检索侧 MMR 去重同源。"""
    if not a or not b:
        return 0.0
    ga = {a[i : i + 2] for i in range(len(a) - 1)}
    gb = {b[i : i + 2] for i in range(len(b) - 1)}
    union = ga | gb
    if not union:
        return 0.0
    return len(ga & gb) / len(union)


def outlier_score(text: str, others: list[str]) -> float:
    """离群度信号（报告 §4.4）：`1 - max_sim(该段, 其它段)`。

    与其它内容都不像 → 不可从别处推导 → 必须自留。
    相似度用字符 2-gram Jaccard（避免为打分去调 embedding）。
    """
    if not others:
        return 1.0
    max_sim = max(jaccard_sim(text, o) for o in others if o)
    return max(0.0, min(1.0, 1.0 - max_sim))


def compute_salience(
    kind: str, emotion: float, singleton: float, outlier: float
) -> float:
    """salience = 0.4·type_weight + 0.25·singleton + 0.2·outlier + 0.15·emotion。

    取值 0~1。约束句 > 闲聊句、singleton 句 > 重复句，是 §6-4 的验收口径。
    """
    w = KIND_WEIGHTS.get(kind, 0.3)
    val = (
        _W_TYPE * w
        + _W_SINGLETON * max(0.0, min(1.0, singleton))
        + _W_OUTLIER * max(0.0, min(1.0, outlier))
        + _W_EMOTION * max(0.0, min(1.0, emotion))
    )
    return round(max(0.0, min(1.0, val)), 4)


@dataclass
class SegmentMeta:
    """压缩段的元数据（与 segments 按下标一一对应）。

    存储在 conversations.compressed_meta（JSON 数组），压缩块文本本身保持
    `[COMPRESSED-n] 文本` 格式不变——prompt 里不出现元数据噪声，KV 前缀不受影响。
    旧数据没有 meta 列，或数组长度对不齐时，一律补默认值（kind=其它）。
    """

    kind: str = "其它"
    refs: list[int] = field(default_factory=list)  # 来源 message id，入库溯源用
    role: str = "user"
    ts: int = 0
    salience: float = 0.5
    hits: int = 0
    staged: bool = False  # 已沉淀进知识库 → 允许被折叠释放
    superseded: bool = False  # 被纠正过的旧说法（报告 §5.2）
    sid: str = ""  # 来源会话 id
    conv_title: str = ""  # 来源会话标题（入库 metadata.source 用）

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "refs": list(self.refs),
            "role": self.role,
            "ts": self.ts,
            "salience": self.salience,
            "hits": self.hits,
            "staged": self.staged,
            "superseded": self.superseded,
            "sid": self.sid,
            "conv_title": self.conv_title,
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> SegmentMeta:
        d = d or {}
        return cls(
            kind=str(d.get("kind") or "其它"),
            refs=[int(r) for r in (d.get("refs") or [])],
            role=str(d.get("role") or "user"),
            ts=int(d.get("ts") or 0),
            salience=float(d.get("salience", 0.5)),
            hits=int(d.get("hits") or 0),
            staged=bool(d.get("staged", False)),
            superseded=bool(d.get("superseded", False)),
            sid=str(d.get("sid") or ""),
            conv_title=str(d.get("conv_title") or ""),
        )


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
    """压缩块：分段存储，增量追加不改旧段。

    v2：与 segments 平行维护一份 SegmentMeta（metas），折叠时按
    "salience 最低且已 staged"选择牺牲段；meta 缺失（旧数据）时行为与 v1 完全一致。
    """

    segments: list[str] = field(default_factory=list)
    metas: list[SegmentMeta] = field(default_factory=list)

    # ---- 序列化 ----
    def render(self) -> str:
        """渲染成落库文本：每段一行 `[COMPRESSED-n] ...`。"""
        return "\n".join(f"{SEG_PREFIX}{i + 1}] {seg}" for i, seg in enumerate(self.segments))

    def meta_json(self) -> str:
        """metas 序列化成 JSON（落 conversations.compressed_meta 列）。"""
        return json.dumps([m.to_dict() for m in self.metas], ensure_ascii=False)

    @classmethod
    def parse(cls, text: str | None, meta_json: str | None = None) -> SummaryBlock:
        """从落库文本还原。解析失败时退回"整块一段"，不丢内容。

        旧格式兼容（报告 §4.2 硬约束）：`[COMPRESSED-n] 文本` 无任何 kind 信息
        → 补默认 meta（kind=其它 / salience=0.5 / role=user），老会话不废。
        meta 数量与 segments 对不齐时同样补默认，绝不让结构化改造丢内容。
        """
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
        metas: list[SegmentMeta] = []
        if meta_json:
            try:
                raw = json.loads(meta_json)
                if isinstance(raw, list):
                    metas = [SegmentMeta.from_dict(d) for d in raw]
            except (ValueError, TypeError):
                metas = []
        while len(metas) < len(segs):
            metas.append(SegmentMeta())
        return cls(segs, metas[: len(segs)])

    # ---- 预算管理 ----
    @property
    def chars(self) -> int:
        return len(self.render())

    def append(self, segment: str, meta: SegmentMeta | None = None) -> str:
        """追加一段（**不改旧段**）。超预算或超段数时按 v2 规则折叠。"""
        segment = (segment or "").strip()
        if segment:
            self.segments.append(segment)
            self.metas.append(meta or SegmentMeta())
        self._enforce_budget()
        return self.render()

    def _pick_fold_indices(self, fold_n: int) -> list[int]:
        """选折叠对象：v2 折"已 staged 里 salience 最低的"，退化到折最早的。

        - FOLD_BY_SALIENCE 关闭 → 永远走 v1 行为
        - meta 齐全且存在 staged 段 → 从 staged（且未被作废）里挑 salience 最低的
          fold_n 段——**已入库的内容折掉只是释放，信息还在知识库里**
        - 否则（旧数据 / 还没有任何段入库）→ 折最早的 fold_n 段（v1 行为）
        """
        n = min(fold_n, len(self.segments) - 1)
        if n <= 0:
            return []
        idx_all = list(range(len(self.segments)))
        if not FOLD_BY_SALIENCE or len(self.metas) != len(self.segments):
            return idx_all[:n]
        staged = [
            i
            for i, m in enumerate(self.metas)
            if m.staged and not m.superseded
        ]
        if not staged:
            return idx_all[:n]
        # 概述段（"[早期内容已折叠] …"）排到最后：反复折叠它自己只会嵌套前缀、
        # 不净减字符；只有当 staged 里全是概述时才允许（此时走收缩路径）
        non_overview = [i for i in staged if not self.segments[i].startswith("[早期内容已折叠]")]
        pool = non_overview or staged
        pool.sort(key=lambda i: (self.metas[i].salience, i))
        return sorted(pool[:n])

    def _merge_meta(self, indices: list[int]) -> SegmentMeta:
        """被折叠各段的 meta 合并：kind 取 salience 最高段的，refs 并集。"""
        parts = [self.metas[i] for i in indices if i < len(self.metas)]
        if not parts:
            return SegmentMeta()
        top = max(parts, key=lambda m: m.salience)
        merged_refs: list[int] = []
        for m in parts:
            for r in m.refs:
                if r not in merged_refs:
                    merged_refs.append(r)
        return SegmentMeta(
            kind=top.kind,
            refs=merged_refs,
            role=parts[0].role,
            ts=min(m.ts for m in parts if m.ts) if any(m.ts for m in parts) else 0,
            # 折叠概述是残余信息：salience 取最低，但已备份 → 标 staged
            salience=min(m.salience for m in parts),
            hits=sum(m.hits for m in parts),
            staged=all(m.staged for m in parts) if parts else False,
            superseded=all(m.superseded for m in parts) if parts else False,
            sid=top.sid,
            conv_title=top.conv_title,
        )

    def _fold(self, fold_n: int) -> None:
        """把选中的 fold_n 段折成一段概述，放回原最早选中位置。"""
        indices = self._pick_fold_indices(fold_n)
        if not indices:
            return
        # 只选中一个"已折叠概述"段时：收缩它自己，而不是重建嵌套概述
        if len(indices) == 1 and self.segments[indices[0]].startswith("[早期内容已折叠]"):
            i = indices[0]
            self.segments[i] = "[早期内容已折叠] " + self.segments[i][:200]
            if i < len(self.metas):
                self.metas[i].salience = min(self.metas[i].salience, 0.1)
            return
        folded_texts = [self.segments[i] for i in indices]
        overview = f"[早期内容已折叠] {' '.join(folded_texts)[:300]}"
        merged_meta = self._merge_meta(indices)
        # staged 由 _merge_meta 的 all() 语义决定：只有全部来源段都已入库，
        # 概述才允许被标记为"已备份"；未入库内容折成的概述不谎报 staged
        keep_texts: list[str] = []
        keep_metas: list[SegmentMeta] = []
        inserted = False
        for i, (t, m) in enumerate(zip(self.segments, self.metas, strict=True)):
            if i in indices:
                if not inserted:
                    keep_texts.append(overview)
                    keep_metas.append(merged_meta)
                    inserted = True
                continue
            keep_texts.append(t)
            keep_metas.append(m)
        if not inserted:  # 理论不可达（indices 非空），防御
            keep_texts.append(overview)
            keep_metas.append(merged_meta)
        self.segments = keep_texts
        self.metas = keep_metas

    def _fold_head_v1(self) -> None:
        """v1 的字符超限动作：第一段收缩成概述、第二段直接删除。

        保留 v1 精确行为是为了旧数据/旧测试的兼容：无 meta 或没有 staged 段时，
        字符超限仍然走这条路径（每轮净减"第二段"的字符）。
        """
        first = self.segments[0]
        had_meta = len(self.metas) == len(self.segments)
        if first.startswith("[早期内容已折叠]"):
            # 已经折叠过：继续压缩这一段本身
            self.segments[0] = "[早期内容已折叠] " + first[:200]
            if len(self.segments) == 1:
                return
            self.segments.pop(1)
            if had_meta and len(self.metas) > 1:
                self.metas.pop(1)
        else:
            self.segments[0] = f"[早期内容已折叠] {first[:200]}"
            self.segments.pop(1)
            if had_meta and len(self.metas) > 1:
                self.metas.pop(1)

    def _enforce_budget(self) -> None:
        """压缩块自身的预算：段数超限 / 字符超限时折叠。

        v2（报告 §4.4）：从"折最早的"改成"折 salience 最低且已 staged 的"——
        只有已经沉淀进知识库的段才允许被折叠释放，没入库的段折了就真的没了。
        全部未 staged（或旧数据无 meta）时退回 v1 的"折最早"，宁可折也不要让
        预算失控；真正的安全底座是 L3 硬截断与入库后的释放。
        """
        # 段数超限：把最早的几段折成一段
        if len(self.segments) > MAX_SEGMENTS:
            self._fold(len(self.segments) - MAX_SEGMENTS + 1)
        # 字符超限：折叠直到进预算
        guard = 0
        while self.chars > MAX_SUMMARY_CHARS and len(self.segments) > 1 and guard < 50:
            guard += 1
            can_v2 = (
                FOLD_BY_SALIENCE
                and len(self.metas) == len(self.segments)
                and any(m.staged and not m.superseded for m in self.metas)
            )
            if can_v2:
                self._fold(1)
            else:
                self._fold_head_v1()


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
    meta_json: str | None = None,
) -> bool:
    """乐观锁写入压缩块。返回 False 表示并发冲突（放弃本轮压缩）。

    为什么用乐观锁：压缩是回答结束后的异步动作，下一轮请求可能先到。
    如果两边都认为自己该写 `compressed_upto`，就会出现
    "压缩块 + 被压缩的原文同时进上下文"的重复（评审指出的竞态）。
    以 `compressed_upto` 做 CAS：不匹配就放弃，下次压缩再试。

    v2：`meta_json` 是 SegmentMeta 数组（落 compressed_meta 列）。
    传 None 时不改动已有 meta（保持旧调用方行为不变）。
    """
    from core import session as store

    with store._get_conn() as conn:  # noqa: SLF001 —— 同模块内的既定用法
        if meta_json is None:
            cur = conn.execute(
                "UPDATE conversations SET compressed_summary = ?, compressed_upto = ?, "
                "compressed_summary_prev = COALESCE(compressed_summary, compressed_summary_prev) "
                "WHERE id = ? AND owner_id = ? AND COALESCE(compressed_upto, 0) = COALESCE(?, 0)",
                (new_summary, upto_message_id, sid, owner, expected_upto),
            )
        else:
            cur = conn.execute(
                "UPDATE conversations SET compressed_summary = ?, compressed_upto = ?, "
                "compressed_meta = ?, "
                "compressed_summary_prev = COALESCE(compressed_summary, compressed_summary_prev) "
                "WHERE id = ? AND owner_id = ? AND COALESCE(compressed_upto, 0) = COALESCE(?, 0)",
                (new_summary, upto_message_id, meta_json, sid, owner, expected_upto),
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

    v2：同时读取 compressed_meta 列并交给 parse——旧库该列为 NULL，
    parse 会补默认 meta（kind=其它 / salience=0.5），行为不变。
    """
    from core import session as store

    with store._get_conn() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT compressed_summary, compressed_summary_prev, compressed_upto, "
            "compressed_meta FROM conversations WHERE id = ? AND owner_id = ?",
            (sid, owner),
        ).fetchone()
    if not row:
        return SummaryBlock([]), 0, None
    return (
        SummaryBlock.parse(row["compressed_summary"], row["compressed_meta"]),
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

    # v2：为这一段构建元数据（kind / salience / refs），入库侧与折叠都依赖它
    seg_meta = build_segment_meta(old, seg, block.segments)
    seg_meta.sid = sid

    # 增量：在旧块上追加一段（不改旧段，前缀只增长）
    new_block = SummaryBlock(list(block.segments), list(block.metas))
    new_text = new_block.append(seg, seg_meta)

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
    ok = commit_summary(
        owner, sid, new_text, new_upto, expected_upto=upto, meta_json=new_block.meta_json()
    )
    return {
        "ok": ok,
        "reason": decision.reason if ok else "race_lost（并发压缩，本轮放弃）",
        "upto": new_upto if ok else upto,
        "action": decision.action,
    }


def build_segment_meta(
    old_messages: list[dict], seg_text: str, existing_segments: list[str]
) -> SegmentMeta:
    """为一个压缩段构建元数据（报告 §4.2 Segment 的落库形态）。

    kind 与 salience 由批内的**候选知识句**决定（句级判定比整段判定准）：
    kind 取批内权重最高的，salience 取候选里最高的；整批没有候选时
    按段文本本身判定。refs 记录批内全部 message id——入库溯源与 staged
    回写都靠它把"段"和"消息"对上。
    """
    from kb import dialogue_import  # 函数内导入：dialogue_import 依赖本模块的分类器

    refs = [int(m.get("id") or 0) for m in old_messages]
    roles = [m.get("role") or "user" for m in old_messages]
    role = "assistant" if roles.count("assistant") > roles.count("user") else "user"
    ts = max((int(m.get("created_at") or 0) for m in old_messages), default=0)
    corpus = "\n".join(m.get("content") or "" for m in old_messages)
    try:
        cands = dialogue_import.extract_candidates(old_messages)
    except Exception:  # noqa: BLE001 —— 元数据缺失只降级不致命
        cands = []
    if cands:
        kind = max((c["kind"] for c in cands), key=lambda k: KIND_WEIGHTS.get(k, 0.0))
        salience = max(c["salience"] for c in cands)
    else:
        kind, emotion = classify_text(seg_text)
        salience = compute_salience(
            kind,
            emotion,
            singleton_ratio(seg_text, corpus),
            outlier_score(seg_text, [s for s in existing_segments if s]),
        )
    return SegmentMeta(kind=kind, refs=refs, role=role, ts=ts, salience=salience)


def mark_segments_staged(owner: str, sid: str, ingested_refs: set[int]) -> int:
    """入库成功后回写 staged（报告 §4.2 的 staged 字段 / §6-5 的"已备份才可释放"）。

    判定口径（刻意保守）：按**整条消息** classify 出 kind != 其它 视为知识消息；
    段内的知识消息 id 全部已入库（⊆ ingested_refs）时该段才标 staged。
    偏保守是安全的：少标只是晚一点折叠，错标会丢掉未入库的内容。

    CAS 写回（与压缩同一条乐观锁），失败放弃——下次入库会再标。
    返回本次新标记的段数。
    """
    from core import session as store

    try:
        msgs = store.get_messages(owner, sid, limit_turns=None, include_system=False)
    except Exception:  # noqa: BLE001
        return 0
    knowledge_ids = {
        int(m.get("id") or 0)
        for m in msgs
        if classify_text(m.get("content") or "")[0] != "其它"
    }
    knowledge_ids.discard(0)
    block, upto, _prev = load_summary_state(owner, sid)
    changed = 0
    ingested = set(ingested_refs)
    for meta in block.metas:
        seg_knowledge = knowledge_ids & set(meta.refs)
        if seg_knowledge and seg_knowledge <= ingested and not meta.staged:
            meta.staged = True
            changed += 1
    if not changed:
        return 0
    ok = commit_summary(
        owner, sid, block.render(), upto, expected_upto=upto, meta_json=block.meta_json()
    )
    return changed if ok else 0

