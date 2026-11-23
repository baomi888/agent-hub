"""00 总架构图：七层分层 + 关键调用/数据流。"""

from dz import CANVAS_W, Diagram, Edge, Group, Node, band

W = CANVAS_W
X0, BW = 40, 1560

BANDS = [
    dict(
        id="L1", label="用户与前端", sub="浏览器 · Next.js 16", tone="green",
        items=[
            ("对话工作台", "ChatArea · SSE 增量渲染"),
            ("四屏控制台", "委托 / 总结 / 打卡 / 设置"),
            ("知识与模板", "上传 · 引用 · 模板广场"),
            ("学习专项", "四六级 · 关卡式路径"),
            ("登录与会话", "AuthGate · HttpOnly Cookie"),
            ("开发者看板", "用量 · 反馈 · 调度健康"),
            ("H5 与移动端", "100dvh · 明暗双主题"),
        ],
    ),
    dict(
        id="L2", label="接入与网关", sub="Nginx → Next → FastAPI", tone="blue",
        items=[
            ("Nginx 反向代理", "80/443 · SSE 关闭缓冲"),
            ("Next 服务", "3000 · /api/* rewrite"),
            ("FastAPI 入口", "127.0.0.1:8000 · 尾斜杠归一"),
            ("鉴权", "JWT HS256 · 三处取 token"),
            ("限流与每日配额", "10 次/分 · ask 60/日"),
            ("LLM 并发闸", "16 并发 · 排队 20s"),
            ("采集与指标", "apilog 3s 落库 · Prometheus"),
        ],
    ),
    dict(
        id="L3", label="应用与编排", sub="FastAPI 路由 · 21 个模块", tone="violet",
        items=[
            ("对话主链路", "/api/chat/stream · 四分支"),
            ("会话与知识库", "/api/sessions · /api/kb"),
            ("混合执行", "/api/hybrid · 工作流×Agent"),
            ("调度与日常", "delegate · report · schedule"),
            ("学习专项", "cet · path · checkin · profile"),
            ("进化与看板", "prompt-evo · admin · metrics"),
            ("SSE 事件协议", "status/token/sources/done"),
        ],
    ),
    dict(
        id="L4", label="智能能力", sub="路由 · Agent · RAG · 上下文", tone="amber",
        items=[
            ("执行前路由", "一次轻量 LLM 判 workflow/agent"),
            ("混合编排", "节点引擎 · 规则切换 · 人工介入"),
            ("Agent 构建", "create_agent · 编译图缓存"),
            ("四个工具", "检索 / 联网 / 天气 / 下载"),
            ("检索流水线", "扩召回→BM25+RRF→MMR→top-k"),
            ("依据判定", "相似度 0.45 以下示弱"),
            ("上下文工程", "摘要块 + 最近 K 轮原文"),
            ("Prompt 版本库", "active / promote / rollback"),
        ],
    ),
]

SIDE = [
    dict(
        id="L5", label="数据与存储", sub="SQLite × 3 + Chroma", tone="slate",
        items=[
            ("会话库", "WAL · conversations/messages"),
            ("调度库", "jobs / job_runs / 心跳"),
            ("账号与配额", "accounts · usage · token 吊销"),
            ("Chroma 向量库", "每库一个 collection"),
            ("文档留档", "uploads · 文件清单"),
            ("画像与提示词", "user_tags · prompt_variants"),
        ],
    ),
    dict(
        id="L6", label="外部服务与工具", sub="模型 · 搜索 · 地图", tone="rose",
        items=[
            ("DeepSeek", "deepseek-chat 对话"),
            ("百炼 Embedding", "text-embedding-v3"),
            ("百炼视觉", "qwen-vl-plus 看图"),
            ("Serper", "联网搜索 + 候选预览"),
            ("高德 / Open-Meteo", "天气双源 + 逆地理"),
            ("网页抓取", "SSRF 白名单 · 下载许可"),
        ],
    ),
]

