"""矢量图绘制基元：统一配色、文本换行、卡片/面板、连线、Mermaid/Graphviz 导出。

所有图都由同一份数据模型（groups + nodes + edges）驱动，保证 HTML / PNG / .mmd / .dot
四份产物描述的是同一张图，后续改图只需改内容文件。
"""

from __future__ import annotations

import html
import math
from dataclasses import dataclass, field

CANVAS_W = 1640
FONT = (
    "-apple-system, 'Segoe UI', 'Microsoft YaHei', 'PingFang SC', "
    "'Noto Sans SC', 'Source Han Sans SC', sans-serif"
)
MONO = "'Cascadia Mono', 'SF Mono', Consolas, 'Courier New', monospace"

INK = "#0F172A"
INK2 = "#475569"
MUTED = "#94A3B8"
PAGE = "#F7F8FA"
LINE = "#E2E8F0"

TONES = {
    "green": {"fill": "#F0FDF4", "stroke": "#86EFAC", "strong": "#15803D", "head": "#166534"},
    "blue": {"fill": "#EFF6FF", "stroke": "#BFDBFE", "strong": "#1D4ED8", "head": "#1E40AF"},
    "violet": {"fill": "#F5F3FF", "stroke": "#DDD6FE", "strong": "#6D28D9", "head": "#5B21B6"},
    "cyan": {"fill": "#ECFEFF", "stroke": "#A5F3FC", "strong": "#0E7490", "head": "#155E75"},
    "amber": {"fill": "#FFFBEB", "stroke": "#FDE68A", "strong": "#B45309", "head": "#92400E"},
    "rose": {"fill": "#FFF1F2", "stroke": "#FECDD3", "strong": "#BE123C", "head": "#9F1239"},
    "slate": {"fill": "#F1F5F9", "stroke": "#CBD5E1", "strong": "#334155", "head": "#1E293B"},
}


def esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def char_w(ch: str, fs: float) -> float:
    o = ord(ch)
    if ch == " ":
        return fs * 0.30
    if o < 128:
        if ch in "ilI.,:;|'`":
            return fs * 0.28
        if ch in "mwMW":
            return fs * 0.86
        if ch.isupper():
            return fs * 0.64
        return fs * 0.55
    if ch in "，。、；：（）【】《》""''·—":
        return fs * 0.98
    return fs * 1.0


def text_w(s: str, fs: float) -> float:
    return sum(char_w(c, fs) for c in s)


def wrap(s: str, max_w: float, fs: float, max_lines: int = 2) -> list[str]:
    """按字符宽度换行，超出行数补省略号。中文无空格，逐字断行最稳。"""
    s = (s or "").strip()
    lines: list[str] = []
    cur = ""
    for ch in s:
        if text_w(cur + ch, fs) <= max_w:
            cur += ch
        else:
            if cur:
                lines.append(cur)
            cur = ch
            if len(lines) == max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(lines) == max_lines and text_w("".join(lines), fs) < text_w(s, fs) - 1:
        last = lines[-1]
        while last and text_w(last + "…", fs) > max_w:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines


@dataclass
class Node:
    id: str
    group: str
    x: float
    y: float
    w: float
    h: float
    title: str
    sub: str = ""
    tone: str = "slate"
    tag: str = ""
    badge: str = ""
    kind: str = "chip"  # chip / capsule / plain


@dataclass
class Group:
    id: str
    label: str
    x: float
    y: float
    w: float
    h: float
    tone: str = "slate"
    sub: str = ""
    solid: bool = False


@dataclass
class Edge:
    a: str
    b: str
    label: str = ""
    style: str = "solid"  # solid / dashed / dotted
    sa: str = "right"  # right / left / top / bottom
    sb: str = "left"
    color: str = ""
    lpos: float = 0.5
    way: tuple[float, float] | None = None


@dataclass
class Note:
    text: str
    tone: str = "amber"
    tag: str = "提示"


