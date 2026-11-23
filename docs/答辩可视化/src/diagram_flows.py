"""05 张核心业务流程图：主链路、知识入库、RAG 检索、会话记忆、工具调用。

版式：自上而下的横向泳道（每层一个阶段），层内按“左→右”表示步骤顺序，
层间箭头表示控制流；异常与降级路径写在脚注里（图中用虚线边表示可选/回退）。
"""

from __future__ import annotations

from dz import Diagram, Edge, Node, Note, flow_lane, notes_block_h

X0, W = 40, 1560
H = 130          # 单条泳道高度
GAP = 40         # 泳道间距
CHIP_H = 66
CHIP_GAP = 24


def _lane(d, gid, label, sub, tone, y, items):
    g, ns = flow_lane(
        id=gid, label=label, sub=sub, y=y, items=items, tone=tone,
        x0=X0, w=W, h=H, gap=CHIP_GAP, chip_h=CHIP_H,
    )
    d.groups.append(g)
    d.nodes.extend(ns)
    return ns


def _chain(d, ns):
    """层内横向串行（无标签，箭头已足够表意）。"""
    for i in range(len(ns) - 1):
        d.edges.append(Edge(a=ns[i].id, b=ns[i + 1].id, sa="right", sb="left"))


def _stage_link(d, from_ns, to_ns, label="", style="solid"):
    """层间连线的「阶段箭头」：在两条泳道中线上画一根竖直箭头。

    比「最后一张卡 → 第一张卡」的斜线更干净：斜线会扫过下层泳道的标题区，
    卡片越多越乱。层内顺序由横向箭头表达，层与层之间只需要一条控制流。
    """
    x = X0 + W / 2
    a = Node(id=f"sa_{from_ns[0].group}_{to_ns[0].group}", group=from_ns[0].group,
             x=x, y=from_ns[0].y - 56 + H, w=0, h=0, title="", kind="plain")
    b = Node(id=f"sb_{from_ns[0].group}_{to_ns[0].group}", group=to_ns[0].group,
             x=x, y=to_ns[0].y - 56, w=0, h=0, title="", kind="plain")
    d.nodes.extend([a, b])
    d.edges.append(Edge(a=a.id, b=b.id, label=label, style=style, sa="bottom", sb="top"))


def _finish(d: Diagram, y_end: float) -> Diagram:
    d.height = int(y_end + notes_block_h(d) + 62)
    return d


# ---------------- F1 主链路 ----------------

