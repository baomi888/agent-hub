"""PDF / Word 抽出来的"数学乱码"修复。

## 问题

课程资料只要带公式，从 PDF 抽出来的文字几乎必然夹着 `\\uf061`、`\\uf0f7` 这种
私有区字符（U+E000–U+F8FF）。原因不在 pypdf，而在 PDF 本身：公式字体
（MathType 的 Symbol、MT Extra）是**自定义编码**，又没有 ToUnicode 表，
阅读器只能按字形号输出 —— 于是 α 变成 0xF061、矩阵括号变成一串 0xF0E7。
这些字符进了知识库会有两个后果：

1. 检索命中不了：学生问「向量组 α 线性无关」，库里存的是乱码，相似度是噪声；
2. 回答里出现乱码：模型复述引用片段时把它们原样带出来。

## 做法

按字体编码表把私有区码位还原成真正的 Unicode：

  - `Symbol`（含 MathType 的 SymbolMT）就是 Adobe Symbol 编码，
    0xF0XX ↔ 0xXX 一一对应 —— α、β、λ、Σ、∫、≤、≠ 都能还原；
  - `MT Extra` 只提供括号/根号的拼块，还原成对应的括号形状后，
    再把同一侧连续拼块压成一个字符（⎛⎜⎝ → 一个 "("），矩阵才读得通。

两张表都是**纯静态数据**，不依赖字体文件、不引入第三方库 —— 服务器上
没有 fontTools，也不该为读几张试卷装它。
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Adobe Symbol 编码（0x20–0x7E 与 0xA0–0xFF）
# 只列"和 ASCII 不同"的码位；其余原样取 ASCII 字符。
# ---------------------------------------------------------------------------
_SYMBOL_HIGH: dict[int, str] = {
    0x22: "∀", 0x24: "∃", 0x27: "∋", 0x2A: "∗", 0x2D: "−",
    0x40: "≅", 0x5C: "∴", 0x5E: "⊥", 0x60: "‾", 0x7E: "∼",
    # 大写希腊
    0x41: "Α", 0x42: "Β", 0x43: "Χ", 0x44: "Δ", 0x45: "Ε", 0x46: "Φ",
    0x47: "Γ", 0x48: "Η", 0x49: "Ι", 0x4A: "ϑ", 0x4B: "Κ", 0x4C: "Λ",
    0x4D: "Μ", 0x4E: "Ν", 0x4F: "Ο", 0x50: "Π", 0x51: "Θ", 0x52: "Ρ",
    0x53: "Σ", 0x54: "Τ", 0x55: "Υ", 0x56: "ς", 0x57: "Ω", 0x58: "Ξ",
    0x59: "Ψ", 0x5A: "Ζ",
    # 小写希腊（0x61 α / 0x62 β 在 Adobe Symbol 里和 ASCII 的 a/b 同码位，
    # 必须显式列出来，否则会按"表里没有就取 ASCII"回落成 a、b）
    0x61: "α", 0x62: "β", 0x63: "χ", 0x64: "δ", 0x65: "ε", 0x66: "φ",
    0x67: "γ", 0x68: "η", 0x69: "ι", 0x6A: "ϕ", 0x6B: "κ", 0x6C: "λ",
    0x6D: "μ", 0x6E: "ν", 0x6F: "ο", 0x70: "π", 0x71: "θ", 0x72: "ρ",
    0x73: "σ", 0x74: "τ", 0x75: "υ", 0x76: "ϖ", 0x77: "ω", 0x78: "ξ",
    0x79: "ψ", 0x7A: "ζ",
    # 运算符 / 关系符
    0xA1: "ϒ", 0xA2: "′", 0xA3: "≤", 0xA4: "⁄", 0xA5: "∞", 0xA6: "ƒ",
    0xA7: "♣", 0xA8: "♦", 0xA9: "♥", 0xAA: "♠", 0xAB: "↔", 0xAC: "←",
    0xAD: "↑", 0xAE: "→", 0xAF: "↓", 0xB0: "°", 0xB1: "±", 0xB2: "″",
    0xB3: "≥", 0xB4: "×", 0xB5: "∝", 0xB6: "∂", 0xB7: "•", 0xB8: "÷",
    0xB9: "≠", 0xBA: "≡", 0xBB: "≈", 0xBC: "…", 0xBD: "⏐", 0xBE: "⎯",
    0xBF: "↵", 0xC0: "ℵ", 0xC1: "ℑ", 0xC2: "ℜ", 0xC3: "℘", 0xC4: "⊗",
    0xC5: "⊕", 0xC6: "∅", 0xC7: "∩", 0xC8: "∪", 0xC9: "⊃", 0xCA: "⊇",
    0xCB: "⊄", 0xCC: "⊂", 0xCD: "⊆", 0xCE: "∈", 0xCF: "∉", 0xD0: "∠",
    0xD1: "∇", 0xD2: "®", 0xD3: "©", 0xD4: "™", 0xD5: "∏", 0xD6: "√",
    0xD7: "⋅", 0xD8: "¬", 0xD9: "∧", 0xDA: "∨", 0xDB: "⇔", 0xDC: "⇐",
    0xDD: "⇑", 0xDE: "⇒", 0xDF: "⇓", 0xE0: "◊", 0xE1: "〈", 0xE5: "∑",
    # 大括号/圆括号/方括号的拼块（矩阵、分段函数）
    0xE6: "⎛", 0xE7: "⎜", 0xE8: "⎝", 0xE9: "⎡", 0xEA: "⎢", 0xEB: "⎣",
    0xEC: "⎧", 0xED: "⎨", 0xEE: "⎩", 0xEF: "⎪", 0xF1: "〉", 0xF2: "∫",
    0xF3: "⌠", 0xF4: "⎮", 0xF5: "⌡", 0xF6: "⎞", 0xF7: "⎟", 0xF8: "⎠",
    0xF9: "⎤", 0xFA: "⎥", 0xFB: "⎦", 0xFC: "⎫", 0xFD: "⎬", 0xFE: "⎭",
}

# MT Extra 只有寥寥几个字形，且都在拼块区；认得的就还原，认不得的丢掉。
# （丢失的是括号拼块本身，内容仍在——比让乱码进知识库强。）
_MTEXTRA_HIGH: dict[int, str] = {
    0x4C: "⎛", 0x4D: "⎜", 0x4E: "⎝", 0x4F: "⎡", 0x50: "⎢", 0x51: "⎣",
    0x60: "⌠", 0x61: "⎮", 0x62: "⌡", 0x64: "√", 0x65: "⟨", 0x66: "⟩",
    0x68: "⎞", 0x69: "⎟", 0x6A: "⎠", 0x6B: "⎤", 0x6C: "⎥", 0x6D: "⎦",
}

# 拼块 → 目标字符：同一侧的连续拼块最后压成一个
_BRACKET_RUNS = (
    (r"[⎛⎜⎝⌈⌊]+", "("),
    (r"[⎞⎟⎠⌉⌋]+", ")"),
    (r"[⎡⎢⎣]+", "["),
    (r"[⎤⎥⎦]+", "]"),
    (r"[⎧⎨⎩]+", "{"),
    (r"[⎫⎬⎭]+", "}"),
    (r"[⌠⎮⌡]+", "∫"),
)

_PUA_RE = re.compile(r"[\ue000-\uf8ff]")


def _symbol_char(code: int) -> str:
    """Symbol 编码 → Unicode；表里没有的按 ASCII 处理（0x20–0x7E 大多一致）。"""
    if code in _SYMBOL_HIGH:
        return _SYMBOL_HIGH[code]
    if 0x20 <= code <= 0x7E:
        return chr(code)
    return ""


def _mtextra_char(code: int) -> str:
    return _MTEXTRA_HIGH.get(code, "")


def decode_pua(text: str, font_hint: str = "", mt_codes: set[int] | None = None) -> str:
    """把私有区字符按字体还原。

    两种用法：
      - `font_hint`：已经知道这段文字属于哪个字体（逐 span 抽取时用）；
      - `mt_codes`：先整体扫一遍文档，记下"哪些字形号是 MT Extra 贡献的"，
        再按码位区分 —— 因为同一份 PDF 里 Symbol 与 MT Extra 的码位会重叠
        （0x61 在 Symbol 里是 α，在 MT Extra 里是括号拼块），
        只按码位猜必然错一批。

    两个都没给时按 Symbol 解：课程资料里的乱码绝大多数出自 MathType 的
    Symbol 字体，猜错的代价只是个别括号不准，比留乱码好。
    """
    if not _PUA_RE.search(text):
        return text
    low = font_hint.lower()
    is_mt_extra = "mt-extra" in low or "mtextra" in low

    def repl(m: re.Match[str]) -> str:
        code = ord(m.group(0)) - 0xF000
        if not 0 <= code <= 0xFF:
            return ""
        if is_mt_extra or (mt_codes and code in mt_codes):
            return _mtextra_char(code)
        return _symbol_char(code)

    out = _PUA_RE.sub(repl, text)
    for pattern, char in _BRACKET_RUNS:
        out = re.sub(pattern, char, out)
    return out


def clean_text(text: str, mt_codes: set[int] | None = None) -> str:
    """统一入口：还原私有区字符 + 把被括号拼块打散的空白收拾干净。

    只做"读得通"这一件事，不改动正常的中英文内容。
    """
    if not text:
        return text
    out = decode_pua(text, mt_codes=mt_codes)
    # 还原后可能留下 "A、 k" 这种断裂（α 被吃掉的残骸）——不硬修，
    # 但把连续空格压成一个，避免检索时把空白当成分隔符切碎
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out


def garble_ratio(text: str) -> float:
    """乱码占比：私有区 + 替换符 / 总字符。

    用来判断"这份资料到底能不能做文字检索"：占比过高说明文字层是
    扫描/字形映射的残渣，建了索引也只会污染检索结果。
    """
    if not text:
        return 0.0
    bad = len(_PUA_RE.findall(text)) + text.count("\ufffd")
    return bad / max(len(text), 1)
