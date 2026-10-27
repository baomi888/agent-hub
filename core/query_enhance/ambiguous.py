"""歧义词裁决表（X4 终裁结果）的加载与使用。

这张表输出三样东西，都直接给匹配器或前端用：

    block_words()          无条件「禁止入表」的词 —— 永远不许被替换
    blocked_for_query()    再加上「有条件下入表」但本次没命中触发词的词
    clarify_questions()    需要澄清时该问用户什么（一句话）

为什么要「有条件」这一类
------------------------
`综述` 在科研/课程语境下压倒性指「文献综述」（一个章节），
但「你综述一下」是动词义。这类词既不能无脑禁，也不能无脑换，
只能靠触发词（怎么写 / 部分 / 章节 / 论文 …）圈定。

为什么绝大多数词是「禁止入表」而不是「有条件替换」
--------------------------------------------
`栈`、`队列`、`树`、`模型`、`框架` 这些词的共同点是：**它们本身就是标准术语**，
不是一个更规范说法的口语变体。「栈」没有"正确写法"可换——它需要的是**上下文**，
不是替换。把它们放进替换词表，等于把一句正确的话改成另一句同样含糊的话。

所以这张表的主要价值是**评审闸门**：谁想往 `vocab_v1.csv` 里加「系统→操作系统」，
这里会直接拦下来，并给出理由。
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field

# 裁决取值（与 ops/build_adjudication.py 写出的 CSV 一致）
DECISION_BLOCK = "禁止入表"
DECISION_CLARIFY = "禁止入表·需澄清"
DECISION_CONDITIONAL = "可入表·有条件"

_YES = {"是", "y", "yes", "true", "1", "需要"}


@dataclass
class Adjudication:
    word: str
    decision: str
    needs_clarify: bool = False
    triggers: list[str] = field(default_factory=list)
    clarify_text: str = ""
    reason: str = ""
    risk: str = ""
    ambiguous_type: str = ""

    @property
    def is_blocked(self) -> bool:
        """无条件禁用（不看上下文）。"""
        return self.decision in (DECISION_BLOCK, DECISION_CLARIFY)

    @property
    def is_conditional(self) -> bool:
        return self.decision == DECISION_CONDITIONAL


def _split(raw: str) -> list[str]:
    return [p.strip() for p in str(raw or "").replace("｜", "|").split("|") if p.strip()]


def load_adjudication(path: str) -> dict[str, Adjudication]:
    """读裁决表。返回 {词: Adjudication}。文件不存在返回空字典（不抛错）。

    为什么不抛错：裁决表是**增强项**。它缺失时匹配器应该退回「不做歧义拦截」，
    而不是让整个检索增强功能起不来。
    """
    if not os.path.exists(path):
        return {}
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    out: dict[str, Adjudication] = {}
    for r in rows:
        word = (r.get("词") or "").strip()
        if not word:
            continue
        out[word] = Adjudication(
            word=word,
            decision=(r.get("裁决") or "").strip(),
            needs_clarify=(r.get("需澄清") or "").strip().lower() in _YES,
            triggers=_split(r.get("触发词") or ""),
            clarify_text=(r.get("澄清问法") or "").strip(),
            reason=(r.get("依据") or "").strip(),
            risk=(r.get("风险等级") or "").strip(),
            ambiguous_type=(r.get("歧义类型") or "").strip(),
        )
    return out


def block_words(rows: dict[str, Adjudication]) -> set[str]:
    """无条件禁用的词（可直接传给 `match(..., global_block=...)`）。"""
    return {w for w, a in rows.items() if a.is_blocked}


def blocked_for_query(rows: dict[str, Adjudication], query: str) -> set[str]:
    """针对**这一句**该禁的词。

    「可入表·有条件」的词：命中任一触发词才放行，否则当禁用处理。
    这样 `综述部分怎么写` 能换成「文献综述」，而 `你综述一下` 保持原样。
    """
    blocked = block_words(rows)
    for word, a in rows.items():
        if not a.is_conditional:
            continue
        if not any(t in query for t in a.triggers):
            blocked.add(word)
    return blocked


def clarify_questions(rows: dict[str, Adjudication], query: str) -> list[str]:
    """查这句话里有哪些词会让人产生歧义、值得追问。

    只返回**确实出现在 query 里**且标了需澄清的词，避免前端每句都追问。
    """
    out = []
    for word, a in rows.items():
        if a.needs_clarify and a.clarify_text and word in query:
            out.append(a.clarify_text)
    return out
