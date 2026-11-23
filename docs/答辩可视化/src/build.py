"""构建全部图：SVG / PNG(3x≈300dpi) / Mermaid / Graphviz / 自包含 HTML 汇总页。"""

from __future__ import annotations

import base64
import html
import pathlib
import sys

from playwright.sync_api import sync_playwright

import dz
from diagram_overview import build as build_overview

ROOT = pathlib.Path(__file__).resolve().parent.parent
PNG_DIR = ROOT / "png"
SVG_DIR = ROOT / "svg"
MMD_DIR = ROOT / "src" / "mmd"
DOT_DIR = ROOT / "src" / "dot"
HTML_PATH = ROOT / "知穗Agent-系统架构图谱.html"

SECTIONS = [
    ("overview", "总架构", ["00-overview"]),
    ("modules", "模块细分（9 张）", [
        "01-frontend", "02-gateway", "03-chat", "04-rag", "05-agent",
        "06-hybrid", "07-memory", "08-scheduler", "09-ops",
    ]),
    ("flows", "核心业务流程（5 张）", [
        "10-flow-main", "11-flow-ingest", "12-flow-retrieval",
        "13-flow-memory", "14-flow-tools",
    ]),
]

# 每张图的「覆盖模块 / 关键结论 / 可能被追问」——HTML 页随图展示
META: dict[str, tuple[str, str, str]] = {
    "00-overview": (
        "七层 57 个组件：用户/前端、接入/网关、应用/编排、智能能力、数据存储、外部服务、部署运维",
        "浏览器只认一个域名：Nginx → Next → FastAPI 同源代理，省掉 CORS；向量库与检索流水线在进程内单例复用。",
        "单机 2C2G 的容量边界在哪：LLM 并发闸 16、进程内限流在 --workers N 下强度 ×N。",
    ),
    "01-frontend": (
        "app/ 9 个页面入口、components/ 8 个核心组件、lib/ 8 个客户端模块",
        "页面不直连 8000 端口，全部走 Next rewrite；SSE 的 token 帧是增量而不是全文，前端自行累加。",
        "为什么不用 WebSocket；断线重连与半截答案如何处理。",
    ),
    "02-gateway": (
        "main.py 装配、JWT 鉴权、限流与每日配额、LLM 并发闸、四道内容护栏、apilog 采集",
        "安全默认拒绝：归属校验失败返回 404 而非 403，避免 id 可枚举；429 有全局兜底。",
        "限流与并发闸是进程内状态，SQLite rate_buckets 只是近似共享，不是分布式精确限流。",
    ),
    "03-chat": (
        "请求前置、_plan_route 执行计划、五个执行分支、上下文组装、SSE 帧协议、收尾副作用",
        "先扣配额再选路；并发名额在发出 200 之前抢占——200 之后错误只能走 error 帧。",
        "为什么 rag 分支仍保留：它是「用户明确只要检索」的兼容口，不是 auto 默认路径。",
    ),
    "04-rag": (
        "入库链路、Chroma 按库隔离、混合检索、缓存与指纹守卫、依据判定、联网导入",
        "扩召回 → 阈值 → RRF 融合 → MMR 四段解决「引用重复、专有名词漏召回」；0.45 以下主动承认依据不足。",
        "为什么没有 rerank：每次检索多一次模型往返，当前 2C2G 预算下性价比不高。",
    ),
    "05-agent": (
        "create_agent 构建与编译缓存、4 个工具、ToolMeta 六字段注册表、执行守卫、ReAct 调用循环",
        "可控性来自「元数据 + 守卫」两层，而不是靠提示词自觉；kb_id / owner 由服务端注入，模型改不了目标。",
        "recursion_limit=10 是轮次上限而非语义终止，复杂任务可能提前截断。",
    ),
    "06-hybrid": (
        "五类工作流节点、HybridOrchestrator、Workflow/Agent 引擎、switch_rules 切换规则、人工介入与恢复",
        "路由只判一次，避免同一句话判两遍导致行为分叉；切换 5 次上限 + 30s 冷却抑制抖动。",
        "规则由用户配置，表达式走白名单 AST，但组合语义复杂度不受控，无法证明收敛。",
    ),
    "07-memory": (
        "SQLite 会话表、assemble_history 上下文装配、抽取式压缩、画像标签、账号与配额、会话生命周期",
        "原文永不丢；压缩只发生在两轮之间，失败保留原文；checkpointer 故意为 None，避免历史重复累积。",
        "SQLite 单写者与跨库查询边界：sessions.db / scheduler.db 是两份文件，联表曾是线上 500 来源。",
    ),
    "08-scheduler": (
        "独立 worker 进程、调度底座三表、七个定时任务、service 业务域、对外接口、配额与可观测",
        "幂等靠 UNIQUE(idem_key) + ON CONFLICT，而不是「先查再插」；心跳按年龄判 stalled / down。",
        "单 worker 是单点；多开会重复触发，APScheduler 的 JobStore 锁只能部分兜底。",
    ),
    "09-ops": (
        "Nginx 入口与 SSE 专用 location、systemd 三 unit、发布与回滚、加固脚本、Prometheus 监控、备份",
        "后端只监听 127.0.0.1，前端同源代理；SSE 必须关 proxy_buffering，否则流式退化为整段返回。",
        "备案前 IP 直连属过渡方案；单机无冗余，发布即短暂中断。",
    ),
    "10-flow-main": (
        "主链路六阶段：接入 → 前置校验 → 组装与选路 → 四分支执行 → 生成与工具 → 收尾",
        "任何增强项失败都不阻断作答；检索弱只影响引用质量，不阻断主回答。",
        "断连、429、路由降级、混合零产出四条异常路径是否都有明确出口。",
    ),
    "11-flow-ingest": (
        "入库五段：上传校验 → 解析切片 → 向量化 → 写 Chroma → 对外可见",
        "先落盘再解析，失败清理临时文件；每库一个 collection，删文件只删该文件片段。",
        "embedding 指纹不一致必须整库重建，否则新旧向量空间混用会让相似度失效。",
    ),
    "12-flow-retrieval": (
        "检索五段：查询入口 → 双通道召回 → 精排去冗 → 依据判定 → 注入作答",
        "向量扩召回补漏、RRF 免分数对齐、MMR 去重；三档依据结论驱动「说不知道」还是「作答」。",
        "缓存命中、HYBRID_ENABLED=0、空库、弱依据四种降级路径。",
    ),
    "13-flow-memory": (
        "会话五段：定位 → 上下文装配 → 模型调用 → 落库 → 压缩与长期记忆",
        "三层记忆：原文（永不丢）、摘要块（可降级）、最近 K 轮（必保留）；压缩阈值按全量历史计算。",
        "会话删除后一律 404；摘要损坏回退原文；压缩失败退化为截断。",
    ),
    "14-flow-tools": (
        "工具五段：模型决策 → 元数据校验 → 安全守卫 → 执行 → 截断回流",
        "模型只决定「用不用」，用谁的库由服务端 configurable 注入；写操作需审批，确定性失败同轮封禁。",
        "审批超时、工具失败、重复调用、recursion_limit 截断四种情况的收敛策略。",
    ),
}


