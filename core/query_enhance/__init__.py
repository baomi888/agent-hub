"""检索词增强（B1 词表契约 + B2 匹配器）。

一句话：**把学生原话里的口语词换成标准词，只换检索用的那份，原话不动。**

对外只暴露两个东西：

    from core.query_enhance import load_vocab, match
    vocab, warns = load_vocab("data/vocab/vocab_v1.csv")
    r = match("高数怎么复习", vocab)
    r.display_text    # -> "高数怎么复习"（原话，永不改）
    r.retrieval_text  # -> "高等数学怎么复习"（喂给检索）

为什么不把这套塞进 api/chat.py
------------------------------
因为它必须能被**离线回归**：94 条金标集跑一遍就知道精确率掉没掉。
写进接口里就只能靠人肉点对话验证，改一次词表要试半天。
"""

from core.query_enhance.ambiguous import (
    Adjudication,
    block_words,
    blocked_for_query,
    clarify_questions,
    load_adjudication,
)
from core.query_enhance.matcher import EnhanceResult, Hit, Skipped, match
from core.query_enhance.vocab import Term, Vocab, load_vocab, norm_key, split_multi

__all__ = [
    "Adjudication",
    "EnhanceResult",
    "Hit",
    "Skipped",
    "Term",
    "Vocab",
    "block_words",
    "blocked_for_query",
    "clarify_questions",
    "load_adjudication",
    "load_vocab",
    "match",
    "norm_key",
    "split_multi",
]
