"""09 张模块细分图：前端、网关、对话主链路、知识检索、Agent 工具、混合编排、
会话记忆、调度业务、部署运维。"""

from __future__ import annotations

from dz import Diagram, Edge, Note, grid_panels, notes_block_h

# 每张模块图标题下方的「数据流」胶囊链（4~6 步，一屏可读）
FLOWS: dict[str, list[str]] = {
    "01-frontend": ["用户操作", "组件状态", "lib/api.ts", "Next rewrite", "FastAPI", "SSE 增量渲染"],
    "02-gateway": ["HTTP 请求", "尾斜杠归一", "JWT 鉴权", "限流 / 配额", "并发闸", "路由处理"],
    "03-chat": ["请求", "会话 / 扣配额", "并发闸", "执行前路由", "四分支执行", "SSE 收尾落库"],
    "04-rag": ["上传", "解析切片", "向量化入库", "扩召回", "BM25+RRF+MMR", "依据判定"],
    "05-agent": ["模型决策", "元数据校验", "审批 / 封禁", "执行工具", "截断回流", "继续推理"],
    "06-hybrid": ["定义校验", "段执行", "信号采集", "规则匹配", "切换 / 人工", "结果落库"],
    "07-memory": ["sid 定位", "装配上下文", "模型调用", "落库原文", "压缩摘要", "画像聚合"],
    "08-scheduler": ["cron 到点", "幂等登记", "任务执行", "结果回写", "重试 / 告警", "心跳"],
    "09-ops": ["浏览器", "Nginx", "Next 3000", "uvicorn 8000", "SQLite / Chroma", "监控与备份"],
}


def mk(did, kicker, title, subtitle, specs, notes, *, top=None, cols=2) -> Diagram:
    flow = FLOWS.get(did, [])
    top = top if top is not None else (160 if flow else 132)
    groups, nodes, end_y = grid_panels(specs, y0=top, cols=cols)
    d = Diagram(
        id=did, kicker=kicker, title=title, subtitle=subtitle,
        height=0, groups=groups, nodes=nodes, top=top, notes=notes, flow=flow,
    )
    d.height = int(end_y + notes_block_h(d) + 62)
    return d


def link(d: Diagram, a: str, b: str, label: str = "", style: str = "solid") -> None:
    d.edges.append(Edge(a=a, b=b, label=label, style=style, sa="bottom", sb="top",
                        way=(d.node(b).x + d.node(b).w / 2, d.node(b).y - 12)))


