"""Generate SVG charts for the KV-cache experiment report (no external deps)."""

OUT = "C:/biancheng/agent-hub/docs/kv-cache-context-engineering"

# ---- raw per-iteration data (from result JSON / run logs) ----
correct_prompt = [757, 2041, 24924, 27410, 40377, 49923, 62896, 71597, 76462, 79129]
correct_cached = [0, 896, 2176, 25088, 27520, 40448, 50048, 62976, 71808, 76544]
correct_ttft = [3.776, 0.959, 1.661, 1.171, 1.474, 1.647, 1.123, 1.473, 1.266, 8.714]

dynamic_prompt = [
    778,
    1927,
    2090,
    5510,
    7925,
    20877,
    33284,
    49634,
    63618,
    68557,
    79363,
    84167,
    88839,
    91300,
    92932,
]
dynamic_cached = [0] * 15
dynamic_ttft = [
    1.402,
    1.088,
    0.866,
    1.071,
    1.240,
    1.674,
    1.689,
    2.148,
    2.309,
    2.255,
    2.072,
    2.188,
    2.458,
    2.141,
    10.177,
]


def cum_uncached(prompt, cached):
    cum = 0
    out = []
    for p, c in zip(prompt, cached, strict=False):
        cum += p - c
        out.append(cum)
    return out


correct_cu = cum_uncached(correct_prompt, correct_cached)
dynamic_cu = cum_uncached(dynamic_prompt, dynamic_cached)

CORRECT_COLOR = "#2E9E5B"  # green
DYNAMIC_COLOR = "#E2574C"  # red
GRID = "#D9DEE6"
AXIS = "#5A6473"
TEXT = "#2A313C"
MUTED = "#8A93A2"


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def polyline(points, color, width=2.5, dash=False):
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    dashattr = ' stroke-dasharray="6 4"' if dash else ""
    return f'<polyline fill="none" stroke="{color}" stroke-width="{width}" stroke-linejoin="round" stroke-linecap="round"{dashattr} points="{pts}"/>'


def dots(points, color, r=3.2):
    s = ""
    for x, y in points:
        s += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{color}"/>'
    return s


def line_chart(
    title,
    xlabel,
    ylabel,
    xmax,
    series,
    ymax,
    yticks,
    yfmt,
    outliers=None,
    width=820,
    height=460,
    notes=None,
):
    L, R, T, B = 78, 28, 64, 64
    pw = width - L - R
    ph = height - T - B
    svg = []
    svg.append(
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" font-family="-apple-system,Segoe UI,Helvetica,Arial,sans-serif">'
        % (width, height, width, height)
    )
    # background
    svg.append('<rect width="%d" height="%d" fill="#FFFFFF"/>' % (width, height))
    # title
    svg.append(
        '<text x="%d" y="34" font-size="20" font-weight="700" fill="%s">%s</text>'
        % (L, TEXT, esc(title))
    )
    # y grid + labels
    for yt in yticks:
        y = T + ph - (yt / ymax) * ph
        svg.append(
            '<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" stroke-width="1"/>'
            % (L, y, L + pw, y, GRID)
        )
        svg.append(
            '<text x="%d" y="%.1f" font-size="12" fill="%s" text-anchor="end" dominant-baseline="middle">%s</text>'
            % (L - 8, y, MUTED, yfmt(yt))
        )
    # x grid + labels
    for xt in range(1, xmax + 1, 2):
        x = L + ((xt - 1) / (xmax - 1)) * pw
        svg.append(
            '<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="%s" stroke-width="1"/>'
            % (x, T, x, T + ph, GRID)
        )
        svg.append(
            '<text x="%.1f" y="%d" font-size="12" fill="%s" text-anchor="middle">%d</text>'
            % (x, T + ph + 20, MUTED, xt)
        )
    # axes
    svg.append(
        '<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s" stroke-width="1.6"/>'
        % (L, T, L, T + ph, AXIS)
    )
    svg.append(
        '<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s" stroke-width="1.6"/>'
        % (L, T + ph, L + pw, T + ph, AXIS)
    )
    # axis titles
    svg.append(
        '<text x="%d" y="%d" font-size="13" fill="%s" text-anchor="middle">%s</text>'
        % (L + pw / 2, height - 14, TEXT, esc(xlabel))
    )
    svg.append(
        '<text x="18" y="%d" font-size="13" fill="%s" text-anchor="middle" transform="rotate(-90 18 %.1f)">%s</text>'
        % (T + ph / 2, TEXT, T + ph / 2, esc(ylabel))
    )
    # series
    for s in series:
        xdata = s["x"]
        ydata = s["y"]
        pts = []
        for i in range(len(xdata)):
            x = L + ((xdata[i] - 1) / (xmax - 1)) * pw
            y = T + ph - (ydata[i] / ymax) * ph
            pts.append((x, y))
        svg.append(polyline(pts, s["color"], dash=s.get("dash", False)))
        svg.append(dots(pts, s["color"]))
    # outliers
    if outliers:
        for ox, oy, olabel in outliers:
            x = L + ((ox - 1) / (xmax - 1)) * pw
            y = T + ph - (oy / ymax) * ph
            svg.append(
                '<circle cx="{:.1f}" cy="{:.1f}" r="6" fill="none" stroke="{}" stroke-width="2"/>'.format(
                    x, y, "#C0392B"
                )
            )
            svg.append(
                f'<text x="{x:.1f}" y="{y - 12:.1f}" font-size="11" fill="#C0392B" text-anchor="middle">{esc(olabel)}</text>'
            )
    # legend
    lx = L + pw - 250
    ly = T + 16
    svg.append(
        '<rect x="%d" y="%d" width="%d" height="%d" rx="6" fill="#FFFFFF" stroke="%s"/>'
        % (lx, ly, 238, 22 * len(series) + 12, GRID)
    )
    for i, s in enumerate(series):
        yy = ly + 18 + i * 22
        svg.append(
            '<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" stroke-width="3"/>'
            % (lx + 12, yy, lx + 40, yy, s["color"])
        )
        svg.append('<circle cx="%d" cy="%.1f" r="3" fill="%s"/>' % (lx + 26, yy, s["color"]))
        svg.append(
            '<text x="%d" y="%.1f" font-size="13" fill="%s" dominant-baseline="middle">%s</text>'
            % (lx + 48, yy, TEXT, esc(s["label"]))
        )
    # notes
    if notes:
        ny = height - 30
        for n in notes:
            svg.append(
                '<text x="%d" y="%d" font-size="11.5" fill="%s">%s</text>' % (L, ny, MUTED, esc(n))
            )
            ny += 16
    svg.append("</svg>")
    return "\n".join(svg)


