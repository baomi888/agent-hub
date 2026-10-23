# -*- coding: utf-8 -*-
"""检索词增强 · 词表加载器（B1 数据模型的「文件版」）。

为什么先做文件版，而不是按 roadmap 直接建 `controlled_terms` 表
------------------------------------------------------------
roadmap 里 B1 写的是建表。但建表之前必须先回答一个更基础的问题：
**词表长什么样、谁写、怎么写、怎么改。** 这个答案现在只有 X1（杨镇邦的词表）
能提供，而它还没到。

于是这里先定「契约」：读什么列、多个值怎么分隔、编码怎么容错。
X1 一到，只换文件、不动代码；等 F4 审核后台要开工时，再把这份 loader
换成读数据库（接口保持不变），届时 `Term` 就是表行的映射。

这样做的实际好处有先例：X3 金标集已经把两个坑暴露出来——
① `replace_to` / `must_not_replace` 用 `|` 分隔多个值；② CSV 带 UTF-8 BOM。
如果不先定契约就写匹配器，等 X1 到货发现他用顿号分隔、用 GBK 编码，
匹配器就得重写一遍。

词表契约（与 X1 任务书一致，缺列直接报错，不猜）
------------------------------------------------
    term_id           词条编号，如 T0001（可空，落库时补）
    canonical         标准词，检索时用它去查
    domain            领域（数学 / 计算机 / …），只用于统计与分组
    definition        一句话定义，给审核后台看，匹配时不用
    aliases           口语别名，多个用 `|` 分隔
    negative_aliases  反例：看着像、但不能替换的说法，多个用 `|` 分隔

**反例的作用不是「降低召回」，是「防止改坏」。** 比如 `考研二战` 这个词条，
`二战` 是别名（该换），但句子里出现「二战考研」时不该再动它——
那不是把口语换成标准词，是往已经正确的说法上叠加。这类规则只能靠人写进反例。
"""
from __future__ import annotations

import csv
import os
import re
import unicodedata
from dataclasses import dataclass, field

# 与 X1 任务书一致的列。顺序无所谓，按列名取。
REQUIRED_COLUMNS = ("canonical", "aliases")
OPTIONAL_COLUMNS = ("term_id", "domain", "definition", "negative_aliases")

# 多值分隔符。任务书写 `|`，但组员常顺手用全角竖线或分号，一并容错。
# **不认 `/`**：`英语/语言` 这种本身就是带斜杠的词。
_MULTI_SEP = re.compile(r"[|｜；;]")


def split_multi(raw: str) -> list[str]:
    """`高等数学|线代` -> ['高等数学', '线代']；去空、去重、保序。"""
    out: list[str] = []
    for part in _MULTI_SEP.split(str(raw or "")):
        s = part.strip().strip("　")
        if s and s not in out:
            out.append(s)
    return out


def norm_key(s: str) -> str:
    """匹配用的归一键：全角转半角 + 小写。

    **不要**在这里去标点或去空格。匹配要能用命中位置去切原话，
    一旦长度变了，`start/end` 就对不上原字符串，替换会切错字。
    """
    return unicodedata.normalize("NFKC", str(s or "")).strip().lower()


@dataclass
class Term:
    """一个词条。canonical 是唯一的，别名挂在它下面。"""

    canonical: str
    aliases: list[str] = field(default_factory=list)
    negative_aliases: list[str] = field(default_factory=list)
    term_id: str = ""
    domain: str = ""
    definition: str = ""

    def __post_init__(self) -> None:
        # 别名里混进 canonical 自己会让「最长匹配」退化：等同于把正确说法
        # 又替换成它自己，虽然没有实际变化，但会污染命中率统计。
        self.aliases = [a for a in self.aliases if norm_key(a) != norm_key(self.canonical)]


