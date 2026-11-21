"""用户反馈汇总的 Word 导出（纯标准库 OOXML，零第三方依赖）。

为什么手写 OOXML 而不是 python-docx：
  线上 venv 没有装 python-docx，装包要动服务器环境（还受镜像/代理限制）。
  zipfile + 字符串拼 XML 只用标准库，本地示例和线上产出的字节结构完全一致 ——
  不会出现「示例好看、上线变样」。Word / WPS 都能直接打开（已实测）。

职责边界：
  本模块**只排版**，不知道 uid、姓名、联系方式、回复这些业务字段的存在。
  数据由 `user_feedback.export_docx()` 组装好后传进来，字段白名单就写在
  `build()` 的 docstring 里 —— 从函数签名上不给隐私数据留出口。

★ 排版踩过的坑（2026-11-19 两轮实测）：
  · 第一版用五列表格，长反馈被压在 4470dxa 的窄栏里，手机预览字号只有 9.5pt，
    标题和正文挤成一团。用户明确「要纯文字、不要表格、字大一点」——
    现在每条反馈是「一行小标题 + 一段正文」的段落列表，正文 12pt、1.5 倍行距。
  · 日期列曾经给 1180dxa 会折行，那是因为它在表格里；纯文字后日期直接写在
    小标题行（2026-11-18 · 出错了 · 待处理），不存在列宽问题。
  · pPr 的子元素有严格顺序：keepNext → pBdr → spacing → ind → jc。
    写反了 Word 不一定报错，但会静默丢样式（分隔线不出现）。
"""

from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

FONT = "Microsoft YaHei"
INK = "4A3420"          # 深棕墨字
MUTED = "8A7B66"        # 次级信息
RULE = "E8DFCF"         # 条目之间的浅分隔线


def _rpr(size: int = 24, bold: bool = False, color: str = INK) -> str:
    b = "<w:b/>" if bold else ""
    # 统一用单引号 f-string + 双引号 XML 属性：线上 Py3.10 对 f-string 里的
    # 反斜杠比 3.12 严（同引号嵌套/转义会语法错），这里彻底不用转义。
    return (
        "<w:rPr>"
        f'<w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:eastAsia="微软雅黑"/>'
        f'{b}<w:color w:val="{color}"/><w:sz w:val="{size}"/>'
        f'<w:szCs w:val="{size}"/>'
        "</w:rPr>"
    )


def _runs(text: str, size: int = 24, bold: bool = False, color: str = INK) -> str:
    """一段文字 → run 列表。换行用 <w:br/>，用户写了几行就保留几行。"""
    parts = str(text or "").split("\n")
    out = []
    for i, part in enumerate(parts):
        if i:
            out.append("<w:r>" + _rpr(size, bold, color) + "<w:br/></w:r>")
        out.append(
            "<w:r>" + _rpr(size, bold, color)
            + f'<w:t xml:space="preserve">{escape(part)}</w:t></w:r>'
        )
    return "".join(out)


def _para(
    text: str,
    *,
    size: int = 24,
    bold: bool = False,
    color: str = INK,
    align: str | None = None,
    after: int = 120,
    before: int = 0,
    indent: int = 0,
    keep_next: bool = False,
    rule: bool = False,
) -> str:
    """一个段落。rule=条目下方的浅分隔线（用段落边框，不是表格）。"""
    keep = "<w:keepNext/>" if keep_next else ""
    border = (
        f'<w:pBdr><w:bottom w:val="single" w:sz="4" w:space="6" w:color="{RULE}"/></w:pBdr>'
        if rule else ""
    )
    ind = f'<w:ind w:left="{indent}"/>' if indent else ""
    jc = f'<w:jc w:val="{align}"/>' if align else ""
    # ★ pPr 子元素顺序不能乱（见模块 docstring）
    return (
        "<w:p><w:pPr>"
        f"{keep}{border}"
        f'<w:spacing w:before="{before}" w:after="{after}" w:line="360" w:lineRule="auto"/>'
        f"{ind}{jc}"
        "</w:pPr>" + _runs(text, size, bold, color) + "</w:p>"
    )


CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    "</Types>"
)
ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/>'
    "</Relationships>"
)
DOC_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
    'Target="styles.xml"/>'
    "</Relationships>"
)
STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<w:styles xmlns:w="{W}"><w:docDefaults><w:rPrDefault><w:rPr>'
    f'<w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:eastAsia="微软雅黑"/>'
    '<w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr></w:rPrDefault>'
    '<w:pPrDefault><w:pPr><w:spacing w:after="120"/></w:pPr></w:pPrDefault>'
    '</w:docDefaults><w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
    '<w:name w:val="Normal"/></w:style></w:styles>'
)


def build(
    items: list[dict],
    *,
    stamp: str,
    scope: str,
    counts: list[tuple[str, int]],
    total: int,
) -> bytes:
    """生成 docx 字节。

    items 的字段白名单（**多一个都不接**）：
        date           提交日期 YYYY-MM-DD（业务时区）
        category_label 分类中文
        content        反馈正文
        status         状态码（纯文字排版不再用它取底纹，保留只为兼容调用方）
        status_label   状态中文

    uid / name / username / contact / page / replies / created_at 一律不进这里。
    """
    count_text = " / ".join(f"{label} {n}" for label, n in counts if n)
    # 空范围时别留一对空括号：读者会以为"统计丢了"，而不是"确实没有"
    total_line = f"合计：{total} 条" + (f"（{count_text}）" if count_text else "")
    paras = [
        _para("知穗 · 用户反馈汇总", size=36, bold=True, align="center", after=200),
        _para(f"导出时间：{stamp}", size=22, color=MUTED, after=60),
        _para(f"数据范围：{scope}", size=22, color=MUTED, after=60),
        _para(total_line, size=22, color=MUTED, after=60),
        _para(
            "已隐去用户姓名、账号 ID 与联系方式；仅含反馈问题与当前状态，不含对话记录。",
            size=20, color=MUTED, after=360, rule=True,
        ),
    ]
    if not items:
        paras.append(
            _para("该范围内没有反馈。", size=24, color=MUTED, align="center", before=240)
        )
    for n, it in enumerate(items, 1):
        head = f"{n}. {it.get('date') or ''} · {it.get('category_label') or ''} · {it.get('status_label') or ''}"
        paras.append(
            _para(
                head,
                size=24, bold=True,
                before=200 if n > 1 else 0,
                after=80, keep_next=True,
            )
        )
        paras.append(
            _para(str(it.get("content") or ""), size=24, indent=480, after=200, rule=True)
        )

    sect = (
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"'
        ' w:header="709" w:footer="709" w:gutter="0"/></w:sectPr>'
    )
    doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W}"><w:body>'
        + "".join(paras) + sect
        + "</w:body></w:document>"
    )
    files = {
        "[Content_Types].xml": CONTENT_TYPES,
        "_rels/.rels": ROOT_RELS,
        "word/_rels/document.xml.rels": DOC_RELS,
        "word/styles.xml": STYLES,
        "word/document.xml": doc,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in files.items():
            z.writestr(name, body)
    return buf.getvalue()
