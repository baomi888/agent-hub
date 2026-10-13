# -*- coding: utf-8 -*-
"""把 规范/团队研发规范_Word手册.md 转成一份排版过的 .docx。

设计取舍：
  - 手动解析 Markdown 的**子集**（标题 / 段落 / 列表 / 表格 / 代码块 / 引用），
    而不是引入 markdown 库 —— 本机没有该依赖，且手册用到的语法有限。
  - 中文必须显式设 w:eastAsia 字体，否则 Word 里中文会落到默认字体、与英文不搭。
  - 表格按 A4 正文宽度约束，避免超宽溢出。
"""

from __future__ import annotations

import os
import re
import sys

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根（本脚本在 tools/ 下）
SRC = os.path.join(ROOT, "规范", "团队研发规范_Word手册.md")
OUT = os.path.join(ROOT, "规范", "知穗Agent_团队研发规范手册_v1.0.docx")

# 正文字体：中文用等线/微软雅黑，英文用 Consolas 走代码
CN_FONT = "微软雅黑"
CODE_FONT = "Consolas"
BODY_SIZE = Pt(10.5)


# ---------------------------------------------------------------- 字体工具
def set_run_font(run, name=CN_FONT, size=BODY_SIZE, bold=None, color=None):
    """同时设置 ascii/hAnsi/eastAsia 三个字体槽。

    只设 run.font.name 在 Word 里对中文无效 —— 必须补 w:eastAsia。
    """
    run.font.name = name
    run.font.size = size
    if bold is not None:
        run.font.bold = bold
    if color is not None:
        run.font.color.rgb = color
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:ascii"), name)
    rfonts.set(qn("w:hAnsi"), name)
    rfonts.set(qn("w:eastAsia"), name)


def shade_cell(cell, hex_fill: str):
    """给单元格加底色（表头用）。"""
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    tc_pr.append(shd)


# ---------------------------------------------------------------- 行内解析
INLINE_RE = re.compile(r"(\*\*.+?\*\*|`[^`]+`)")


def add_inline(paragraph, text: str, size=BODY_SIZE, base_bold=False):
    """解析 **粗体** 与 `代码`，其余按普通文本写入。"""
    for piece in INLINE_RE.split(text):
        if not piece:
            continue
        if piece.startswith("**") and piece.endswith("**") and len(piece) > 4:
            run = paragraph.add_run(piece[2:-2])
            set_run_font(run, CN_FONT, size, bold=True)
        elif piece.startswith("`") and piece.endswith("`") and len(piece) > 2:
            run = paragraph.add_run(piece[1:-1])
            set_run_font(run, CODE_FONT, Pt(size.pt - 0.5), color=RGBColor(0xC7, 0x25, 0x4E))
        else:
            run = paragraph.add_run(piece)
            set_run_font(run, CN_FONT, size, bold=base_bold if base_bold else None)


def strip_md(text: str) -> str:
    """给表格单元格用：去掉行内标记，保留纯文本。"""
    return INLINE_RE.sub(lambda m: m.group(0)[2:-2] if m.group(0).startswith("**") else m.group(0)[1:-1], text)


# ---------------------------------------------------------------- 块级解析
def parse_table(lines, start):
    """从 start 起收集一张 Markdown 表格，返回 (rows, next_index)。"""
    rows = []
    i = start
    while i < len(lines) and lines[i].strip().startswith("|"):
        raw = lines[i].strip()
        cells = [c.strip() for c in raw.strip("|").split("|")]
        # 跳过分隔行 |---|---|
        if not all(re.fullmatch(r":?-{2,}:?", c or "-") for c in cells):
            rows.append(cells)
        i += 1
    return rows, i


def add_table(doc, rows):
    """把表格写进文档，首行作表头。"""
    if not rows:
        return
    ncols = max(len(r) for r in rows)
    table = doc.add_table(rows=0, cols=ncols)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = True

    for ri, row in enumerate(rows):
        cells = table.add_row().cells
        for ci in range(ncols):
            text = row[ci] if ci < len(row) else ""
            cell = cells[ci]
            cell.text = ""
            para = cell.paragraphs[0]
            para.paragraph_format.space_before = Pt(1)
            para.paragraph_format.space_after = Pt(1)
            if ri == 0:
                add_inline(para, strip_md(text), size=Pt(9.5), base_bold=True)
                shade_cell(cell, "E8EEF7")
            else:
                add_inline(para, text, size=Pt(9.5))
    doc.add_paragraph()


def add_code_block(doc, code_lines):
    """代码/框图块：等宽字体 + 浅灰底 + 不换行断裂感。"""
    if not code_lines:
        return
    para = doc.add_paragraph()
    pf = para.paragraph_format
    pf.space_before = Pt(4)
    pf.space_after = Pt(8)
    pf.left_indent = Cm(0.3)
    # 用段落底纹模拟代码块
    ppr = para._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), "F5F5F5")
    ppr.append(shd)
    for idx, line in enumerate(code_lines):
        run = para.add_run(line)
        set_run_font(run, CODE_FONT, Pt(8.5))
        if idx != len(code_lines) - 1:
            run.add_break()


def add_quote(doc, text):
    para = doc.add_paragraph()
    pf = para.paragraph_format
    pf.left_indent = Cm(0.6)
    pf.space_before = Pt(2)
    pf.space_after = Pt(4)
    add_inline(para, text, size=Pt(10))
    for run in para.runs:
        run.font.color.rgb = RGBColor(0x44, 0x44, 0x44)
    return para