@dataclass
class Vocab:
    """整张词表 + 匹配用的索引。构造一次，反复匹配。"""

    terms: dict[str, Term] = field(default_factory=dict)      # canonical -> Term
    # 别名 -> canonical。同一别名挂到多个 canonical 上属于词表瑕疵，
    # 这里保留第一个并记进 conflicts，交由回归脚本报出来。
    alias_to_canonical: dict[str, str] = field(default_factory=dict)
    negatives: set[str] = field(default_factory=set)
    conflicts: list[tuple[str, str, str]] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.terms)

    def add(self, term: Term) -> None:
        """加入一个词条；canonical 重复则合并别名（X1 多人分头写必然出现）。"""
        key = norm_key(term.canonical)
        if not key:
            return
        exist = self.terms.get(key)
        if exist:
            for a in term.aliases:
                if a not in exist.aliases:
                    exist.aliases.append(a)
            for n in term.negative_aliases:
                if n not in exist.negative_aliases:
                    exist.negative_aliases.append(n)
        else:
            self.terms[key] = term
            exist = term
        for a in exist.aliases:
            k = norm_key(a)
            owner = self.alias_to_canonical.get(k)
            if owner and owner != key:
                self.conflicts.append((a, owner, key))
                continue
            self.alias_to_canonical[k] = key
        # ★ 反例**按词条挂**，不做全局黑名单。
        # 原因有实测：把金标里所有 must_not_replace 灌成全局反例后，
        # 召回率从 99.0% 掉到 94.1% —— 因为「实习」在某句里该保留，
        # 就因为它在另一句的反例里出现，害得「远程实习→线上实习」也被挡掉了。
        # 反例是「这个词条在此处的例外」，不是「全词表禁用词」。
        for n in exist.negative_aliases:
            self.negatives.add(norm_key(n))

    def negatives_of(self, canonical: str) -> list[str]:
        """取某个词条自己的反例（已归一、去空）。"""
        term = self.terms.get(norm_key(canonical))
        if not term:
            return []
        return [k for k in (norm_key(n) for n in term.negative_aliases) if k]

    def alias_items(self) -> list[tuple[str, str]]:
        """按长度倒序的 (别名, canonical) 列表——最长匹配优先要用。"""
        return sorted(self.alias_to_canonical.items(), key=lambda kv: -len(kv[0]))


def load_vocab(path: str) -> tuple[Vocab, list[str]]:
    """读词表 CSV。返回 (词表, 警告列表)。

    容错策略：缺必需列 → 抛 ValueError（列名对不上时安静地少匹配一半，
    比直接报错更难查）；个别行写错 → 跳过并记警告，不整体失败。
    """
    warnings: list[str] = []
    if not os.path.exists(path):
        raise ValueError(f"词表文件不存在：{path}")

    raw = open(path, "rb").read()
    if raw[:3] == b"\xef\xbb\xbf":
        raw = raw[3:]      # Excel 导出必带 BOM，不去掉第一列名会变成 "\ufeffcanonical"
    text = raw.decode("utf-8-sig", errors="replace")

    rows = list(csv.reader(text.splitlines()))
    if not rows:
        raise ValueError("词表是空的")
    header = [h.strip().lstrip("\ufeff") for h in rows[0]]
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        raise ValueError(f"词表缺必需列 {missing}；实际列 = {header}")

    vocab = Vocab()
    for lineno, cells in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in cells):
            continue
        d = {header[i]: (cells[i] if i < len(cells) else "") for i in range(len(header))}
        canonical = (d.get("canonical") or "").strip()
        if not canonical:
            warnings.append(f"第 {lineno} 行没有 canonical，已跳过")
            continue
        aliases = split_multi(d.get("aliases") or "")
        if not aliases:
            warnings.append(f"第 {lineno} 行「{canonical}」没有别名，该词条永远不会被命中")
        vocab.add(Term(
            canonical=canonical,
            aliases=aliases,
            negative_aliases=split_multi(d.get("negative_aliases") or ""),
            term_id=(d.get("term_id") or "").strip(),
            domain=(d.get("domain") or "").strip(),
            definition=(d.get("definition") or "").strip(),
        ))

    if vocab.conflicts:
        seen = {c[0] for c in vocab.conflicts}
        warnings.append(
            f"{len(seen)} 个别名挂到了多个标准词上（只会用第一个）："
            + "；".join(f"{a}→{x}/{y}" for a, x, y in vocab.conflicts[:5])
        )
    return vocab, warnings