@dataclass
class Diagram:
    id: str
    kicker: str
    title: str
    subtitle: str
    height: float
    groups: list[Group] = field(default_factory=list)
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    footer: str = ""
    foot_h: float = 0
    top: float = 128
    legend: bool = True
    flow: list[str] = field(default_factory=list)

    def node(self, nid: str) -> Node:
        for n in self.nodes:
            if n.id == nid:
                return n
        raise KeyError(nid)


# ---------------- 布局助手 ----------------

def panel_items(
    group: Group,
    items: list[tuple[str, str]],
    *,
    cols: int = 2,
    chip_h: float = 56,
    gap: float = 10,
    pad: float = 18,
    top: float = 52,
    tone: str = "",
) -> list[Node]:
    """在面板里按网格铺内部组件。items 超过可用高度时自动压缩行距。"""
    inner_w = group.w - pad * 2
    chip_w = (inner_w - gap * (cols - 1)) / cols
    out: list[Node] = []
    for i, (title, sub) in enumerate(items):
        r, c = divmod(i, cols)
        out.append(
            Node(
                id=f"{group.id}__{i}",
                group=group.id,
                x=group.x + pad + c * (chip_w + gap),
                y=group.y + top + r * (chip_h + gap),
                w=chip_w,
                h=chip_h,
                title=title,
                sub=sub,
                tone=tone or group.tone,
            )
        )
    return out


def panel_height(n_items: int, cols: int = 2, chip_h: float = 56, gap: float = 10,
                 top: float = 52, pad_bottom: float = 16) -> float:
    rows = math.ceil(n_items / cols)
    return top + rows * chip_h + (rows - 1) * gap + pad_bottom


def grid_panels(
    specs: list[dict],
    *,
    x0: float = 40,
    y0: float = 132,
    panel_w: float = 752,
    col_gap: float = 44,
    row_gap: float = 26,
    cols: int = 2,
    top: float = 56,
    chip_h: float = 56,
    chip_gap: float = 10,
    pad: float = 18,
) -> tuple[list[Group], list[Node], float]:
    """specs: {id,label,sub,tone,items[(t,s)],cols?}。返回分组、节点与底部 y。"""
    groups: list[Group] = []
    nodes: list[Node] = []
    y = y0
    for i in range(0, len(specs), cols):
        row = specs[i : i + cols]
        heights = [
            panel_height(len(s["items"]), s.get("cols", 2), chip_h, chip_gap, top)
            for s in row
        ]
        row_h = max(heights)
        for j, s in enumerate(row):
            g = Group(
                id=s["id"],
                label=s["label"],
                x=x0 + j * (panel_w + col_gap),
                y=y,
                w=panel_w,
                h=row_h,
                tone=s.get("tone", "slate"),
                sub=s.get("sub", ""),
            )
            groups.append(g)
            nodes += panel_items(
                g, s["items"], cols=s.get("cols", 2), chip_h=chip_h,
                gap=chip_gap, pad=pad, top=top, tone=g.tone,
            )
        y += row_h + row_gap
    return groups, nodes, y


def band(
    *,
    id: str,
    label: str,
    sub: str,
    y: float,
    h: float,
    items: list[tuple[str, str]],
    tone: str,
    cols: int,
    x0: float = 40,
    w: float = 1560,
    chip_h: float = 62,
    gap: float = 10,
    label_w: float = 118,
) -> tuple[Group, list[Node]]:
    """总架构图的一层：左侧竖排层名 + 右侧组件横排。"""
    g = Group(id=id, label=label, x=x0, y=y, w=w, h=h, tone=tone, sub=sub)
    pad = 18
    area_x = x0 + label_w + 8
    area_w = w - label_w - pad * 2 - 8
    chip_w = (area_w - gap * (cols - 1)) / cols
    nodes: list[Node] = []
    rows = math.ceil(len(items) / cols)
    chip_hh = min(chip_h, (h - 44 - (rows - 1) * gap) / rows)
    for i, (t, s) in enumerate(items):
        r, c = divmod(i, cols)
        nodes.append(
            Node(
                id=f"{id}__{i}",
                group=id,
                x=area_x + c * (chip_w + gap),
                y=y + 40 + r * (chip_hh + gap),
                w=chip_w,
                h=chip_hh,
                title=t,
                sub=s,
                tone=tone,
            )
        )
    return g, nodes


