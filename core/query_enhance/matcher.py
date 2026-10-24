# -*- coding: utf-8 -*-
"""检索词增强 · 匹配器（B2）。

它只做一件事：在学生的口语问法后面，**追加**词表里的规范术语，让检索同时能命中
「高数」和「高等数学」两种说法。原话一个字都不动。

★ 为什么是追加而不是替换（2026-10-24 改，这次改动有血有泪）
-----------------------------------------------------------
第一版是替换：`高数怎么复习` → `高等数学怎么复习`。金标集上跑得好看（精确 97.1%、
召回 99.0%），但拿 X2 的 142 条真实问法一测就露了：

    32 条触发换词，**12 条改出了病句**（每 3 条坏 1 条）
    文献综述怎么写   → 文献文献综述怎么写     ← 防护漏了一个方向
    学分绩点怎么算   → 学分平均学分绩点怎么算   ← 替换点吞掉「学分」再还回一个
    查重率多少算安全 → 重复率检测率多少算安全   ← 术语自带「率」，和原字撞车

后两条**防不住**：2 字口语词换成 5~6 字术语，必然和原句相邻的字重新组合。
补 heuristic 只能一个个打补丁，词表一扩又冒新的。

所以改成 OR 扩展（query expansion 的标准做法就是 OR，不是替换）：
原话全保留，末尾追加标准词，检索两边都能命中。病句问题在结构上消失，
而且召回只会更高——原话里的口语词不再被删掉。

匹配器要同时满足三个互相拉扯的目标
----------------------------------
1. **该追加的要追加**（召回）：「高数怎么复习」→ 追加「高等数学」
2. **不该追加的绝不能追加**（精确）：反例词、歧义词、已经说过标准词的说法
3. **追加完还得像人话**：检索文本是「原话 + 空格 + 术语」，原话部分恒为前缀，
   所以不存在「改了语法」这回事；要防的只剩「同一个术语塞两遍」这种冗余。

优先级（从高到低）
------------------
1. 全局黑名单（调用方显式给的原词）——命中即占位，谁也别想动
2. 长别名优先——避免「操作系统」被拆成「操作」+「系统」
3. 已占用位置不再匹配——保证一个字符只被处理一次
4. 该词条自己的反例——只挡它自己，不牵连别的词条
5. 歧义词（需上下文裁决，来自 X4）——有疑问就不追加，宁可少换不可错换
6. 冗余检查——原话里已经说过标准词的，不重复追加
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.query_enhance.vocab import Vocab

# 判断「相邻片段是否已属于标准词」时，至少比对 2 个字。
# 1 个字太容易误伤（「数」几乎什么都沾边）。
_MIN_FRAGMENT = 2

# 原话与追加术语之间、术语与术语之间的分隔符。只用于检索，不参与展示。
# 用空格而不是顿号：检索侧（BM25 / 向量）按空白切词，标点会被当成正文。
_JOIN = " "


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

    ★ 现在 `retrieval_text` 也**以原话开头**（追加式而非替换式），
      所以「展示」和「检索」不再是两种语法，只差末尾多出来的术语串。
    """

    original: str
    hits: list[Hit] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)

    @property
    def display_text(self) -> str:
        return self.original

    @property
    def canonicals(self) -> list[str]:
        """命中侧的标准词（**不去重**）——精确率/召回率按"处"算，重复出现要计多处。

        要拿去检索的请用 `added_terms`（去重版）。
        """
        return [h.canonical for h in self.hits]

    @property
    def added_terms(self) -> list[str]:
        """要追加给检索的标准词，去重且保持出现顺序。

        去重是必须的：「高数和线代都要看，线代更难」里「线性代数」只该出现一次。
        保持顺序是为了让检索文本可复现——同一句话任何时候生成的串都一样，
        否则缓存和 A/B 对比都会变得不可信。
        """
        seen: set[str] = set()
        out: list[str] = []
        for h in self.hits:
            if h.canonical not in seen:
                seen.add(h.canonical)
                out.append(h.canonical)
        return out

    @property
    def retrieval_text(self) -> str:
        """检索用文本 = 原话 + 空格 + 标准词（OR 扩展）。

        例：「高数怎么复习」→「高数怎么复习 高等数学」

        ★ 为什么不替换（2026-10-24 改）
        -------------------------------
        替换会让替换点和原句相邻的字重新组合成病句，而且是结构性的：
        「学分绩点」→「学分平均学分绩点」、「查重率」→「重复率检测率」。
        拿 X2 的 142 条真实问法实测，32 条触发换词里 12 条变形（每 3 条坏 1 条）。
        这类错误补 heuristic 防不住 —— 2 字口语词换 5~6 字术语必然与原邻字碰撞。

        追加则完全没有这个问题：原话一个字不动，口语词和术语同时可被检索命中。
        这也是 query expansion 的标准做法（同义词扩展是 OR，不是替换）。

        ★ 附带好处：`original` 恒为前缀 ⇒ `hits[].start/end` 在检索文本上
        同样成立，前端拿同一套下标就能高亮，不用做偏移换算。
        """
        terms = self.added_terms
        if not terms:
            return self.original
        return self.original + _JOIN + _JOIN.join(terms)


def _find_all(haystack: str, needle: str) -> list[int]:
    """所有出现位置（含重叠，比如「二战二战」）。不重叠版本会漏掉第二种情况。"""
    out, i = [], haystack.find(needle)
    while i != -1:
        out.append(i)
        i = haystack.find(needle, i + 1)
    return out


def _already_said(query: str, start: int, end: int, canonical: str) -> str:
    """原话里是否已经说过这个标准词。返回原因（空串=没说，可以追加）。

    改成追加式之后，这个检查的职责变了：不再是"救语法"（追加不会破坏语法），
    而是**不乱塞重复项**——检索词里同一个术语出现两遍纯属浪费算力，还会稀释相似度。

    三条判据：
      · 标准词整段就在原话里 —— `文献综述怎么写` 里的 `文献综述`
        ★ 这条是 X2 验收补上的。原来只判"替换后会不会叠字"，漏了这个方向：
          `文献` + `[综述]` 替换后确实是 `文献文献综述`，而正确做法是压根不用动。
      · 别名**前面**紧挨着标准词的后半截：`[高等]高数`
      · 别名**后面**紧挨着标准词的前半截：`二战[考研]`
    """
    if canonical in query:
        return f"原话里已有标准词「{canonical}」，不必追加"

    before = query[max(0, start - len(canonical)):start]
    after = query[end:end + len(canonical)]

    for n in range(_MIN_FRAGMENT, len(canonical)):
        # canonical 的后缀落在别名前面
        if before.endswith(canonical[-n:]):
            return f"前文已含标准词尾「{canonical[-n:]}」，不必追加"
        # canonical 的前缀落在别名后面
        if after.startswith(canonical[:n]):
            return f"后文已含标准词头「{canonical[:n]}」，不必追加"
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
            why = _already_said(query, s, e, term.canonical)
            if why:
                result.skipped.append(Skipped(raw, term.canonical, s, why))
                continue
            result.hits.append(Hit(raw=raw, canonical=term.canonical, start=s, end=e))
            mark(s, e)

    result.hits.sort(key=lambda h: h.start)
    return result