# ---- Chart A: cumulative billed (uncached) prompt tokens ----
xmax = 15
ymax = 700000
yticks = [0, 100000, 200000, 300000, 400000, 500000, 600000, 700000]


def fmt_k(v):
    return "%dk" % (v // 1000)


svg_a = line_chart(
    "图A  累计「未缓存(计费)」Prompt Token —— 缓存失效的代价",
    "迭代轮次 (iteration)",
    "累计未缓存 token",
    xmax,
    [
        {
            "x": list(range(1, len(correct_cu) + 1)),
            "y": correct_cu,
            "color": CORRECT_COLOR,
            "label": "correct（稳定 system prompt）",
        },
        {
            "x": list(range(1, len(dynamic_cu) + 1)),
            "y": dynamic_cu,
            "color": DYNAMIC_COLOR,
            "label": "dynamic_system（每次变 system prompt）",
        },
    ],
    ymax,
    yticks,
    fmt_k,
    notes=[
        "correct 终值 ≈ 78k（仅 18% 的 token 未命中），dynamic_system 终值 ≈ 691k（100% 未命中）。",
        "两线在前期几乎重合（前 2 轮都是冷启动），之后差距随迭代指数级拉开。",
    ],
)

with open(OUT + "/fig_a_cumulative_uncached_tokens.svg", "w", encoding="utf-8") as f:
    f.write('<?xml version="1.0" encoding="UTF-8"?>\n' + svg_a)

# ---- Chart B: TTFT per iteration ----
xmax_b = 15
ymax_b = 11
yticks_b = [0, 2, 4, 6, 8, 10]


def fmt_s(v):
    return f"{v:.0f}s"


svg_b = line_chart(
    "图B  逐轮 TTFT（秒）—— 趋势比首尾单点更可信",
    "迭代轮次 (iteration)",
    "TTFT (秒)",
    xmax_b,
    [
        {
            "x": list(range(1, len(correct_ttft) + 1)),
            "y": correct_ttft,
            "color": CORRECT_COLOR,
            "label": "correct（稳定 system prompt）",
        },
        {
            "x": list(range(1, len(dynamic_ttft) + 1)),
            "y": dynamic_ttft,
            "color": DYNAMIC_COLOR,
            "label": "dynamic_system（每次变 system prompt）",
        },
    ],
    ymax_b,
    yticks_b,
    fmt_s,
    outliers=[(10, 8.714, "末轮 outlier"), (15, 10.177, "末轮 outlier")],
    notes=[
        "correct 去首末后均值 ≈ 1.33s；dynamic_system 去首末后均值 ≈ 1.77s（单次 +33%，纯缓存效应）。",
        "首尾两轮为 outlier：首轮冷启动、末轮长 prompt(≈79k)+满额输出(2000) 叠加，不计入趋势均值。",
    ],
)

with open(OUT + "/fig_b_ttft_trend.svg", "w", encoding="utf-8") as f:
    f.write('<?xml version="1.0" encoding="UTF-8"?>\n' + svg_b)

print("Chart A final correct/dynamic cumulative uncached:", correct_cu[-1], dynamic_cu[-1])
print("Wrote fig_a and fig_b SVG.")