def diagrams() -> list[dz.Diagram]:
    out = [build_overview()]
    from diagram_modules import build_all as build_modules
    out += build_modules()
    from diagram_flows import build_all as build_flows
    out += build_flows()
    return out


def render_png(pg, d: dz.Diagram) -> pathlib.Path:
    svg = dz.render_svg(d)
    b64 = base64.b64encode(svg.encode("utf-8")).decode()
    page_html = (
        "<html><body style='margin:0;background:#F7F8FA'>"
        f"<img src='data:image/svg+xml;base64,{b64}' width='{dz.CANVAS_W}' height='{d.height}' "
        "style='display:block'></body></html>"
    )
    png = PNG_DIR / f"{d.id}.png"
    pg.set_viewport_size({"width": dz.CANVAS_W, "height": int(d.height)})
    pg.set_content(page_html)
    pg.screenshot(path=str(png), omit_background=False)
    return png


def render_html(items: list[dz.Diagram]) -> pathlib.Path:
    by_id = {d.id: d for d in items}
    slides: list[str] = []
    nav: list[str] = []
    index = 0
    total = len(items)
    for sec_key, sec_name, ids in SECTIONS:
        nav.append(f'<div class="nav-sec">{html.escape(sec_name)}</div>')
        for did in ids:
            d = by_id[did]
            index += 1
            cov, concl, ask = META.get(did, ("", "", ""))
            nav.append(
                f'<a class="nav-item" href="#{did}" data-target="{did}">'
                f'<span class="num">{index:02d}</span>{html.escape(d.title.split("：")[0])}</a>'
            )
            svg = dz.render_svg(d)
            slides.append(f"""
    <section class="slide" id="{did}" data-id="{did}" data-sec="{sec_key}">
      <header class="head">
        <div class="kicker">{html.escape(d.kicker)}</div>
        <h2>{html.escape(d.title)}</h2>
        <p>{html.escape(d.subtitle)}<span class="hint">· 点击图表可放大</span></p>
      </header>
      <div class="figure">{svg}</div>
      <footer class="meta">
        <div class="cell"><b>覆盖模块</b><span>{html.escape(cov)}</span></div>
        <div class="cell"><b>关键结论</b><span>{html.escape(concl)}</span></div>
        <div class="cell warn"><b>可能被追问</b><span>{html.escape(ask)}</span></div>
      </footer>
      <div class="pager">{index:02d} / {total:02d}</div>
    </section>""")

    page = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>知穗 Agent · 系统架构图谱</title>