def flow_main() -> Diagram:
    d = Diagram(
        id="10-flow-main", kicker="FLOW 01 · MAIN PIPELINE",
        title="核心主链路：一次提问从 HTTP 到 SSE 收尾",
        subtitle="先校验并抢占资源，再选分支；检索只是增强项，任何环节失败都不阻断作答",
        height=0, top=136, legend=False,
    )
    y = 150

    n1 = _lane(d, "s1", "① 接入", "浏览器 → Nginx → Next → FastAPI", "green", y, [
        ("POST /api/chat/stream", "Nginx → Next rewrite → FastAPI"),
        ("ChatRequest 解析", "sid · question · use_kb / use_web"),
    ])
    _chain(d, n1)
    y += H + GAP

    n2 = _lane(d, "s2", "② 前置校验", "先抢资源，再发 200", "blue", y, [
        ("JWT 鉴权", "三处取 token · 失败 401"),
        ("会话归属", "sid 非本人 / 不存在 → 404"),
        ("每日 ask 配额", "60 次 / 日 · 超限 429"),
        ("LLM 并发闸", "16 并发 · 排队 20s"),
        ("返回 StreamingResponse", "200 之后不能再改状态码"),
    ])
    _chain(d, n2)
    y += H + GAP

    n3 = _lane(d, "s3", "③ 组装与选路", "历史重建 + 一次轻量 LLM", "violet", y, [
        ("assemble_history", "摘要块 + 最近 K 轮原文"),
        ("router.decide", "workflow / agent · 2s 超时"),
        ("执行计划", "mode 兼容 · 能力开关"),
    ])
    _chain(d, n3)
    y += H + GAP

    n4 = _lane(d, "s4", "④ 四分支执行", "同一出口：增量 token + 统一收尾", "amber", y, [
        ("vision", "有图优先 · qwen-vl"),
        ("hybrid", "工作流 × Agent"),
        ("agent", "ReAct · 四工具"),
        ("rag", "检索增强"),
        ("llm", "纯对话"),
    ])
    _chain(d, n4)
    y += H + GAP

    n5 = _lane(d, "s5", "⑤ 生成与工具", "DeepSeek 流式输出 · 工具结果回注", "amber", y, [
        ("模型流式输出", "token 帧逐段下发"),
        ("工具调用循环", "≤10 步 · 结果截断"),
        ("引用与依据", "sources / grounding 帧"),
    ])
    _chain(d, n5)
    y += H + GAP

    n6 = _lane(d, "s6", "⑥ 收尾", "成功失败都要走到", "slate", y, [
        ("落库 assistant", "content + refs + truncated"),
        ("标题 / 画像", "异步 · 失败不阻塞"),
        ("压缩调度", "两轮之间判定"),
        ("释放并发名额", "BackgroundTask 兜底"),
    ])
    _chain(d, n6)
    y += H

    _stage_link(d, n1, n2)
    _stage_link(d, n2, n3)
    _stage_link(d, n3, n4, "按计划")
    _stage_link(d, n4, n5, "分支输出")
    _stage_link(d, n5, n6)

    d.notes = [
        Note("关键结论：配额的扣减发生在「会话校验之后、选路之前」，并发名额在发出 200 之前抢占——"
             "因为 SSE 一旦开始就无法再改 HTTP 状态码，抢不到名额只能提前 429。", "green", "结论"),
        Note("异常与降级：router.decide 超时、返回非法枚举或有异常，一律回落 agent；混合编排零产出时再走一次纯 LLM，"
             "保证「总有答案」。检索失败、空库、弱依据都只影响引用质量，不阻断作答。", "amber", "降级"),
        Note("边界：客户端断连时半截答案不落库；释放并发名额放在 BackgroundTask，避免异常路径漏放导致闸门长期占用。", "rose", "风险"),
    ]
    return _finish(d, y)


# ---------------- F2 知识入库 ----------------