# ---------------- SVG 绘制 ----------------

def _text_lines(x, y, lines, fs, fill, weight=400, anchor="start", lh=1.35, mono=False):
    out = []
    fam = MONO if mono else FONT
    for i, ln in enumerate(lines):
        out.append(
            f'<text x="{x:.1f}" y="{y + i * fs * lh:.1f}" font-family="{fam}" '
            f'font-size="{fs}" font-weight="{weight}" fill="{fill}" '
            f'text-anchor="{anchor}">{esc(ln)}</text>'
        )
    return "".join(out)


def draw_group(g: Group) -> str:
    t = TONES[g.tone]
    fill = "#FFFFFF" if not g.solid else t["fill"]
    head = (
        f'<rect x="{g.x + 0.5:.1f}" y="{g.y + 0.5:.1f}" width="{g.w - 1:.1f}" height="{g.h - 1:.1f}" '
        f'rx="16" fill="{fill}" stroke="{t["stroke"]}" stroke-width="1.1"/>'
    )
    bar = (
        f'<rect x="{g.x + 16:.1f}" y="{g.y + 18:.1f}" width="4" height="16" rx="2" fill="{t["strong"]}"/>'
    )
    label = _text_lines(g.x + 28, g.y + 26, [g.label], 14.5, t["head"], 700)
    sub = ""
    if g.sub:
        sub = _text_lines(g.x + 28, g.y + 45, [g.sub], 11, INK2, 400)
    return head + bar + label + sub


def draw_node(n: Node) -> str:
    if n.kind == "plain":
        return ""
    t = TONES[n.tone]
    rx = n.h / 2 if n.kind == "capsule" else 9
    body = (
        f'<rect x="{n.x + 0.5:.1f}" y="{n.y + 0.5:.1f}" width="{n.w - 1:.1f}" height="{n.h - 1:.1f}" '
        f'rx="{rx}" fill="{t["fill"]}" stroke="{t["stroke"]}" stroke-width="1"/>'
    )
    left = n.x + 14
    if n.badge:
        # 2026-11-26：原先把角标算进局部变量 badge 却从未拼进返回值，角标一直没被画出来。
        # 这里改为直接累加到 body，与下面 left 右移 18px 的排版意图一致。
        body += (
            f'<circle cx="{n.x + 17:.1f}" cy="{n.y + 17:.1f}" r="9" fill="{t["strong"]}" opacity="0.12"/>'
            + _text_lines(n.x + 17, n.y + 21, [n.badge], 10.5, t["strong"], 700, anchor="middle")
        )
        left = n.x + 32
    # 标题按可用宽度折行；有副标题时标题最多 1 行
    maxw = n.w - (left - n.x) - 14
    if n.tag:
        maxw -= max(38, text_w(n.tag, 10) + 14)
    title_lines = wrap(n.title, maxw, 12.5, 1 if n.sub else 2)
    body += _text_lines(left, n.y + (17 if n.sub else n.h / 2 + 5), title_lines, 12.5, t["head"], 700)
    if n.sub:
        tw = text_w(n.sub, 10.5)
        if tw <= n.w - (left - n.x) - 14 and "\n" not in n.sub:
            body += _text_lines(left, n.y + n.h - 13, [n.sub], 10.5, INK2, 400)
        else:
            body += _text_lines(
                left, n.y + 36, wrap(n.sub, n.w - (left - n.x) - 14, 10.5, 2), 10.5, INK2, 400
            )
    if n.tag:
        tx = n.x + n.w - 12
        body += (
            f'<rect x="{tx - text_w(n.tag, 10) - 12:.1f}" y="{n.y + 8:.1f}" '
            f'width="{text_w(n.tag, 10) + 16:.1f}" height="18" rx="9" fill="#FFFFFF" '
            f'stroke="{t["stroke"]}"/>'
        )
        body += _text_lines(tx - 4, n.y + 21, [n.tag], 10, t["strong"], 600, anchor="end")
    return body