def add_list_item(doc, text, level=0, ordered=False, number=None):
    style = "List Number" if ordered else "List Bullet"
    try:
        para = doc.add_paragraph(style=style)
    except KeyError:
        para = doc.add_paragraph()
    para.paragraph_format.left_indent = Cm(0.75 + 0.6 * level)
    para.paragraph_format.space_before = Pt(0)
    para.paragraph_format.space_after = Pt(2)
    if ordered and number:
        text = f"{number}. {text}"
    add_inline(para, text)


# ---------------------------------------------------------------- 主流程
def main():
    with open(SRC, encoding="utf-8") as fh:
        lines = fh.read().split("\n")

    doc = Document()
    # 页面设置：A4 + 适中页边距，给表格留够宽度
    section = doc.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.left_margin = Cm(2.2)
    section.right_margin = Cm(2.2)
    section.top_margin = Cm(2.2)
    section.bottom_margin = Cm(2.0)

    # 全局默认样式
    normal = doc.styles["Normal"]
    normal.font.name = CN_FONT
    normal.font.size = BODY_SIZE
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), CN_FONT)

    i = 0
    in_code = False
    code_buf: list[str] = []

    while i < len(lines):
        line = lines[i]

        # ---------- 代码块 ----------
        if line.strip().startswith("```"):
            if in_code:
                add_code_block(doc, code_buf)
                code_buf = []
                in_code = False
            else:
                in_code = True
            i += 1
            continue
        if in_code:
            code_buf.append(line)
            i += 1
            continue

        stripped = line.strip()

        # ---------- 空行 ----------
        if not stripped:
            i += 1
            continue

        # ---------- 分隔线 ----------
        if re.fullmatch(r"-{3,}|\*{3,}", stripped):
            para = doc.add_paragraph()
            ppr = para._p.get_or_add_pPr()
            pbdr = OxmlElement("w:pBdr")
            bottom = OxmlElement("w:bottom")
            bottom.set(qn("w:val"), "single")
            bottom.set(qn("w:sz"), "6")
            bottom.set(qn("w:color"), "BBBBBB")
            pbdr.append(bottom)
            ppr.append(pbdr)
            i += 1
            continue

        # ---------- 表格 ----------
        if stripped.startswith("|"):
            rows, i = parse_table(lines, i)
            add_table(doc, rows)
            continue

        # ---------- 标题 ----------
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            level = len(m.group(1))
            text = strip_md(m.group(2).strip())
            if level == 1:
                # 一级标题（章）：另起一页更清晰
                if len(doc.paragraphs) > 3:
                    doc.add_page_break()
                para = doc.add_paragraph()
                para.alignment = WD_ALIGN_PARAGRAPH.LEFT
                para.paragraph_format.space_before = Pt(6)
                para.paragraph_format.space_after = Pt(10)
                run = para.add_run(text)
                set_run_font(run, CN_FONT, Pt(19), bold=True, color=RGBColor(0x1F, 0x3A, 0x5F))
            elif level == 2:
                para = doc.add_paragraph()
                para.paragraph_format.space_before = Pt(12)
                para.paragraph_format.space_after = Pt(6)
                run = para.add_run(text)
                set_run_font(run, CN_FONT, Pt(14.5), bold=True, color=RGBColor(0x25, 0x4A, 0x7A))
            elif level == 3:
                para = doc.add_paragraph()
                para.paragraph_format.space_before = Pt(8)
                para.paragraph_format.space_after = Pt(4)
                run = para.add_run(text)
                set_run_font(run, CN_FONT, Pt(12), bold=True, color=RGBColor(0x33, 0x33, 0x33))
            else:
                para = doc.add_paragraph()
                para.paragraph_format.space_before = Pt(6)
                para.paragraph_format.space_after = Pt(3)
                run = para.add_run(text)
                set_run_font(run, CN_FONT, Pt(11), bold=True, color=RGBColor(0x44, 0x44, 0x44))
            i += 1
            continue

        # ---------- 引用 ----------
        if stripped.startswith(">"):
            add_quote(doc, stripped.lstrip(">").strip())
            i += 1
            continue

        # ---------- 列表 ----------
        m = re.match(r"^(\s*)([-*])\s+(.*)$", line)
        if m:
            level = len(m.group(1)) // 2
            add_list_item(doc, m.group(3).strip(), level=level, ordered=False)
            i += 1
            continue
        m = re.match(r"^(\s*)(\d+)\.\s+(.*)$", line)
        if m:
            level = len(m.group(1)) // 2
            add_list_item(doc, m.group(3).strip(), level=level, ordered=True, number=m.group(2))
            i += 1
            continue

        # ---------- 普通段落 ----------
        para = doc.add_paragraph()
        para.paragraph_format.space_before = Pt(0)
        para.paragraph_format.space_after = Pt(4)
        para.paragraph_format.line_spacing = 1.25
        add_inline(para, stripped)
        i += 1

    if code_buf:
        add_code_block(doc, code_buf)

    # ---------- 页脚页码 ----------
    footer = section.footer
    fp = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = fp.add_run()
    set_run_font(run, CN_FONT, Pt(9), color=RGBColor(0x88, 0x88, 0x88))
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "PAGE")
    run._element.addnext(fld)

    doc.save(OUT)
    print(f"saved: {OUT}")
    print(f"paragraphs: {len(doc.paragraphs)}  tables: {len(doc.tables)}")


if __name__ == "__main__":
    sys.exit(main())