def flow_ingest() -> Diagram:
    d = Diagram(
        id="11-flow-ingest", kicker="FLOW 02 · KNOWLEDGE INGEST",
        title="知识库入库：从上传到可检索的五段流水线",
        subtitle="每库独立 collection；先落盘再解析，失败清理临时文件，状态对前端可见",
        height=0, top=136, legend=False,
    )
    y = 150

    n1 = _lane(d, "s1", "① 上传校验", "api/kb · 默认拒绝", "cyan", y, [
        ("POST /api/kb/{id}/upload", "multipart · owner 校验"),
        ("大小与类型", "≤100MB · txt / md / pdf"),
        ("流式落盘", "分块写 · 失败清理"),
    ])
    _chain(d, n1)
    y += H + GAP

    n2 = _lane(d, "s2", "② 解析与切片", "kb/rag_core", "cyan", y, [
        ("文本抽取", "PDF → 纯文本"),
        ("噪声清洗", "页眉页脚 / 空行"),
        ("切片", "500 字 · 重叠 50"),
        ("元数据", "topic / 文件名 / 序号"),
    ])
    _chain(d, n2)
    y += H + GAP

    n3 = _lane(d, "s3", "③ 向量化", "阿里百炼 · 外部 API", "amber", y, [
        ("批量 embedding", "batch 10 · 并发 4"),
        ("指纹守卫", "模型 / 维度 / host 一致"),
        ("日配额", "1000 片段 / 日"),
    ])
    _chain(d, n3)
    y += H + GAP

    n4 = _lane(d, "s4", "④ 写入向量库", "Chroma · 按库隔离", "violet", y, [
        ("collection", "kb_{kb_id}"),
        ("upsert", "id = 文件 + 序号"),
        ("文件清单", "原子写 · 支持单删"),
        ("重建", "删旧片段后重写"),
    ])
    _chain(d, n4)
    y += H + GAP

    n5 = _lane(d, "s5", "⑤ 对外可见", "入库完成之后", "slate", y, [
        ("状态流转", "processing → ready / failed"),
        ("检索可达", "kb_search 立即可查"),
        ("引用溯源", "来源文件 + 片段序号"),
    ])
    _chain(d, n5)
    y += H

    _stage_link(d, n1, n2)
    _stage_link(d, n2, n3)
    _stage_link(d, n3, n4)
    _stage_link(d, n4, n5)

    d.notes = [
        Note("关键结论：入库按库加锁、每库一个 collection，删除文件时只删该文件的片段而不重建整库；"
             "原始文件留档在 data/uploads，向量只是派生数据，可随时重建。", "cyan", "结论"),
        Note("异常与降级：格式不支持 → 400，超过 100MB → 413，embedding 配额耗尽 → 429；"
             "解析或向量化失败时文件标记 failed 并清理临时文件，不留下「半入库」状态。", "amber", "降级"),
        Note("风险：embedding 指纹（模型 / 维度 / host）不一致时直接拒绝重建，否则新旧向量空间混用会让相似度失去意义；"
             "这也是换模型必须整库重建的原因。", "rose", "风险"),
    ]
    return _finish(d, y)


# ---------------- F3 RAG 检索 ----------------

def flow_retrieval() -> Diagram:
    d = Diagram(
        id="12-flow-retrieval", kicker="FLOW 03 · RAG RETRIEVAL",
        title="RAG 检索：双通道召回、精排与依据判定",
        subtitle="扩召回 → 阈值 → 融合 → MMR → top-k；相似度低于 0.45 时主动承认「依据不足」",
        height=0, top=136, legend=False,
    )
    y = 150

    n1 = _lane(d, "s1", "① 查询入口", "工具调用与预检索共用", "green", y, [
        ("kb_search / 预检索", "owner 与 kb_id 由服务端注入"),
        ("embed_guard", "指纹校验 · 不一致即拒绝"),
        ("结果缓存", "key 含 owner · 防串结果"),
    ])
    _chain(d, n1)
    y += H + GAP

    n2 = _lane(d, "s2", "② 双通道召回", "向量 + 关键词", "blue", y, [
        ("向量扩召回", "fetch = 3×k · 下限 12"),
        ("BM25", "k1=1.5 · b=0.75"),
        ("RRF 融合", "k=60 · 免分数对齐"),
    ])
    _chain(d, n2)
    y += H + GAP

    n3 = _lane(d, "s3", "③ 精排与去冗", "kb/rag_core", "violet", y, [
        ("相似度阈值", "余弦 < 0.25 丢弃"),
        ("MMR 去重", "λ=0.65 · 字符 2-gram"),
        ("截断 top-k", "默认 3 · 上限 20"),
    ])
    _chain(d, n3)
    y += H + GAP

    n4 = _lane(d, "s4", "④ 依据判定", "决定「说不知道」还是「作答」", "amber", y, [
        ("相似度换算", "1 - distance / 2"),
        ("assess_grounding", "0.45 阈值分档"),
        ("三档结论", "strong / medium / weak"),
    ])
    _chain(d, n4)
    y += H + GAP

    n5 = _lane(d, "s5", "⑤ 注入与作答", "同一份片段，两种用法", "slate", y, [
        ("sources 帧", "来源 · 页码 · 预览"),
        ("grounding 提示", "弱依据强制声明"),
        ("模型作答", "[1][2] 角标可回原文"),
    ])
    _chain(d, n5)
    y += H

    _stage_link(d, n1, n2)
    _stage_link(d, n2, n3)
    _stage_link(d, n3, n4, "按最高分")
    _stage_link(d, n4, n5)

    d.notes = [
        Note("关键结论：扩召回解决「只取 top-3 会漏」，MMR 解决「三条引用里两条是同一段」，"
             "RRF 让向量分与 BM25 分不必换算到同一量纲——三段合起来才是一条完整检索链。", "blue", "结论"),
        Note("异常与降级：命中结果缓存则跳过 embedding；HYBRID_ENABLED=0 时退化为纯向量检索；"
             "空库或 Chroma 异常返回明确错误，由 Agent 如实告知，而不是静默给空答案。", "amber", "降级"),
        Note("答辩追问：为什么不做 rerank？当前库规模与延迟预算下，BM25+RRF+MMR 性价比更高；"
             "rerank 每次检索多一次模型往返，2C2G 单机扛不住，属于明确的「暂不做」。", "rose", "追问"),
    ]
    return _finish(d, y)