def _anchor(n: Node, side: str) -> tuple[float, float]:
    if side == "right":
        return n.x + n.w, n.y + n.h / 2
    if side == "left":
        return n.x, n.y + n.h / 2
    if side == "top":
        return n.x + n.w / 2, n.y
    return n.x + n.w / 2, n.y + n.h


def draw_edge(e: Edge, d: Diagram) -> str:
    a, b = d.node(e.a), d.node(e.b)
    x1, y1 = _anchor(a, e.sa)
    x2, y2 = _anchor(b, e.sb)
    color = e.color or TONES[b.tone]["strong"]
    dash = {"solid": "", "dashed": ' stroke-dasharray="7 5"', "dotted": ' stroke-dasharray="3 4"'}[e.style]
    mark = f"m{b.tone}m"
    if e.way:
        wx, wy = e.way
        path = f"M{x1:.1f},{y1:.1f} L{wx:.1f},{y1:.1f} L{wx:.1f},{wy:.1f} L{x2:.1f},{wy:.1f} L{x2:.1f},{y2:.1f}"
        mx, my = (wx + x2) / 2, wy
    else:
        if e.sa in ("right", "left"):
            dx = (x2 - x1) * 0.45
            path = (
                f"M{x1:.1f},{y1:.1f} C{x1 + dx:.1f},{y1:.1f} "
                f"{x2 - dx:.1f},{y2:.1f} {x2:.1f},{y2:.1f}"
            )
        else:
            dy = (y2 - y1) * 0.45
            path = (
                f"M{x1:.1f},{y1:.1f} C{x1:.1f},{y1 + dy:.1f} "
                f"{x2:.1f},{y2 - dy:.1f} {x2:.1f},{y2:.1f}"
            )
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    out = (
        f'<path d="{path}" fill="none" stroke="{color}" stroke-width="1.6"{dash} '
        f'marker-end="url(#{mark})" opacity="0.9"/>'
    )
    if e.label:
        fs = 10.5
        lines = wrap(e.label, 190, fs, 2)
        bw = max(text_w(ln, fs) for ln in lines) + 14
        bh = 17 * len(lines) + 4
        lx = mx if e.way is None else mx
        ly = my - bh / 2 if not e.way else my - bh - 2
        out += (
            f'<rect x="{lx - bw / 2:.1f}" y="{ly:.1f}" width="{bw:.1f}" height="{bh:.1f}" '
            f'rx="6" fill="#FFFFFF" stroke="{LINE}" stroke-width="0.8" opacity="0.97"/>'
        )
        out += _text_lines(lx, ly + 13, lines, fs, INK2, 500, anchor="middle", lh=1.3)
    return out


def draw_legend(d: Diagram, y: float) -> str:
    x = 40
    out = ['<g opacity="0.95">']
    items = [
        ("solid", "调用 / 数据流", "#15803D"),
        ("dashed", "异步 / 事件 / 降级", "#94A3B8"),
        ("dotted", "可选 / 回退路径", "#94A3B8"),
    ]
    for style, label, color in items:
        dash = {"solid": "", "dashed": ' stroke-dasharray="7 5"', "dotted": ' stroke-dasharray="3 4"'}[style]
        out.append(
            f'<line x1="{x}" y1="{y}" x2="{x + 26}" y2="{y}" stroke="{color}" '
            f'stroke-width="1.6"{dash}/>'
        )
        out.append(_text_lines(x + 33, y + 4, [label], 10.5, INK2, 400))
        x += 34 + text_w(label, 10.5) + 26
    out.append("</g>")
    return "".join(out)


