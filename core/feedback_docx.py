"""用户反馈汇总的 Word 导出（纯标准库 OOXML，零第三方依赖）。

为什么手写 OOXML 而不是 python-docx：
  线上 venv 没有装 python-docx，装包要动服务器环境（还受镜像/代理限制）。
  zipfile + 字符串拼 XML 只用标准库，本地示例和线上产出的字节结构完全一致 ——
  不会出现「示例好看、上线变样」。Word / WPS 都能直接打开（已实测）。

职责边界：
  本模块**只排版**，不知道 uid、姓名、联系方式、回复这些业务字段的存在。
  数据由 `user_feedback.export_docx()` 组装好后传进来，字段白名单就写在
  `build()` 的 docstring 里 —— 从函数签名上不给隐私数据留出口。

★ 排版踩过的坑（2026-11-19 实测）：
  · 日期列第一版给 1180dxa，Word 把 2026-11-18 折成两行；序号列 560 把
    「序号」拆成两个字。现在列宽 700/1450，且短文本加 <w:noWrap/>。
  · 五列宽之和必须 = 9638（A4 宽 11906 减去左右各 1134 的页边距）。
"""

from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

FONT = "Microsoft YaHei"
INK = "4A3420"          # 深棕墨字
MUTED = "8A7B66"        # 次级信息
BORDER = "D9D0C0"
HEAD_FILL = "F5EFE3"

# 序号 / 提交日期 / 分类 / 反馈问题 / 解决状态 —— 和示例一致
COL_WIDTHS = (700, 1450, 1300, 4470, 1718)

# 状态底纹（很浅，只为一眼分组，不做大面积深色）
STATUS_FILL = {
    "open": "FCEBE6",
    "doing": "FFF4D6",
    "done": "E9F2E4",
    "closed": "EFEFEF",
}


def _rpr(size: int = 20, bold: bool = False, color: str = INK) -> str:
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


def _runs(text: str, size: int = 20, bold: bool = False, color: str = INK) -> str:
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
    size: int = 20,
    bold: bool = False,
    color: str = INK,
    align: str | None = None,
    after: int = 60,
) -> str:
    jc = f'<w:jc w:val="{align}"/>' if align else ""
    return (
        "<w:p><w:pPr>"
        f'<w:spacing w:after="{after}" w:line="300" w:lineRule="auto"/>'
        f"{jc}</w:pPr>{_runs(text, size, bold, color)}</w:p>"
    )


def _cell(
    text: str,
    width: int,
    *,
    fill: str | None = None,
    align: str = "left",
    bold: bool = False,
    size: int = 19,
    color: str = INK,
    nowrap: bool = False,
) -> str:
    shd = f'<w:shd w:val="clear" w:color="auto" w:fill="{fill}"/>' if fill else ""
    jc = f'<w:jc w:val="{align}"/>'
    nw = "<w:noWrap/>" if nowrap else ""
    return (
        "<w:tc><w:tcPr>"
        f'<w:tcW w:w="{width}" w:type="dxa"/>{shd}<w:vAlign w:val="center"/>{nw}'
        "</w:tcPr>"
        "<w:p><w:pPr>"
        f'<w:spacing w:before="40" w:after="40" w:line="280" w:lineRule="auto"/>{jc}'
        f"</w:pPr>{_runs(text, size, bold, color)}</w:p></w:tc>"
    )


def _row(cells: list[str], *, header: bool = False) -> str:
    trpr = "<w:trPr><w:tblHeader/></w:trPr>" if header else ""
    return f"<w:tr>{trpr}{''.join(cells)}</w:tr>"


CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    "</Types>"
)
ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)
DOC_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    "</Relationships>"
)
STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<w:styles xmlns:w="{W}"><w:docDefaults><w:rPrDefault><w:rPr>'
    f'<w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:eastAsia="微软雅黑"/>'
    '<w:sz w:val="20"/><w:szCs w:val="20"/></w:rPr></w:rPrDefault>'
    '<w:pPrDefault><w:pPr><w:spacing w:after="60"/></w:pPr></w:pPrDefault>'
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
        status         状态码，仅用于选底纹色
        status_label   状态中文

    uid / name / username / contact / page / replies / created_at 一律不进这里。
    """
    count_text = " / ".join(f"{label} {n}" for label, n in counts if n)
    # 空范围时别留一对空括号：读者会以为"统计丢了"，而不是"确实没有"
    total_line = f"合计：{total} 条" + (f"（{count_text}）" if count_text else "")
    paras = [
        _para("知穗 · 用户反馈汇总", size=30, bold=True, align="center", after=100),
        _para(
            f"导出时间：{stamp}　｜　数据范围：{scope}　｜　{total_line}",
            size=18, color=MUTED, align="center", after=40,
        ),
        _para(
            "已隐去用户姓名、账号 ID 与联系方式；仅含反馈问题与当前状态，不含对话记录。",
            size=18, color=MUTED, align="center", after=180,
        ),
    ]

    rows_xml: list[str] = []
    for n, it in enumerate(items, 1):
        st = str(it.get("status") or "")
        rows_xml.append(
            _row([
                _cell(str(n), COL_WIDTHS[0], align="center", nowrap=True),
                _cell(str(it.get("date") or ""), COL_WIDTHS[1], align="center", size=18, nowrap=True),
                _cell(str(it.get("category_label") or ""), COL_WIDTHS[2], align="center"),
                _cell(str(it.get("content") or ""), COL_WIDTHS[3]),
                _cell(str(it.get("status_label") or ""), COL_WIDTHS[4],
                      fill=STATUS_FILL.get(st), align="center", bold=True),
            ])
        )
    if not rows_xml:
        rows_xml.append(
            _row([_cell("该范围内没有反馈。", sum(COL_WIDTHS), align="center", color=MUTED)])
        )

    table = (
        "<w:tbl><w:tblPr>"
        f'<w:tblW w:w="{sum(COL_WIDTHS)}" w:type="dxa"/><w:tblLayout w:type="fixed"/>'
        "<w:tblBorders>"
        + "".join(
            f'<w:{e} w:val="single" w:sz="4" w:color="{BORDER}"/>'
            for e in ("top", "left", "bottom", "right", "insideH", "insideV")
        )
        + "</w:tblBorders><w:tblCellMar>"
        '<w:top w:w="70" w:type="dxa"/><w:left w:w="100" w:type="dxa"/>'
        '<w:bottom w:w="70" w:type="dxa"/><w:right w:w="100" w:type="dxa"/>'
        "</w:tblCellMar></w:tblPr><w:tblGrid>"
        + "".join(f'<w:gridCol w:w="{w}"/>' for w in COL_WIDTHS)
        + "</w:tblGrid>"
        + _row([
            _cell("序号", COL_WIDTHS[0], fill=HEAD_FILL, align="center", bold=True, nowrap=True),
            _cell("提交日期", COL_WIDTHS[1], fill=HEAD_FILL, align="center", bold=True, nowrap=True),
            _cell("分类", COL_WIDTHS[2], fill=HEAD_FILL, align="center", bold=True),
            _cell("反馈问题", COL_WIDTHS[3], fill=HEAD_FILL, align="center", bold=True),
            _cell("解决状态", COL_WIDTHS[4], fill=HEAD_FILL, align="center", bold=True),
        ], header=True)
        + "".join(rows_xml)
        + "</w:tbl>"
    )
    sect = (
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"'
        ' w:header="709" w:footer="709" w:gutter="0"/></w:sectPr>'
    )
    doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W}"><w:body>'
        + "".join(paras) + table + sect
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