# ---------------- F4 会话与记忆 ----------------

def flow_memory() -> Diagram:
    d = Diagram(
        id="13-flow-memory", kicker="FLOW 04 · SESSION & MEMORY",
        title="会话与记忆：原文永不丢，摘要负责长期，窗口负责细节",
        subtitle="每轮从 SQLite 重建上下文；压缩只在两轮之间发生，失败保留原文",
        height=0, top=136, legend=False,
    )
    y = 150

    n1 = _lane(d, "s1", "① 会话定位", "core/session.py", "slate", y, [
        ("sid", "首轮为空则新建会话"),
        ("归属校验", "非本人 / 已删除 → 404"),
        ("窗口读取", "详情默认最近 10 轮"),
    ])
    _chain(d, n1)
    y += H + GAP

    n2 = _lane(d, "s2", "② 上下文装配", "assemble_history", "green", y, [
        ("摘要块", "长期记忆 · 置历史前部"),
        ("最近 K 轮", "默认 6 · 扩展 12"),
        ("占用判定", "按全量历史算阈值"),
        ("角色成对", "user / assistant 交替"),
    ])
    _chain(d, n2)
    y += H + GAP

    n3 = _lane(d, "s3", "③ 模型调用", "前缀稳定优先", "blue", y, [
        ("system prompt", "prompt_store active 版本"),
        ("动态定位", "末尾追加 · 不进 system"),
        ("历史消息", "原文 + 摘要一起送入"),
    ])
    _chain(d, n3)
    y += H + GAP

    n4 = _lane(d, "s4", "④ 落库与副作用", "成败都走到", "violet", y, [
        ("add_message", "assistant + refs + truncated"),
        ("标题", "首轮并行生成"),
        ("画像标签", "fire-and-forget"),
    ])
    _chain(d, n4)
    y += H + GAP

    n5 = _lane(d, "s5", "⑤ 压缩与长期记忆", "context_compress", "amber", y, [
        ("salience 打分", "信息密度 / 实体 / 离群"),
        ("抽取式摘要", "保留关键句 · 非生成"),
        ("安全阀", "失败改用截断"),
        ("commit_summary", "版本号 · 幂等写入"),
    ])
    _chain(d, n5)
    y += H

    _stage_link(d, n1, n2)
    _stage_link(d, n2, n3)
    _stage_link(d, n3, n4)
    _stage_link(d, n4, n5, "两轮之间", "dashed")

    d.notes = [
        Note("关键结论：Agent 故意不挂 LangGraph checkpointer——历史已由 SQLite 重建，两份状态同时用会重复累积"
             "（第 2 轮消息出现 2 次、第 3 轮 3 次），上下文平方级膨胀。", "green", "结论"),
        Note("异常与降级：摘要损坏则回退到原文窗口；压缩失败退化为硬截断；画像抽取失败静默，不影响主回答。"
             "会话被删除后所有读写一律 404，不做「猜 id」兜底。", "amber", "降级"),
        Note("风险：SQLite 单写者，web 与 worker 分库（sessions.db / scheduler.db）靠 WAL + 短事务；"
             "跨库联表查询曾是线上 500 的来源，答辩时需说明这条边界。", "rose", "风险"),
    ]
    return _finish(d, y)