def build_all() -> list[Diagram]:
    out: list[Diagram] = []

    # ---------- M1 前端与交互 ----------
    out.append(
        mk(
            "01-frontend", "MODULE 01 · FRONTEND",
            "前端与交互层：Next.js 四屏控制台 + SSE 增量渲染",
            "页面只经 lib/api.ts 与 lib/sse.ts 访问后端；同源 rewrite 省掉 CORS，所有契约集中在 types.ts",
            [
                dict(id="p1", label="页面路由 · app/", sub="App Router · 9 个页面入口", tone="green",
                     items=[
                         ("对话工作台", "首页 · ChatArea · 侧栏"),
                         ("四屏控制台", "委托 / 总结 / 打卡 / 设置"),
                         ("知识库页", "上传 · 文件 · 重建"),
                         ("委托与总结", "delegate · report"),
                         ("学习专项", "cet · path 关卡"),
                         ("执行轨迹", "flow · 混合执行回放"),
                         ("开发者看板", "dashboard · 用量排行"),
                         ("模板广场", "templates · 场景模板"),
                     ]),
                dict(id="p2", label="组件层 · components/", sub="8 个核心组件 · 受控状态", tone="green",
                     items=[
                         ("ChatArea", "消息流 · 输入 · 引用角标"),
                         ("Sidebar", "会话列表 · 新建 / 切换"),
                         ("KnowledgePanel", "文件清单 · 入库状态"),
                         ("HybridTrace", "节点时间轴 · 切换提示"),
                         ("FeedbackCard", "赞踩 · 一键反馈"),
                         ("UsagePanel", "配额与用量"),
                         ("AuthGate", "未登录拦截 · 登录态"),
                         ("FilePreviewModal", "原文片段预览"),
                     ]),
                dict(id="p3", label="客户端库 · lib/", sub="请求 / SSE / 契约 / 降级", tone="slate",
                     items=[
                         ("api.ts", "统一请求 · 错误中文化"),
                         ("sse.ts", "逐帧解析 · 增量累加"),
                         ("types.ts", "前后端契约单一来源"),
                         ("useHybridTrace", "轨迹状态机"),
                         ("geo.ts", "定位三级降级"),
                         ("errors.ts", "网络 / 429 / 401 文案"),
                         ("consolePrefs", "偏好本地存储"),
                         ("chunkProfiles", "切片参数预设"),
                     ]),
                dict(id="p4", label="交互与数据流", sub="用户可见行为", tone="slate",
                     items=[
                         ("增量渲染", "token 拼接 · 不整段替换"),
                         ("引用溯源", "[1] 点击回原文"),
                         ("能力开关", "use_kb / use_web"),
                         ("图片理解", "附件上传 → vision"),
                         ("会话隔离", "sid 级历史与知识库"),
                         ("依据提示", "弱依据横幅前置"),
                     ]),
            ],
            [
                Note("关键结论：前端不直连后端端口，全部走 Next rewrite 同源代理；SSE 的 token 帧是增量而非全文，"
                     "前端自行累加——这是流式体验与「卡十几秒后整段出现」的分水岭。", "green", "结论"),
                Note("答辩追问：为什么不用 WebSocket？答：问答是单向、按轮次结束，SSE 足够且能复用 HTTP 鉴权与 Nginx；"
                     "代价是不能中途双向干预，因此人工切换走独立的 /api/hybrid/runs/{id}/switch。", "amber", "追问"),
            ],
        )
    )

    # ---------- M2 接入与安全 ----------
    out.append(
        mk(
            "02-gateway", "MODULE 02 · GATEWAY & SECURITY",
            "接入与安全网关：默认要登录、四道配额、一道并发闸",
            "main.py 只做装配；鉴权、限流、配额、并发闸、护栏、采集各自独立成模块，异常统一映射为 401/403/429",
            [
                dict(id="p1", label="进程入口 · main.py", sub="FastAPI 装配层", tone="blue",
                     items=[
                         ("FastAPI 实例", "redirect_slashes=False"),
                         ("尾斜杠归一", "进路由前校正 /api/*"),
                         ("CORS", "仅本地开发可开"),
                         ("启动预热", "Chroma / 路由 LLM / 建表"),
                         ("健康检查", "/health · 默认不回版本"),
                         ("默认参数", "/api/config/defaults"),
                     ]),
                dict(id="p2", label="身份与鉴权 · core/auth.py", sub="JWT + HttpOnly Cookie", tone="blue",
                     items=[
                         ("注册 / 登录", "用户名密码 · 管理员名保留"),
                         ("JWT 签发", "HS256 · uid / jti / exp"),
                         ("Cookie 写入", "baomi_session · SameSite=Lax"),
                         ("token 三取一", "Header → Bearer → Cookie"),
                         ("get_current_user", "校验签名与过期 · 401"),
                         ("令牌吊销", "revoked_tokens 表"),
                     ]),
                dict(id="p3", label="限流与配额 · core/quota.py", sub="按 uid · 按日", tone="blue",
                     items=[
                         ("每分钟限流", "滑动窗口 10 次 / 分"),
                         ("全局兜底桶", "rate_buckets 防单机打爆"),
                         ("ask 配额", "60 次 / 日"),
                         ("embed 配额", "1000 片段 / 日"),
                         ("auto 配额", "120 次 / 日（定时任务）"),
                         ("upload 配额", "2000 MB / 日"),
                     ]),
                dict(id="p4", label="并发闸 · core/llm_gate.py", sub="保护最贵的共享资源", tone="blue",
                     items=[
                         ("前台信号量", "16 并发 · 可配"),
                         ("排队超时", "20s 拿不到名额即 429"),
                         ("Busy 映射", "Retry-After 头"),
                         ("后台任务槽", "定时任务独立额度"),
                         ("管理员豁免", "配额与限流均跳过"),
                         ("快照", "当前占用 / 等待数"),
                     ]),
                dict(id="p5", label="内容护栏", sub="四道边界 · 默认拒绝", tone="slate",
                     items=[
                         ("prompt_guard", "注入 / 越权指令识别"),
                         ("tool_guard", "审批 · 截断 · 封禁"),
                         ("netsafe", "私网 / 回环 SSRF 拦截"),
                         ("urltools", "下载许可清单"),
                         ("输出上限", "超长落盘只回预览"),
                         ("归属校验", "非本人资源按 404"),
                     ]),
                dict(id="p6", label="采集与可观测", sub="准实时 ≤3 秒", tone="slate",
                     items=[
                         ("apilog 中间件", "路由模板归一 · 流式计时"),
                         ("3 秒落库", "后台 flush 循环"),
                         ("Prometheus", "计数 / 配额拒绝 / 缓存"),
                         ("开发者看板", "用量 · 慢接口 · 反馈"),
                         ("审计字段", "uid · 状态码 · 耗时"),
                         ("异常兜底", "采集失败不影响请求"),
                     ]),
            ],
            [
                Note("关键结论：安全是「默认拒绝」——除认证路由外，其余 router 在文件级挂 get_current_user，"
                     "新增端点忘写鉴权也仍然安全；归属校验失败返回 404 而非 403，避免 id 可枚举。", "blue", "结论"),
                Note("设计缺陷（会被追问）：限流与并发闸都是进程内状态。uvicorn 起 N 个 worker 时，"
                     "实际放行是 N 倍；项目用 SQLite rate_buckets 做了共享兜底，但仍是近似限流，不是分布式精确限流。", "rose", "风险"),
            ],
        )
    )

    # ---------- M3 对话主链路 ----------
    out.append(
        mk(
            "03-chat", "MODULE 03 · CHAT PIPELINE",
            "对话主链路：一次请求的四个分支与一条 SSE 流",
            "api/chat.py 是唯一入口：先扣配额再选路；检索只是增强项，任何失败都不阻断作答",
            [
                dict(id="p1", label="请求前置", sub="校验 → 扣费 → 归属", tone="violet",
                     items=[
                         ("ChatRequest", "use_kb / use_web / plan / location"),
                         ("会话校验", "sid 归属 · 不存在 404"),
                         ("扣问答额度", "consume(ask)"),
                         ("知识库归属", "非本人库按未绑定"),
                         ("图片分支", "有图直接进 vision"),
                         ("计划主题", "追问时从历史续主题"),
                     ]),
                dict(id="p2", label="执行计划 · _plan_route", sub="兼容旧 mode，新契约优先", tone="violet",
                     items=[
                         ("旧 mode 映射", "rag / llm / agent / hybrid"),
                         ("router.decide", "轻量 LLM · 2s 超时"),
                         ("workflow 判定", "命中且开关开启 → hybrid"),
                         ("无工具退化", "use_kb/use_web 都关 → llm"),
                         ("库不存在", "rag → agent"),
                         ("失败兜底", "超时 / 坏格式 → agent"),
                     ]),
                dict(id="p3", label="四个执行分支", sub="同一出口：增量文本 + 收尾", tone="violet",
                     items=[
                         ("hybrid", "工作流 × Agent · 可人工切换"),
                         ("agent", "ReAct 循环 · 四工具"),
                         ("rag", "改写 → 检索 → 拼参考资料"),
                         ("llm", "纯对话 · 压缩历史"),
                         ("vision", "qwen-vl 看图 · 可叠加检索"),
                         ("兜底", "混合零产出 → 纯 LLM 再答"),
                     ]),
                dict(id="p4", label="上下文组装 · 请求路径", sub="前缀稳定是硬约束", tone="slate",
                     items=[
                         ("系统提示词", "prompt_store active 版本"),
                         ("摘要块", "历史压缩后的长期记忆"),
                         ("最近 K 轮", "原文保留 · 细节不失真"),
                         ("预检索片段", "拼进本轮用户消息"),
                         ("定位", "追加末尾 · 不进 system"),
                         ("缓存友好", "动态内容不破坏前缀"),
                     ]),
                dict(id="p5", label="SSE 帧协议", sub="前端按 event 分发", tone="slate",
                     items=[
                         ("status", "进度提示 · 中文"),
                         ("token", "增量文本"),
                         ("tool", "工具发起 / 返回"),
                         ("sources", "引用卡片 + 相似度"),
                         ("grounding", "依据强弱 · 正文前"),
                         ("title / done", "标题 · message_id · truncated"),
                         ("error", "翻译后错误 · 不吐栈"),
                     ]),
                dict(id="p6", label="收尾与副作用", sub="成败都要走到", tone="slate",
                     items=[
                         ("落库 assistant", "refs / truncated 一并记"),
                         ("标题并行", "首轮即算 · 收尾零等待"),
                         ("画像标签", "fire-and-forget"),
                         ("压缩调度", "两轮之间判定"),
                         ("释放并发名额", "BackgroundTask 兜底"),
                         ("断连保护", "半截答案不落库"),
                     ]),
            ],
            [
                Note("关键结论：配额在「会话校验之后、选路之前」扣减；并发名额在进入 SSE 之前抢，抢不到直接 429 —— "
                     "因为一旦发出 200，SSE 就再没有机会改状态码。", "violet", "结论"),
                Note("答辩追问：为什么 rag 分支仍保留？答：它是「用户明确只要检索」的兼容口，不是 auto 的默认路径；"
                     "auto 走的是 agent + 预检索，模型仍可补调 kb_search。", "amber", "追问"),
            ],
        )
    )

    # ---------- M4 知识检索 ----------
    out.append(
        mk(
            "04-rag", "MODULE 04 · KNOWLEDGE & RETRIEVAL",
            "知识与检索：四段流水线 + 双通道召回 + 依据判定",
            "入库按库加锁、向量库每库一个 collection；检索先扩召回再去重，最后用相似度阈值决定「说不准」",
            [
                dict(id="p1", label="入库链路 · api/kb + kb/rag_core", sub="上传 / 导入 / 重建", tone="cyan",
                     items=[
                         ("流式落盘", "大小预检 · 分块写 · 失败清理"),
                         ("文档解析", "txt / md / pdf"),
                         ("切片", "500 字 · 重叠 50"),
                         ("主题标签", "入库时注入 topic 元数据"),
                         ("批量向量化", "batch 10 · 并发 4"),
                         ("重建 / 删文件", "删 collection 片段后重写"),
                     ]),
                dict(id="p2", label="存储与隔离 · kb/pipelines", sub="进程级单例 + 按库锁", tone="cyan",
                     items=[
                         ("Chroma 客户端", "进程级单例 · 持久化目录"),
                         ("collection 命名", "kb_{kb_id}"),
                         ("多库隔离", "每库独立 collection"),
                         ("原始留档", "data/uploads 保原文"),
                         ("文件清单", "原子写 · 支持单文件删"),
                         ("归属校验", "owner_of · 跨用户拒绝"),
                     ]),
                dict(id="p3", label="混合检索 · retrieve_with_score", sub="先召回后精排", tone="cyan",
                     items=[
                         ("向量扩召回", "3 倍 · 下限 12 条"),
                         ("BM25 通道", "k1=1.5 · b=0.75"),
                         ("RRF 融合", "k=60 · 无需分数对齐"),
                         ("阈值过滤", "余弦 < 0.25 丢弃"),
                         ("MMR 去重", "λ=0.65 · 字符 2-gram"),
                         ("截断 top-k", "前端可调 · 上限 20"),
                     ]),
                dict(id="p4", label="缓存与守卫", sub="正确性优先于速度", tone="slate",
                     items=[
                         ("embed 指纹", "模型 / 维度 / host 不一致即拒绝"),
                         ("查询向量缓存", "省一次外部 embedding"),
                         ("检索结果缓存", "key 含 owner · 防串结果"),
                         ("画像偏置", "标签命中重排 · 不改距离"),
                         ("相似度换算", "1 - distance/2"),
                         ("空库 / 异常", "明确报错 · 不静默"),
                     ]),
                dict(id="p5", label="依据判定与输出", sub="把「不知道」说出口", tone="slate",
                     items=[
                         ("assess_grounding", "最高相似度分档"),
                         ("0.45 阈值", "低于即判弱依据"),
                         ("strong / medium / weak", "三档 · 前端提示条"),
                         ("grounding_note", "拼进提示词约束"),
                         ("sources 帧", "来源 · 页码 · 预览"),
                         ("refs 文本", "[1][2] 角标可回原文"),
                     ]),
                dict(id="p6", label="联网导入 · kb/importer", sub="搜索 → 抓取 → 入库", tone="slate",
                     items=[
                         ("Serper 预览", "候选 URL 供勾选"),
                         ("URL 抓取", "超时 / 大小 / 类型限制"),
                         ("正文清洗", "去导航 · 保留主体"),
                         ("批量导入", "逐条失败不拖垮整批"),
                         ("下载代理", "/api/kb/download"),
                         ("SSRF 白名单", "私网地址直接拒"),
                     ]),
            ],
            [
                Note("关键结论：检索不是简单 top-k —— 扩召回 → 阈值 → MMR → top-k 四段，"
                     "解决「三条引用里两条是同一段」；混合检索补专有名词 / 缩写 / 编号的召回短板。", "cyan", "结论"),
                Note("答辩追问：为什么没有 rerank 模型？答：当前库规模与延迟预算下，BM25+RRF+MMR 的性价比更高；"
                     "rerank 会再增加一次模型往返，且 2C2G 机器扛不住，属于明确的「暂不做」。", "amber", "追问"),
            ],
        )
    )

    # ---------- M5 Agent 与工具 ----------
    out.append(
        mk(
            "05-agent", "MODULE 05 · AGENT & TOOLS",
            "Agent 与工具：四个工具、六字段元数据、三层守卫",
            "模型只能决定「用不用」，不能决定「用谁的库」——kb_id / owner 一律由服务端 configurable 注入",
            [
                dict(id="p1", label="构建 · agent/builder.py", sub="编译一次 · 按参数缓存", tone="amber",
                     items=[
                         ("create_agent", "LangChain v1 统一入口"),
                         ("system prompt", "prompt_store active + 三态库说明"),
                         ("场景预设", "考研 / 笔记 / 论文"),
                         ("编译图缓存", "kb×web×场景×激活×版本"),
                         ("checkpointer=None", "历史由 SQLite 重建"),
                         ("模型参数", "temperature 0.1"),
                     ]),
                dict(id="p2", label="工具集 · agent/tools.py", sub="4 个工具 · 各自降级", tone="amber",
                     items=[
                         ("kb_search", "只读 · 确定性失败不重试"),
                         ("web_search", "只读 · 可重试 · 写下载白名单"),
                         ("get_weather", "高德 + Open-Meteo 双源"),
                         ("download_file", "唯一写操作 · 需审批"),
                         ("参数 schema", "由 StructuredTool 生成"),
                         ("降级文案", "无 key / 空库 / 超时都明说"),
                     ]),
                dict(id="p3", label="注册表 · core/tool_registry.py", sub="导入即校验", tone="slate",
                     items=[
                         ("category", "read / write / compute"),
                         ("defer", "是否延迟发现"),
                         ("retryable", "确定性失败不进重试"),
                         ("needs_approval", "执行前挂起等确认"),
                         ("max_output_chars", "超限落盘回预览"),
                         ("cost_hint", "cheap / normal / expensive"),
                         ("playbook 四问", "何时用 / 不用 / 参数 / 失败"),
                         ("按用户激活", "延迟工具集进程内隔离"),
                     ]),
                dict(id="p4", label="执行守卫 · core/tool_guard.py", sub="审批 · 截断 · 封禁", tone="slate",
                     items=[
                         ("审批令牌", "同名同参才可确认"),
                         ("输出截断", "4000 字上限 · 落盘"),
                         ("确定性失败封禁", "同轮不再重试"),
                         ("重试策略", "仅 retryable 工具"),
                         ("写操作串行", "避免并发改外部状态"),
                         ("拒绝即回传", "模型看到原因再换路"),
                     ]),
                dict(id="p5", label="调用循环", sub="LangGraph ReAct · 有硬上限", tone="amber",
                     items=[
                         ("astream 双模式", "updates + messages"),
                         ("recursion_limit", "10 步 · 防原地重试"),
                         ("tool 帧", "发起 / 返回都上屏"),
                         ("configurable 注入", "owner / kb / 下载许可"),
                         ("安全边界", "模型参数改不了真实目标"),
                         ("失败收敛", "异常 → error 帧，不吞"),
                     ]),
            ],
            [
                Note("关键结论：工具的可控性来自「元数据 + 守卫」两层，而不是靠提示词自觉；"
                     "裁剪工具（原 5 个减为 4 个）是把算术交回模型，减少一次工具往返。", "amber", "结论"),
                Note("答辩追问：Agent 会死循环吗？答：recursion_limit=10 且确定性失败封禁；"
                     "但这是「轮次上限」，不是语义终止，复杂任务仍可能提前截断——属于已知取舍。", "rose", "风险"),
            ],
        )
    )

    # ---------- M6 混合编排 ----------
    out.append(
        mk(
            "06-hybrid", "MODULE 06 · HYBRID ORCHESTRATION",
            "混合编排：工作流可预期，Agent 可应变，规则决定何时切换",
            "hybrid/ 是独立子系统：定义 → 编排 → 引擎 → 切换规则 → 持久化，全部围绕 run_id 可回放",
            [
                dict(id="p1", label="工作流定义 · hybrid/workflow.py", sub="JSON 图 · 先校验后执行", tone="violet",
                     items=[
                         ("节点五类", "llm / tool / agent / condition / human"),
                         ("条件边", "表达式求值后选边"),
                         ("validate_definition", "唯一 id · 入口 · 失败跳转"),
                         ("内置流程", "builtin_plan_execute"),
                         ("节点超时", "默认 120s"),
                         ("失败收敛", "跳到 fallback 或终止"),
                     ]),
                dict(id="p2", label="编排器 · orchestrator.py", sub="段执行 + 切换决策", tone="violet",
                     items=[
                         ("HybridOrchestrator", "stream 产出统一事件"),
                         ("段执行", "workflow 段 / agent 段"),
                         ("SwitchEngine", "signal → 条件 → 动作"),
                         ("切换上限", "5 次 · 防抖动"),
                         ("awaiting_human", "挂起等人工"),
                         ("run_id", "前端轨迹 / 切换寻址"),
                     ]),
                dict(id="p3", label="执行引擎", sub="预算与兜底", tone="slate",
                     items=[
                         ("WorkflowEngine", "节点调度 · 上下文传递"),
                         ("AgentEngine", "10 步 / 300s / 8000 token"),
                         ("LLM 兜底", "零产出才触发"),
                         ("答案选取", "末个成功节点 output_key"),
                         ("exec_logs", "每节点进出留痕"),
                         ("状态机", "failed / timeout / done"),
                     ]),
                dict(id="p4", label="切换规则 · switcher.py", sub="用户可配 · 有冷却", tone="slate",
                     items=[
                         ("switch_rules", "signal / condition / action"),
                         ("优先级", "高优先规则先命中"),
                         ("方向冷却", "默认 30s"),
                         ("表达式求值", "白名单 AST · 非 eval"),
                         ("默认规则", "内置种子规则"),
                         ("越权拦截", "规则按 owner 隔离"),
                     ]),
                dict(id="p5", label="人工介入与恢复", sub="可解释、可接管", tone="violet",
                     items=[
                         ("/runs/{id}/switch", "人工指定下一段"),
                         ("/resume", "恢复 human 节点"),
                         ("快照落库", "跨进程可恢复"),
                         ("前端时间轴", "节点 / 状态 / 切换"),
                         ("权限校验", "run 归属 owner"),
                         ("审计", "谁在何时改了什么"),
                     ]),
            ],
            [
                Note("关键结论：路由只判「一句话要不要多步」，命中才进混合；混合内部再决定起跑是 workflow 还是 agent，"
                     "避免同一句话判两遍导致行为分叉。", "violet", "结论"),
                Note("设计缺陷：切换规则由用户配置，表达式虽走白名单 AST，但规则组合的语义复杂度不受控；"
                     "冷却只能缓解抖动，无法证明收敛——答辩时不宜声称「保证最优路径」。", "rose", "风险"),
            ],
        )
    )

    # ---------- M7 会话与记忆 ----------
    out.append(
        mk(
            "07-memory", "MODULE 07 · SESSION & MEMORY",
            "会话与记忆：SQLite 落原文，摘要块管长期，标签喂检索",
            "原始对话永不失真；压缩只发生在「两轮之间」，且失败保留原文——记忆是可降级的增强项",
            [
                dict(id="p1", label="会话存储 · core/session.py", sub="SQLite · WAL", tone="slate",
                     items=[
                         ("conversations", "owner / title / kb_id / 更新时间"),
                         ("messages", "role / content / refs / truncated"),
                         ("feedbacks", "赞踩 · 按 message_id"),
                         ("owner 隔离", "查询即带 owner 条件"),
                         ("窗口读取", "详情默认最近 10 轮"),
                         ("整轮删除", "user + 后续 assistant"),
                     ]),
                dict(id="p2", label="上下文装配", sub="assemble_history", tone="green",
                     items=[
                         ("摘要块", "长期记忆 · 置于历史前部"),
                         ("最近 K 轮", "默认 6 · 扩展 12"),
                         ("触发阈值", "字符占比达阈值才压"),
                         ("全量判定", "按未截断历史算占用"),
                         ("角色规范", "user / assistant 成对"),
                         ("降级", "摘要损坏回退原文"),
                     ]),
                dict(id="p3", label="压缩与摘要 · context_compress", sub="抽取式 + 安全阀", tone="green",
                     items=[
                         ("salience 打分", "信息密度 / 离群 / 实体"),
                         ("抽取式压缩", "保留关键句 · 非生成式"),
                         ("实体校验", "摘要必须覆盖原文实体"),
                         ("安全阀", "压缩失败改用截断"),
                         ("commit_summary", "版本号 · 幂等写入"),
                         ("可追溯", "摘要与原文双存"),
                     ]),
                dict(id="p4", label="画像与标签", sub="标签钩子 → 聚合 → 偏置", tone="green",
                     items=[
                         ("user_tags", "对话后抽标签 · 权重累加"),
                         ("user_tag_picks", "手选标签优先级更高"),
                         ("profile 快照", "每日 02:30 聚合"),
                         ("检索偏置", "命中标签的片段上浮"),
                         ("词表约束", "30 条定稿标签"),
                         ("失败静默", "画像不影响主回答"),
                     ]),
                dict(id="p5", label="账号与配额存储", sub="与业务同库不同表", tone="slate",
                     items=[
                         ("accounts", "用户名 / 密码散列 / 状态"),
                         ("usage", "按 uid × day × kind 累加"),
                         ("revoked_tokens", "登出即吊销"),
                         ("每日快照", "前端用量面板"),
                         ("管理员判定", "UID 优先 · 用户名兼容"),
                         ("参数化查询", "无字符串拼接 SQL"),
                     ]),
                dict(id="p6", label="会话生命周期", sub="前端可见的六个动作", tone="slate",
                     items=[
                         ("首轮标题", "并行生成 · 失败保默认"),
                         ("重命名", "PATCH / 会话"),
                         ("绑定 / 解绑库", "kb_id 可置空"),
                         ("切换会话", "独立历史与上下文"),
                         ("赞踩", "落到 feedbacks"),
                         ("删除会话", "级联清消息"),
                     ]),
            ],
            [
                Note("关键结论：记忆分三层——原文（SQLite，永不丢）、摘要块（长期，可降级）、最近 K 轮（细节，必保留）；"
                     "压缩阈值按全量历史算，避免「窗口截断后永远不触发压缩」的陷阱。", "green", "结论"),
                Note("风险：SQLite 是单写者，worker 与 web 共用同一文件时靠 WAL + 短事务；"
                     "跨库查表曾是线上 500 的来源（会话库 / 调度库是两个文件），答辩时需说明边界。", "rose", "风险"),
            ],
        )
    )

    # ---------- M8 调度与业务域 ----------
    out.append(
        mk(
            "08-scheduler", "MODULE 08 · SCHEDULER & DOMAINS",
            "调度与业务域：独立 worker、七个定时任务、幂等与心跳",
            "web 进程不 import apscheduler；worker 调用的 service 函数与手动端点完全同一条代码路径",
            [
                dict(id="p1", label="独立 worker · worker/scheduler.py", sub="进程隔离", tone="cyan",
                     items=[
                         ("APScheduler", "BlockingScheduler · 3.x"),
                         ("时区", "Asia/Shanghai"),
                         ("与 web 解耦", "web 只写「待执行」行"),
                         ("心跳", "worker_heartbeat 周期写"),
                         ("优雅退出", "停止调度 · 收尾当前任务"),
                         ("单实例假设", "多 worker 会重复触发"),
                     ]),
                dict(id="p2", label="调度底座 · core/scheduler.py", sub="三张表 · 一个唯一索引", tone="cyan",
                     items=[
                         ("schedule_jobs", "job_key / cron / enabled"),
                         ("job_runs", "planned_at / status / attempts"),
                         ("幂等键", "job_key|subject|planned_at"),
                         ("ON CONFLICT", "重复登记直接跳过"),
                         ("心跳三态", "up / stalled / down"),
                         ("重试", "失败按 attempts 再投"),
                     ]),
                dict(id="p3", label="七个任务", sub="全部可配 cron", tone="cyan",
                     items=[
                         ("delegate.dispatch", "每 5 分钟扫描到点委托"),
                         ("delegate.retry", "错峰 7/22/37/52 分"),
                         ("report.daily", "默认 23:30 生成总结"),
                         ("report.backfill", "04:00 补 7 天内失败"),
                         ("housekeeping", "04:15 清 90 天记录"),
                         ("profile.aggregate", "02:30 聚合画像"),
                         ("prompt.evolve", "每 6 小时一轮候选"),
                     ]),
                dict(id="p4", label="业务服务 · service/", sub="纯函数核心 · LLM 可注入", tone="slate",
                     items=[
                         ("daily", "每日委托 + 每日总结"),
                         ("cet", "四六级计划 / 词汇 / 模考"),
                         ("path", "关卡式学习路径"),
                         ("checkin", "打卡记录与连续天数"),
                         ("prompt_evo", "GEPA 候选 · 评估 · 回滚"),
                         ("templates", "模板目录 · 一次起点"),
                     ]),
                dict(id="p5", label="对外接口", sub="手动触发与定时同源", tone="slate",
                     items=[
                         ("/api/delegate", "7 条 · 委托 CRUD / runs"),
                         ("/api/report", "5 条 · 总结生成 / 查询"),
                         ("/api/schedule", "5 条 · health / run-now"),
                         ("/api/cet", "计划 · 任务 · 词汇"),
                         ("/api/path", "节点进度 · 解锁"),
                         ("/api/checkin", "打卡 · 统计"),
                     ]),
                dict(id="p6", label="配额与可观测", sub="无人值守也要有护栏", tone="slate",
                     items=[
                         ("auto 配额", "120 次 / 日 · 独立账本"),
                         ("tokens_out", "每次运行记 token"),
                         ("失败告警", "心跳 + 连续失败"),
                         ("run-now", "管理员手动补跑"),
                         ("运行历史", "按 job_key 分页"),
                         ("跳过原因", "幂等 / 额度 / 禁用"),
                     ]),
            ],
            [
                Note("关键结论：定时任务曾经是「配额敞口」——无人值守烧 token 不计入任何限额；"
                     "现在单独记 auto 配额，并用心跳年龄判活，而不是只看进程在不在。", "cyan", "结论"),
                Note("风险：单 worker 是单点；多开 worker 会重复触发（APScheduler 的 JobStore 锁只能部分兜底）。"
                     "当前在 2C2G 单机上够用，但不是可水平扩展的设计。", "rose", "风险"),
            ],
        )
    )

    # ---------- M9 部署运维 ----------
    out.append(
        mk(
            "09-ops", "MODULE 09 · DEPLOYMENT & OPS",
            "部署与运维：Nginx → Next → uvicorn，三进程 + 监控 + 备份",
            "单机 2C2G 的务实方案：后端只监听回环，前端同源代理；发布脚本一条命令，失败可回滚到上一版",
            [
                dict(id="p1", label="入口链路", sub="浏览器只看见一个域名", tone="blue",
                     items=[
                         ("Nginx", "80 / 443 · server_name"),
                         ("SSE 专用 location", "proxy_buffering off"),
                         ("上传上限", "client_max_body_size 100m"),
                         ("长任务超时", "read / send 600s"),
                         ("Next 3000", "standalone server"),
                         ("uvicorn 8000", "127.0.0.1 · 不对外"),
                     ]),
                dict(id="p2", label="进程编排 · systemd", sub="三个 unit · 崩溃自启", tone="blue",
                     items=[
                         ("backend.service", "--workers N · 127.0.0.1"),
                         ("frontend.service", "npm start · -H 127.0.0.1"),
                         ("worker.service", "python -m worker.scheduler"),
                         ("重启策略", "Restart=always · 5s"),
                         ("环境变量", "config 自己 load_dotenv"),
                         ("日志", "journald · 轮转"),
                     ]),
                dict(id="p3", label="发布与回滚", sub="deploy.sh", tone="slate",
                     items=[
                         ("start", "首次安装 + 建库"),
                         ("deploy", "拉码 · 装依赖 · 构建前端"),
                         ("restart", "只改 Python 时跳过构建"),
                         ("status / logs", "进程与日志"),
                         ("standalone", "免 npm install 449MB"),
                         ("回滚", "保留上一版构建产物"),
                     ]),
                dict(id="p4", label="安全加固", sub="默认最小暴露面", tone="slate",
                     items=[
                         ("harden-server.sh", "SSH / 防火墙 / 权限"),
                         ("fix-s01-ssh.sh", "专项修复脚本"),
                         ("HTTPS", "setup-https.sh + certbot"),
                         ("管理员", "ADMIN_UIDS 优先"),
                         ("密钥", ".env 仅服务账号可读"),
                         ("回环监听", "8000 不对公网"),
                     ]),
                dict(id="p5", label="监控与告警", sub="Prometheus + node-exporter", tone="slate",
                     items=[
                         ("/api/metrics", "Prometheus 文本 / 快照"),
                         ("业务指标", "问答量 · 配额拒绝 · 缓存"),
                         ("主机指标", "CPU / 内存 / 磁盘"),
                         ("alerts.yml", "阈值规则"),
                         ("probe-health.sh", "外部探活"),
                         ("资源上限", "Prometheus 256MB"),
                     ]),
                dict(id="p6", label="备份与恢复", sub="数据三类，各自策略", tone="slate",
                     items=[
                         ("SQLite 热备", "WAL checkpoint 后拷贝"),
                         ("Chroma 目录", "整目录快照"),
                         ("上传留档", "按日增量"),
                         ("保留策略", "本地 + 远端各一份"),
                         ("恢复演练", "restore 脚本"),
                         ("容量预估", "2C2G 磁盘水位告警"),
                     ]),
            ],
            [
                Note("关键结论：所有请求都从 Nginx 进入，Next 通过 rewrite 代理 /api/*，后端只监听 127.0.0.1 —— "
                     "少一层暴露面，也让 Cookie 域与登录态只有一个来源。", "blue", "结论"),
                Note("风险：多 worker 会让进程内的限流、LLM 并发闸、工具激活集各存一份，实际强度 ×N；"
                     "部署脚本必须显式说明这一点，答辩时不要声称「全局并发 16」。", "rose", "风险"),
            ],
        )
    )

    return out