DEPLOY = dict(
    id="L7", label="部署与运维", sub="单机 2C2G · 三进程", tone="cyan",
    items=[
        ("systemd 三进程", "backend / frontend / worker"),
        ("APScheduler worker", "Asia/Shanghai · 7 个任务"),
        ("发布脚本", "deploy.sh · standalone 产物"),
        ("监控告警", "Prometheus + node-exporter"),
        ("健康探针", "/health · probe-health.sh"),
        ("备份与恢复", "SQLite + Chroma + 上传件"),
        ("HTTPS 与加固", "certbot · harden-server.sh"),
    ],
)

Y0, BAND_H, GAP = 144, 126, 40
SIDE_H = 186


def build() -> Diagram:
    groups: list[Group] = []
    nodes: list[Node] = []
    gb: dict[str, Group] = {}
    y = Y0
    for spec in BANDS:
        g, ns = band(
            id=spec["id"], label=spec["label"], sub=spec["sub"], y=y, h=BAND_H,
            items=spec["items"], tone=spec["tone"], cols=len(spec["items"]),
            x0=X0, w=BW, chip_h=66, gap=9, label_w=132,
        )
        groups.append(g)
        gb[spec["id"]] = g
        nodes += ns
        y += BAND_H + GAP

    side_w = (BW - GAP) / 2
    for i, spec in enumerate(SIDE):
        g, ns = band(
            id=spec["id"], label=spec["label"], sub=spec["sub"], y=y, h=SIDE_H,
            items=spec["items"], tone=spec["tone"], cols=2,
            x0=X0 + i * (side_w + GAP), w=side_w, chip_h=54, gap=9, label_w=132,
        )
        groups.append(g)
        gb[spec["id"]] = g
        nodes += ns
    y += SIDE_H + GAP

    g, ns = band(
        id=DEPLOY["id"], label=DEPLOY["label"], sub=DEPLOY["sub"], y=y, h=BAND_H,
        items=DEPLOY["items"], tone=DEPLOY["tone"], cols=len(DEPLOY["items"]),
        x0=X0, w=BW, chip_h=66, gap=9, label_w=132,
    )
    groups.append(g)
    gb[DEPLOY["id"]] = g
    nodes += ns

    # 存储层 / 外部服务层的「入口锚点」：0 尺寸节点，只用于让跨层箭头落在层顶，
    # 避免竖线穿过同列的其他卡片。
    anchors = [
        Node(id="anchor_l5", group="L5", x=gb["L5"].x + gb["L5"].w * 0.55, y=gb["L5"].y,
             w=0, h=0, title="", kind="plain"),
        Node(id="anchor_l6", group="L6", x=gb["L6"].x + gb["L6"].w * 0.55, y=gb["L6"].y,
             w=0, h=0, title="", kind="plain"),
    ]
    nodes += anchors

    d = Diagram(
        id="00-overview",
        kicker="ZHISUI AGENT · SYSTEM OVERVIEW",
        title="知穗 Agent 总架构：七层分层与主调用链",
        subtitle="FastAPI + LangChain/LangGraph + Chroma + DeepSeek｜前后端同源代理，进程内单例复用向量库",
        height=int(y + BAND_H + 64),
        groups=groups,
        nodes=nodes,
        top=140,
    )

    def find(title: str) -> Node:
        for n in nodes:
            if n.title == title:
                return n
        raise KeyError(title)

    # 目标既可以是真实卡片，也可以是层顶锚点
    def down(a: str, b: Node, label: str, style: str = "solid"):
        na = find(a)
        xb = b.x + b.w / 2
        d.edges.append(
            Edge(a=na.id, b=b.id, label=label, style=style,
                 sa="bottom", sb="top", way=(xb, b.y - 18))
        )

    down("对话工作台", find("Nginx 反向代理"), "HTTPS / SSE")
    down("FastAPI 入口", find("对话主链路"), "HTTP / JSON")
    down("对话主链路", find("执行前路由"), "选路")
    down("检索流水线", anchors[0], "向量 + 元数据", "dashed")
    down("四个工具", anchors[1], "HTTPS 调用", "dashed")

    d.notes = []
    return d