def draw_notes(d: Diagram, y: float) -> str:
    out: list[str] = []
    yy = y
    for n in d.notes:
        t = TONES[n.tone]
        lines = wrap(n.text, 1450, 11.5, 3)
        h = 18 * len(lines) + 12
        out.append(
            f'<rect x="40" y="{yy:.1f}" width="1560" height="{h:.1f}" rx="8" '
            f'fill="{t["fill"]}" stroke="{t["stroke"]}" stroke-width="0.9"/>'
            f'<rect x="40" y="{yy:.1f}" width="3.5" height="{h:.1f}" rx="1.7" fill="{t["strong"]}"/>'
        )
        out.append(_text_lines(58, yy + 16, [f"{n.tag}｜" + lines[0]] + lines[1:], 11.5, t["head"], 500, lh=1.32))
        yy += h + 8
    return "".join(out)


def notes_block_h(d: Diagram) -> float:
    return sum(18 * len(wrap(n.text, 1450, 11.5, 3)) + 20 for n in d.notes)


def flow_lane(
    *,
    id: str,
    label: str,
    sub: str,
    y: float,
    items: list[tuple[str, str]],
    tone: str,
    x0: float = 40,
    w: float = 1560,
    h: float = 146,
    gap: float = 14,
    chip_h: float = 74,
) -> tuple[Group, list[Node]]:
    """横向泳道：左侧无竖排标签，标题在左上，内部组件等宽横排。"""
    g = Group(id=id, label=label, x=x0, y=y, w=w, h=h, tone=tone, sub=sub)
    pad = 16
    n = len(items)
    cw = (w - pad * 2 - gap * (n - 1)) / n
    nodes: list[Node] = []
    for i, (t, s) in enumerate(items):
        nodes.append(
            Node(
                id=f"{id}__{i}",
                group=id,
                x=x0 + pad + i * (cw + gap),
                y=y + 56,
                w=cw,
                h=min(chip_h, h - 72),
                title=t,
                sub=s,
                tone=tone,
            )
        )
    return g, nodes


def draw_flow_strip(d: Diagram, y: float) -> str:
    """标题下方的横向数据流链：请求 → 校验 → …"""
    if not d.flow:
        return ""
    out: list[str] = []
    x = 56
    out.append(_text_lines(x, y + 15, ["数据流"], 10.5, MUTED, 600))
    x += 52
    for i, step in enumerate(d.flow):
        fs = 10.8
        w = text_w(step, fs) + 22
        if i:
            out.append(
                f'<path d="M{x - 17:.1f},{y + 11:.1f} L{x - 3:.1f},{y + 11:.1f}" stroke="#94A3B8" '
                f'stroke-width="1.3" marker-end="url(#mslatem)" opacity="0.85"/>'
            )
            x += 18
        out.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="22" rx="11" fill="#FFFFFF" '
            f'stroke="{LINE}" stroke-width="0.9"/>'
        )
        out.append(_text_lines(x + 11, y + 15, [step], fs, "#334155", 600))
        x += w
    return "".join(out)


def svg_head(d: Diagram) -> str:
    marks = []
    for tone, t in TONES.items():
        marks.append(
            f'<marker id="m{esc(tone)}m" viewBox="0 0 10 10" refX="9" refY="5" '
            f'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            f'<path d="M0,1 L9,5 L0,9 z" fill="{t["strong"]}"/></marker>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{CANVAS_W}" height="{d.height}" '
        f'viewBox="0 0 {CANVAS_W} {d.height}" role="img" aria-label="{esc(d.title)}">'
        f"<defs>{''.join(marks)}</defs>"
        f'<rect width="{CANVAS_W}" height="{d.height}" fill="{PAGE}"/>'
    )


def svg_title(d: Diagram) -> str:
    out = [
        '<rect x="40" y="30" width="4" height="40" rx="2" fill="#22C55E"/>',
        _text_lines(56, 46, [d.kicker], 11.5, "#15803D", 700),
        _text_lines(56, 82, [d.title], 27, INK, 700),
    ]
    if d.subtitle:
        out.append(_text_lines(56, 106, [d.subtitle], 12, INK2, 400))
    out.append(f'<line x1="40" y1="{d.top - 10:.1f}" x2="1600" y2="{d.top - 10:.1f}" stroke="{LINE}"/>')
    return "".join(out)