<style>
:root {{
  --ink:#0F172A; --ink2:#475569; --muted:#94A3B8; --line:#E2E8F0;
  --green:#22C55E; --green-d:#15803D; --bg:#F7F8FA; --card:#FFFFFF;
}}
* {{ box-sizing:border-box; }}
html,body {{ margin:0; height:100%; }}
body {{
  font-family:-apple-system,'Segoe UI','Microsoft YaHei','PingFang SC','Noto Sans SC',sans-serif;
  background:var(--bg); color:var(--ink); display:flex;
}}
#toc {{
  width:250px; flex:none; height:100vh; overflow:auto; padding:26px 18px 40px;
  background:var(--card); border-right:1px solid var(--line); position:sticky; top:0;
}}
#toc h1 {{ font-size:15px; margin:0 0 4px; letter-spacing:.5px; }}
#toc .sub {{ font-size:11px; color:var(--muted); margin-bottom:18px; line-height:1.6; }}
.nav-sec {{
  font-size:10.5px; font-weight:700; color:var(--muted); letter-spacing:1px;
  margin:18px 0 8px; padding-left:8px;
}}
.nav-item {{
  display:flex; align-items:center; gap:8px; padding:7px 8px; border-radius:8px;
  font-size:12px; color:var(--ink2); text-decoration:none; line-height:1.4;
  border-left:2px solid transparent;
}}
.nav-item:hover {{ background:#F1F5F9; color:var(--ink); }}
.nav-item.on {{ background:#F0FDF4; color:var(--green-d); border-left-color:var(--green); }}
.nav-item .num {{ font-size:10px; color:var(--muted); font-variant-numeric:tabular-nums; }}
#deck {{ flex:1; height:100vh; overflow-y:auto; scroll-behavior:smooth; }}
.slide {{
  position:relative; min-height:100vh; padding:26px 34px 18px; display:flex; flex-direction:column;
  border-bottom:1px solid var(--line); background:var(--bg);
}}
.head {{ flex:none; }}
.kicker {{ font-size:11px; font-weight:700; color:var(--green-d); letter-spacing:1.2px; }}
.head h2 {{ font-size:22px; margin:6px 0 4px; letter-spacing:.2px; }}
.head p {{ font-size:12px; color:var(--ink2); margin:0; }}
.head .hint {{ color:var(--muted); margin-left:8px; }}
.figure {{
  flex:1; min-height:0; display:flex; align-items:center; justify-content:center;
  margin:14px 0 10px; overflow:hidden;
}}
.figure svg {{
  max-width:100%; max-height:calc(100vh - 212px); width:auto; height:auto; cursor:zoom-in;
  border:1px solid var(--line); border-radius:14px; background:#F7F8FA;
  box-shadow:0 10px 30px rgba(15,23,42,.06);
}}
.figure.zoom {{ overflow:auto; align-items:flex-start; justify-content:flex-start; }}
.figure.zoom svg {{
  max-height:none; width:100%; height:auto; cursor:zoom-out;
}}
.meta {{
  flex:none; display:grid; grid-template-columns:repeat(3,1fr); gap:12px;
}}
.meta .cell {{
  background:var(--card); border:1px solid var(--line); border-left:3px solid var(--green);
  border-radius:8px; padding:10px 13px; font-size:12px; line-height:1.55; color:var(--ink2);
}}
.meta .cell b {{ display:block; font-size:10.5px; color:var(--green-d); letter-spacing:.6px; margin-bottom:3px; }}
.meta .cell.warn {{ border-left-color:#E11D48; }}
.meta .cell.warn b {{ color:#BE123C; }}
.pager {{
  position:absolute; right:34px; top:26px; font-size:11px; color:var(--muted);
  font-variant-numeric:tabular-nums;
}}
#hud {{
  position:fixed; right:34px; top:56px; display:flex; gap:8px; z-index:5;
}}
#hud button {{
  border:1px solid var(--line); background:rgba(255,255,255,.92); color:var(--ink2);
  font-size:12px; padding:7px 11px; border-radius:8px; cursor:pointer;
  font-family:inherit; box-shadow:0 2px 8px rgba(15,23,42,.06);
}}
#hud button:hover {{ color:var(--ink); border-color:#CBD5E1; }}
@media (max-width:900px) {{
  #toc {{ display:none; }}
  .meta {{ grid-template-columns:1fr; }}
}}
@media print {{
  #toc, #hud {{ display:none; }}
  body {{ display:block; }}
  #deck {{ height:auto; overflow:visible; }}
  .slide {{ min-height:auto; page-break-after:always; border:none; }}
  .figure svg {{ max-height:none; width:100%; box-shadow:none; }}
}}
</style>
</head>
<body>
<aside id="toc">
  <h1>知穗 Agent · 系统架构图谱</h1>
  <div class="sub">FastAPI + LangChain / LangGraph + Chroma + DeepSeek<br>答辩可视化材料 · 共 {total} 张图</div>
  {''.join(nav)}
</aside>
<main id="deck">{''.join(slides)}
</main>
<div id="hud">
  <button id="btn-prev">← 上一张</button>
  <button id="btn-next">下一张 →</button>
  <button id="btn-full">全屏</button>
</div>
<script>
(function () {{
  var deck = document.getElementById('deck');
  var slides = Array.prototype.slice.call(document.querySelectorAll('.slide'));
  var links = Array.prototype.slice.call(document.querySelectorAll('.nav-item'));
  var cur = 0;

  function go(i, smooth) {{
    cur = Math.max(0, Math.min(slides.length - 1, i));
    slides[cur].scrollIntoView({{ behavior: smooth === false ? 'auto' : 'smooth', block: 'start' }});
    markNav();
  }}
  function markNav() {{
    var id = slides[cur].id;
    links.forEach(function (a) {{ a.classList.toggle('on', a.dataset.target === id); }});
  }}
  links.forEach(function (a, i) {{
    a.addEventListener('click', function (e) {{
      e.preventDefault();
      var idx = slides.findIndex(function (s) {{ return s.id === a.dataset.target; }});
      if (idx >= 0) go(idx);
    }});
  }});
  document.getElementById('btn-prev').onclick = function () {{ go(cur - 1); }};
  document.getElementById('btn-next').onclick = function () {{ go(cur + 1); }};
  document.getElementById('btn-full').onclick = function () {{
    if (!document.fullscreenElement) document.documentElement.requestFullscreen();
    else document.exitFullscreen();
  }};
  document.addEventListener('keydown', function (e) {{
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown' || e.key === 'PageDown') {{
      e.preventDefault(); go(cur + 1);
    }} else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp' || e.key === 'PageUp') {{
      e.preventDefault(); go(cur - 1);
    }} else if (e.key === 'f' || e.key === 'F') {{
      document.getElementById('btn-full').onclick();
    }}
  }});
  deck.addEventListener('scroll', function () {{
    var best = 0, bestD = Infinity;
    slides.forEach(function (s, i) {{
      var d = Math.abs(s.getBoundingClientRect().top - 0);
      if (d < bestD) {{ bestD = d; best = i; }}
    }});
    if (best !== cur) {{ cur = best; markNav(); }}
  }}, {{ passive: true }});
  document.querySelectorAll('.figure').forEach(function (f) {{
    f.addEventListener('click', function () {{ f.classList.toggle('zoom'); }});
  }});
  markNav();
}})();
</script>
</body>
</html>
"""
    HTML_PATH.write_text(page, encoding="utf-8")
    return HTML_PATH


def main() -> None:
    for p in (PNG_DIR, SVG_DIR, MMD_DIR, DOT_DIR):
        p.mkdir(parents=True, exist_ok=True)
    items = diagrams()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": dz.CANVAS_W, "height": 1200}, device_scale_factor=3)
        for d in items:
            svg = dz.render_svg(d)
            (SVG_DIR / f"{d.id}.svg").write_text(svg, encoding="utf-8")
            (MMD_DIR / f"{d.id}.mmd").write_text(dz.to_mermaid(d), encoding="utf-8")
            (DOT_DIR / f"{d.id}.dot").write_text(dz.to_dot(d), encoding="utf-8")
            png = render_png(page, d)
            print(f"{d.id:22s} {int(d.height):4d}px  {png.stat().st_size // 1024:5d} KB")
        browser.close()
    out = render_html(items)
    print(f"共 {len(items)} 张图 · HTML: {out}")


if __name__ == "__main__":
    sys.exit(main())
