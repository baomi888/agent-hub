# -*- coding: utf-8 -*-
"""检索词增强 · 匹配器（B2）。

它只做一件事：把学生原话里的口语词，换成词表里的标准词。
**换的结果只用来检索，原话一个字都不动**（这是技术报告里三条不可违背原则的第三条）。

匹配器要同时满足三个互相拉扯的目标
----------------------------------
1. **该换的要换**（召回）：「高数怎么复习」→「高等数学怎么复习」
2. **不该换的绝不能换**（精确）：反例词、歧义词、已经正确的说法
3. **换完还得像人话**：「二战考研怎么准备」里的「二战」如果换成「考研二战」，
   会变成「考研二战考研怎么准备」——这不是增强，是制造噪声。

第 3 条最容易被忽略。只按「别名表扫一遍全替换」的写法，
在第一版测试里就会撞上它，而且**指标上看不出来**：命中率还是 100%，
只是答案变难看了。所以这里专门有一个 `_adjacent_already_standard` 检查。

优先级（从高到低）
------------------
1. 全局黑名单（调用方显式给的原词）——命中即占位，谁也别想动
2. 长别名优先——避免「操作系统」被拆成「操作」+「系统」
3. 已占用位置不再匹配——保证一个字符只被替换一次
4. 该词条自己的反例——只挡它自己，不牵连别的词条
5. 歧义词（需上下文裁决，来自 X4）——有疑问就不换，宁可少换不可错换
6. 冗余检查——替换后会和标准词叠字的，不换
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.query_enhance.vocab import Vocab

# 判断「相邻片段是否已属于标准词」时，至少比对 2 个字。
# 1 个字太容易误伤（「数」几乎什么都沾边）。
_MIN_FRAGMENT = 2


@dataclass
class Hit:
    """一次命中。`start/end` 是在**原话**里的下标，可直接用于高亮渲染。"""

    raw: str
    canonical: str
    start: int
    end: int


@dataclass
class Skipped:
    """一次「本该换但没换」。不算错，但要留证据，否则调参只能靠猜。"""

    raw: str
    canonical: str
    start: int
    reason: str


@dataclass
class EnhanceResult:
    """匹配结果。

    `display_text` 恒等于原话——三原则里的「不改展示」在类型层面就锁死了，
    调用方拿不到被改过的展示文本，只能拿 `retrieval_text` 去检索。
    """

    original: str
    hits: list[Hit] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)

    @property
    def display_text(self) -> str:
        return self.original

    @property
    def retrieval_text(self) -> str:
        """把命中处替换成标准词后、用于检索的文本。"""
        if not self.hits:
            return self.original
        out, last = [], 0
        for h in self.hits:
            out.append(self.original[last:h.start])
            out.append(h.canonical)
            last = h.end
        out.append(self.original[last:])
        return "".join(out)

    @property
    def canonicals(self) -> list[str]:
        return [h.canonical for h in self.hits]


def _find_all(haystack: str, needle: str) -> list[int]:
    """所有出现位置（含重叠，比如「二战二战」）。不重叠版本会漏掉第二种情况。"""
    out, i = [], haystack.find(needle)
    while i != -1:
        out.append(i)
        i = haystack.find(needle, i + 1)
    return out


def _adjacent_already_standard(query: str, start: int, end: int, canonical: str) -> str:
    """检查替换后会不会和句子里已有的标准词叠字。返回原因（空串=不冲突）。

    两种情形都算冲突：
      · 别名**前面**紧挨着标准词的后半截：`[高等]高数` —— 少见，但存在
      · 别名**后面**紧挨着标准词的前半截：`二战[考研]` —— 这是真实高频情形

    例：canonical = 考研二战，query = 「二战考研怎么准备」
        别名「二战」后面紧跟「考研」，正是 canonical 的前缀 ⇒ 换了会变成
        「考研二战考研」，所以跳过。
    """
    before = query[max(0, start - len(canonical)):start]
    after = query[end:end + len(canonical)]

    for n in range(_MIN_FRAGMENT, len(canonical)):
        # canonical 的后缀落在别名前面
        if before.endswith(canonical[-n:]):
            return f"前文已有标准词尾「{canonical[-n:]}」，替换会叠字"
        # canonical 的前缀落在别名后面
        if after.startswith(canonical[:n]):
            return f"后文已有标准词头「{canonical[:n]}」，替换会叠字"
    return ""


def match(query: str, vocab: Vocab, ambiguous: set[str] | None = None,
          global_block: set[str] | None = None) -> EnhanceResult:
    """匹配并返回结果。不修改 `query`。

    Args:
        query: 学生原话。
        vocab: 词表。
        ambiguous: 需要上下文裁决的**标准词**集合（来自 X4 裁决表）。
            命中了也不换——这类词换错一次的代价（语义漂移）远大于少换一次。
        global_block: 跨词条一律不许替换的**原词**（如「系统」「网络」这类
            在任何词条下都不该机械替换的通用歧义词）。默认不含任何全局反例。
    """
    result = EnhanceResult(original=query)
    if not query or not len(vocab):
        return result

    low = query.lower()
    # 大小写折叠不该改变长度；真变了（极少数语言）就退回原串，别让下标错位。
    if len(low) != len(query):
        low = query

    blocked = {a.lower() for a in (ambiguous or set())}
    taken = [False] * len(low)

    def mark(s: int, e: int) -> None:
        for i in range(s, e):
            taken[i] = True

    def free(s: int, e: int) -> bool:
        return not any(taken[s:e])

    def overlaps(spans: list[tuple[int, int]], s: int, e: int) -> bool:
        return any(ns < e and s < ne for ns, ne in spans)

    # ---- 第一遍：全局黑名单（调用方显式给的，默认空）----
    # ⚠️ 这里**只**吃 global_block，绝不拿 vocab.negatives 当全局用。
    # 实测过：把词表反例当全局黑名单，召回率会从 99.0% 掉到 94.1%，
    # 因为「实习」在 A 句里该保留，却把 B 句的「远程实习」也一起挡了。
    for word in sorted((w.lower() for w in (global_block or set()) if w), key=len, reverse=True):
        for s in _find_all(low, word):
            mark(s, s + len(word))

    # ---- 第二遍：别名，长的先赢 ----
    neg_cache: dict[str, list[tuple[int, int]]] = {}
    for alias, canon_key in vocab.alias_items():   # 已按长度倒序
        if not alias:
            continue
        term = vocab.terms.get(canon_key)
        if term is None:
            continue
        if alias not in low:
            continue
        # 该词条自己的反例跨度：只用来挡它自己，不影响别的词条
        if canon_key not in neg_cache:
            spans: list[tuple[int, int]] = []
            for nk in vocab.negatives_of(term.canonical):
                spans += [(s, s + len(nk)) for s in _find_all(low, nk)]
            neg_cache[canon_key] = spans
        neg_spans = neg_cache[canon_key]

        for s in _find_all(low, alias):
            e = s + len(alias)
            raw = query[s:e]      # 用原话切片，保留原始大小写
            if not free(s, e):
                continue          # 被全局黑名单或更长的别名占了，安静跳过
            if overlaps(neg_spans, s, e):
                result.skipped.append(Skipped(raw, term.canonical, s, "属于该词条的反例"))
                continue
            if canon_key in blocked:
                result.skipped.append(Skipped(raw, term.canonical, s, "歧义词需上下文裁决"))
                continue
            why = _adjacent_already_standard(query, s, e, term.canonical)
            if why:
                result.skipped.append(Skipped(raw, term.canonical, s, why))
                continue
            result.hits.append(Hit(raw=raw, canonical=term.canonical, start=s, end=e))
            mark(s, e)

    result.hits.sort(key=lambda h: h.start)
    return result