def render_svg(d: Diagram, standalone: bool = True) -> str:
    parts = [svg_head(d), svg_title(d), draw_flow_strip(d, d.top - 36)]
    for g in d.groups:
        parts.append(draw_group(g))
    for n in d.nodes:
        parts.append(draw_node(n))
    for e in d.edges:
        parts.append(draw_edge(e, d))
    nh = notes_block_h(d)
    if nh:
        parts.append(draw_notes(d, d.height - 40 - nh))
    if d.legend:
        parts.append(draw_legend(d, d.height - 26))
    parts.append("</svg>")
    return "".join(parts)


# ---------------- Mermaid / Graphviz 导出 ----------------

def _mermaid_text(s: str) -> str:
    return s.replace('"', "'").replace("[", "（").replace("]", "）")


def to_mermaid(d: Diagram) -> str:
    lines = [f"%% {d.title}", "flowchart TB"]
    tones_used = set()
    for g in d.groups:
        lines.append(f'  subgraph {g.id}["{g.label}"]')
        lines.append("    direction TB")
        for n in d.nodes:
            if n.group != g.id or n.kind == "plain":
                continue
            label = _mermaid_text(n.title)
            if n.sub:
                label += "<br/><small>" + _mermaid_text(n.sub) + "</small>"
            lines.append(f'    {n.id}["{label}"]')
        lines.append("  end")
        tones_used.add(g.tone)
    for e in d.edges:
        arrow = "-.->" if e.style == "dashed" else "-->"
        lab = f"|{_mermaid_text(e.label)}|" if e.label else ""
        lines.append(f"  {e.a} {arrow}{lab} {e.b}")
    lines.append("  classDef default fill:#F8FAFC,stroke:#CBD5E1,stroke-width:1px,color:#0F172A")
    for tone in sorted(tones_used):
        t = TONES[tone]
        lines.append(
            f"  classDef {tone} fill:{t['fill']},stroke:{t['stroke']},"
            f"stroke-width:1px,color:{t['head']}"
        )
    by_tone: dict[str, list[str]] = {}
    for n in d.nodes:
        by_tone.setdefault(n.tone, []).append(n.id)
    for tone, ids in by_tone.items():
        lines.append(f"  class {','.join(ids)} {tone}")
    return "\n".join(lines) + "\n"


def to_dot(d: Diagram) -> str:
    lines = [
        f'digraph "{d.id}" {{',
        "  graph [rankdir=TB, fontname=\"Microsoft YaHei\", bgcolor=\"#F7F8FA\", "
        "nodesep=0.28, ranksep=0.38, compound=true];",
        "  node [shape=box, style=\"rounded,filled\", fontname=\"Microsoft YaHei\", "
        "fontsize=11, margin=\"0.12,0.08\", penwidth=1.0];",
        "  edge [fontname=\"Microsoft YaHei\", fontsize=9, color=\"#64748B\", arrowsize=0.7];",
    ]
    for g in d.groups:
        lines.append(f'  subgraph cluster_{g.id} {{')
        lines.append(f'    label="{g.label}"; fontsize=13; fontcolor="{TONES[g.tone]["head"]}"; '
                     f'style="rounded,filled"; fillcolor="#FFFFFF"; color="{TONES[g.tone]["stroke"]}";')
        for n in d.nodes:
            if n.group != g.id or n.kind == "plain":
                continue
            t = TONES[n.tone]
            label = n.title + ("\\n" + n.sub if n.sub else "")
            lines.append(
                f'    {n.id} [label="{label}", fillcolor="{t["fill"]}", color="{t["stroke"]}", '
                f'fontcolor="{t["head"]}"];'
            )
        lines.append("  }")
    for e in d.edges:
        style = "dashed" if e.style in ("dashed", "dotted") else "solid"
        lab = f'label="{e.label}"' if e.label else ""
        lines.append(f'  {e.a} -> {e.b} [{lab} style={style} color="#64748B"];')
    lines.append("}")
    return "\n".join(lines) + "\n"
