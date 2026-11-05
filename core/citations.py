"""正文里「指不到来源」的引用编号清理。

模型会跟着上下文里的历史回答继续写 `[1]` / `依据：[1][2]`，哪怕这一轮根本没
检索、没联网。用户看到一排点了没反应的小方块，末尾那句「依据」更是无中生有。

这里做的是**确定性**兜底：本轮有多少来源是服务端知道的，模型不知道，所以由
服务端在落库前把指不到的编号删掉。前端 lib/citations.ts 有一份等价实现负责
显示层（历史消息也走那一份），两边规则必须保持一致，改一处要改另一处。

只删指不到的，不碰真引用；`[文案](url)` 这类 markdown 链接也不碰。
"""

from __future__ import annotations

import re

# 联网编号：[W1] / [w3]
_WEB_TOKEN = re.compile(r"\s?\[W(\d{1,3})\]", re.IGNORECASE)
# 资料库编号：[1]；排除 [1](url) 这种 markdown 链接文案
_REF_TOKEN = re.compile(r"\s?\[(\d{1,3})\](?!\()")
# 编号删光之后只剩光杆前缀的行：依据：/ 参考资料：/ Sources:
_BARE_TAIL = re.compile(
    r"^\s*(?:\*\*)?\s*(?:依据|参考资料|参考来源|引用来源|来源|sources?)\s*[:：]\s*(?:\*\*)?\s*[\[\]\d\s,，、;；&]*$",
    re.IGNORECASE,
)


def strip_phantom_citations(
    text: str,
    *,
    has_kb: bool,
    web_ids: set[int] | None = None,
) -> str:
    """删掉正文里指不到来源的引用编号与空「依据」行。

    Args:
        text: 助手回答原文。
        has_kb: 本轮是否真的有资料库来源。False 时所有 `[n]` 都视为凭空写的。
        web_ids: 本轮联网来源的编号集合。None / 空集时所有 `[Wn]` 都删。

    Returns:
        清理后的正文。没有任何可删内容时原样返回（不做 trim，避免动用户排版）。
    """
    if not text:
        return text
    ids = web_ids or set()
    out = text

    if ids:
        out = _WEB_TOKEN.sub(
            lambda m: m.group(0) if int(m.group(1)) in ids else "", out
        )
    else:
        out = _WEB_TOKEN.sub("", out)

    # 有资料库来源时不按编号精确删：refs 是 markdown 文本，编号解析在前端还有
    # 一套（parseRefs），两边对不齐就会误删真角标。宁可少删。
    if not has_kb:
        out = _REF_TOKEN.sub("", out)

    if out == text:
        return text

    kept: list[str] = []
    for line in out.split("\n"):
        if _BARE_TAIL.match(line):
            continue
        kept.append(line.rstrip())
    out = "\n".join(kept)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.rstrip()