# ---------------- F5 工具调用 ----------------

def flow_tools() -> Diagram:
    d = Diagram(
        id="14-flow-tools", kicker="FLOW 05 · TOOL CALLING",
        title="工具调用：模型只决定「用不用」，用谁的库由服务端决定",
        subtitle="元数据校验 → 安全守卫 → 执行 → 截断回流；写操作需审批，确定性失败同轮封禁",
        height=0, top=136, legend=False,
    )
    y = 150

    n1 = _lane(d, "s1", "① 模型决策", "ReAct 主循环", "amber", y, [
        ("判断是否用工具", "由问题与提示词决定"),
        ("工具可见性", "按 use_kb / use_web 绑定"),
        ("生成参数", "StructuredTool schema"),
    ])
    _chain(d, n1)
    y += H + GAP

    n2 = _lane(d, "s2", "② 元数据校验", "core/tool_registry.py", "blue", y, [
        ("ToolMeta", "category / retryable / cost"),
        ("激活状态", "未激活工具直接拒绝"),
        ("needs_approval", "写操作挂起等确认"),
        ("prompt_guard", "注入 / 越权指令识别"),
    ])
    _chain(d, n2)
    y += H + GAP

    n3 = _lane(d, "s3", "③ 安全守卫", "默认拒绝", "violet", y, [
        ("netsafe", "私网 / 回环地址拦截"),
        ("urltools", "下载许可清单"),
        ("参数校验", "类型 · 范围 · 长度"),
    ])
    _chain(d, n3)
    y += H + GAP

    n4 = _lane(d, "s4", "④ 执行工具", "当前 4 个", "green", y, [
        ("kb_search", "只读 · 归属库"),
        ("web_search", "只读 · 写下载白名单"),
        ("get_weather", "高德 + Open-Meteo"),
        ("download_file", "唯一写操作 · 需审批"),
    ])
    _chain(d, n4)
    y += H + GAP

    n5 = _lane(d, "s5", "⑤ 回流与终止", "结果要能被模型消化", "slate", y, [
        ("输出截断", "4000 字 · 超限落盘"),
        ("tool 帧", "发起 / 返回都上屏"),
        ("模型继续", "ReAct ≤10 步"),
        ("终止", "给出答案或说明截断"),
    ])
    _chain(d, n5)
    y += H

    _stage_link(d, n1, n2)
    _stage_link(d, n2, n3)
    _stage_link(d, n3, n4)
    _stage_link(d, n4, n5)

    d.notes = [
        Note("关键结论：工具可控性来自「六字段元数据 + 三层守卫」，而不是靠提示词自觉；"
             "kb_id / owner / 下载许可一律由服务端 configurable 注入，模型参数改不了真实目标。", "green", "结论"),
        Note("异常与降级：工具失败按 retryable 决定是否重试，确定性失败同轮封禁；"
             "审批超时则跳过并把原因回传模型；工具返回的错误文案要求「如实说明、不许编造」。", "amber", "降级"),
        Note("风险：recursion_limit=10 是轮次上限而非语义终止，复杂任务可能提前截断；"
             "这是已知取舍，答辩时不宜声称「保证任务完成」。", "rose", "风险"),
    ]
    return _finish(d, y)


def build_all() -> list[Diagram]:
    return [flow_main(), flow_ingest(), flow_retrieval(), flow_memory(), flow_tools()]
