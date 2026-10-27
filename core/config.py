"""集中配置管理。

所有 API Key、地址、路径、默认参数统一在此读取，业务代码禁止硬编码。
密钥一律来自 .env（已被 .gitignore 忽略，严禁提交）。
"""

import os

from dotenv import load_dotenv

# ---------- 路径预处理：基于 __file__ 算绝对路径 ----------
# 无论从哪个 cwd 启动，都能正确定位项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT_ROOT)  # 切到根目录，让 .env / data / chroma_db 相对路径全部正确

# 从项目根加载 .env
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

# ---------- 大模型（DeepSeek Chat）----------
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# ---------- Embedding（向量化）----------
# DeepSeek 不提供 embedding 接口，必须单独配置（阿里百炼等 OpenAI 兼容服务）
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "")
EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-v3")

# ---------- 视觉模型（图片理解，复用阿里百炼 DashScope）----------
# DashScope 兼容 OpenAI 接口，qwen-vl-plus 支持图片理解
VISION_API_KEY = os.getenv("VISION_API_KEY", EMBEDDING_API_KEY)
VISION_BASE_URL = os.getenv("VISION_BASE_URL", EMBEDDING_BASE_URL)
VISION_MODEL = os.getenv("VISION_MODEL", "qwen-vl-plus")

# ---------- 路径 ----------
DATA_DIR = os.getenv("DATA_DIR", os.path.join(PROJECT_ROOT, "data"))
PERSIST_DIR = os.getenv("PERSIST_DIR", os.path.join(PROJECT_ROOT, "chroma_db"))
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", os.path.join(PROJECT_ROOT, "downloads"))

# ---------- 默认参数 ----------
CHUNK_SIZE_DEFAULT = int(os.getenv("CHUNK_SIZE", "500"))
CHUNK_OVERLAP_DEFAULT = int(os.getenv("CHUNK_OVERLAP", "50"))
TOP_K_DEFAULT = int(os.getenv("TOP_K", "3"))
# 检索条数上界：前端 UI 已限 1~10，服务端再兜一道，避免请求塞超大 top_k 拉爆检索与费用
TOP_K_MAX = int(os.getenv("TOP_K_MAX", "20"))

# Embedding 接口单次批量上限（阿里百炼为 10）
EMBED_BATCH_SIZE = int(os.getenv("EMBED_BATCH_SIZE", "10"))
# 入库时并发发出的 embedding 批次数（每批仍是 EMBED_BATCH_SIZE 条）。
# 瓶颈在网络往返，串行等 N 批 = N 个 RTT 叠加；并发 4 基本能把 RTT 藏起来。
# 调太高会撞服务端限流（429），一般 3~4 足够；1 = 退回串行。
EMBED_CONCURRENCY = int(os.getenv("EMBED_CONCURRENCY", "4"))

# ---------- 网络超时与重试（防单次请求无限挂起）----------
# 单次 LLM 请求的超时秒数。流式下这是「字节间隔」超时：只要还在出字就不会触发，
# 只有整段卡死（服务端长时间不吐字节）才中断。默认 60s 够宽松，不会误杀长回答。
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "60"))
# 失败自动重试次数（含 429 / 5xx / 超时）。重试会重复计费，别调太大。
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))
# ---------- LLM 并发闸（200 人同时提问时唯一真正会拖垮体验的点）----------
# 每进程允许「同时打到上游」的问答轮数。0 或负数 = 关闭闸门（不限）。
# ⚠️ 这是**每进程**的值：uvicorn 起 N 个 worker 时全局上限 = N × 该值。
#    闸是进程内的（跨进程共享需要 Redis），要收紧就调小这个数。
# 默认 16：DeepSeek 的并发/TPM 限制按账号等级给，16×2 worker = 32 条并行流
# 已经接近常见额度的上限；先保守设，按实际表现再调。
LLM_MAX_CONCURRENCY = int(os.getenv("LLM_MAX_CONCURRENCY", "16"))
# 排队超时：抢不到名额最多等这么多秒，超时返回 429（前端提示"稍后再试"）。
# 设太大等于让用户干等，设太小会在峰值时把本来能排上的请求也拒掉。20 秒折中。
LLM_QUEUE_TIMEOUT = float(os.getenv("LLM_QUEUE_TIMEOUT", "20"))
# 一键关闸（排障用）：0 时 /health 的 llm_gate.enabled 变 false，行为回到改造前。
LLM_GATE_ENABLED = os.getenv("LLM_GATE_ENABLED", "1").strip().lower() not in ("0", "false", "no")

# 向量化请求超时：embedding 是批量小请求，短超时即可。
EMBED_TIMEOUT = float(os.getenv("EMBED_TIMEOUT", "30"))
EMBED_MAX_RETRIES = int(os.getenv("EMBED_MAX_RETRIES", "2"))

# ---------- 检索策略（候选池 → 离群过滤 → 多样性挑选）----------
# 先多取几倍候选再挑，否则 top_k 名额会被相邻重复切片占满
RETRIEVE_FETCH_MULT = int(os.getenv("RETRIEVE_FETCH_MULT", "3"))
# 候选池下限：top_k 设得小时也保证有得挑
RETRIEVE_MIN_FETCH = int(os.getenv("RETRIEVE_MIN_FETCH", "12"))
# MMR 相关度权重：1 = 完全按相关度排（等同旧行为），越小越强调多样性
RETRIEVE_MMR_LAMBDA = float(os.getenv("RETRIEVE_MMR_LAMBDA", "0.65"))
# 余弦相似度下限：低于它当不相关直接丢。0 = 不做离群过滤
# 0.25 是刻意留宽的：宁可多带一条弱的，也别把能用的片段误杀。
# 觉得回答还是太"硬答"就往 0.4 调；觉得资料太少就调回 0.2 或 0。
RETRIEVE_MIN_SIM = float(os.getenv("RETRIEVE_MIN_SIM", "0.25"))

# ---------- 混合检索（向量 + BM25 关键词，RRF 融合）----------
# 纯向量对专有名词 / 缩写 / 编号（"第 3 条""CUDA 12.4"）召回差，关键词通道能补上。
# 置 0 即退回纯向量，无需改代码。
HYBRID_ENABLED = bool(int(os.getenv("HYBRID_ENABLED", "1")))
# RRF 融合常数：score = Σ 1/(K + rank)，K 越大越弱化头部名次差异（业界惯用 60）
HYBRID_RRF_K = int(os.getenv("HYBRID_RRF_K", "60"))
# 关键词通道取前几条参与融合
HYBRID_KEYWORD_TOP = int(os.getenv("HYBRID_KEYWORD_TOP", "20"))
# 库内片段数超过它就放弃关键词通道（BM25 需要把全文载入内存，给大库留个闸）
HYBRID_MAX_DOCS = int(os.getenv("HYBRID_MAX_DOCS", "20000"))

# ---------- 查询改写（多轮追问 → 独立检索问题）----------
# 追问（"那它呢""第二个呢"）单独看检索不到东西。只在"看起来像追问"时才用对话
# 历史改写，其余问题零额外开销。置 0 完全关闭。
QUERY_REWRITE_ENABLED = bool(int(os.getenv("QUERY_REWRITE_ENABLED", "1")))
# 问题长度 ≤ 它且存在对话历史时，判定为追问（中文字符数）
QUERY_REWRITE_MAX_LEN = int(os.getenv("QUERY_REWRITE_MAX_LEN", "8"))
# 改写时喂给模型的历史消息条数（从最近往前取）
QUERY_REWRITE_HISTORY = int(os.getenv("QUERY_REWRITE_HISTORY", "4"))
# 改写输出的 token 上限：只输出一条问题，给小值即可，防跑偏也防费用
QUERY_REWRITE_MAX_TOKENS = int(os.getenv("QUERY_REWRITE_MAX_TOKENS", "64"))

# ---------- 可信度（P0：让模型"敢说知识库里没有"）----------
# ⚠️ 和上面的 RETRIEVE_MIN_SIM 是**两件事**，别合并成一个：
#   RETRIEVE_MIN_SIM 决定「这段资料丢不丢」——丢完一条不剩时会强留最相关那条；
#   RETRIEVE_LOW_SIM 决定「留下来的这段够不够格当依据」——不丢，但要在回答里示弱。
# 用户调研（56 份）里「答案不准还装自信」占 83.9%，是压倒性第一担忧，
# 这个阈值就是拿来对冲它的：相似度低于它 = 没找到足够依据。
#
# 为什么 0.45：同一份资料里的原文提问通常能到 0.6+，换个说法问大约 0.5，
# 只是"用词沾边但讲的是另一件事"落在 0.3 附近。0.45 卡在中间，
# 会漏判一部分边缘情况，但宁可多示弱一次，也别再装一次自信。
RETRIEVE_LOW_SIM = float(os.getenv("RETRIEVE_LOW_SIM", "0.45"))

# 低置信提示开关。关掉后检索链路行为完全回到改造前（不示弱、不追加提示），
# 留着它是为了出问题时能一键回退，不用回滚代码。
GROUNDING_NOTICE = os.getenv("GROUNDING_NOTICE", "1") not in ("0", "false", "False")

# ---------- 多用户 / 认证 ----------
# 会话 token 的签发密钥。不配的话会自动在 data/.auth_secret 生成并持久化，
# 好处是重启不丢登录态；坏处是迁移机器时要把这个文件一起带走，否则所有人掉线一次。
AUTH_SECRET = os.getenv("AUTH_SECRET", "")
_AUTH_SECRET_FILE = os.path.join(DATA_DIR, ".auth_secret")
# 登录态有效期（天）
AUTH_TTL_DAYS = int(os.getenv("AUTH_TTL_DAYS", "7"))
# 是否开放自助注册。演示期默认开着；正式用建议关掉并改成邀请码
ALLOW_SIGNUP = os.getenv("ALLOW_SIGNUP", "1").strip().lower() in ("1", "true", "yes")
# 口令散列轮数（PBKDF2-SHA256）。机器好可以调高，反之别低于 10 万
AUTH_PBKDF2_ROUNDS = int(os.getenv("AUTH_PBKDF2_ROUNDS", "200000"))

# 是否在登录响应体里额外回吐 token。
# 默认关：浏览器侧走 HttpOnly Cookie 就够了，把 token 交给 JS 等于把 XSS 的
# 收益从"偷不到"变成"直接顺走"。只在给移动端 / 小程序 / 接口调试时需要，
# 那些客户端本来也没有 Cookie 罐子，用的是 Authorization 头。
AUTH_RETURN_TOKEN = os.getenv("AUTH_RETURN_TOKEN", "0").strip().lower() in ("1", "true", "yes")

# 令牌头的名字。对应 sky-take-out 的 admin-token-name / user-token-name，
# 只是那边一个端一个头名，这边同一套身份两处都认（见 core/auth.py:extract_token）。
AUTH_TOKEN_HEADER = os.getenv("AUTH_TOKEN_HEADER", "X-Auth-Token")

# 迁移账号：历史数据（改造前建的会话 / 知识库）先锁在这个名下，谁都看不到，
# 由 tools/migrate_owner.py 明确指派给某个真人账号后才恢复可用。
# 这是刻意的"默认拒绝"——宁可暂时看不见，也不能让第一个登录的人顺手认领别人的数据。
LEGACY_OWNER = "legacy"

# ---------- 配额（防止别人用你的界面烧你的额度）----------
# 每个用户每分钟最多多少次问答 / 建库类写操作；0 = 不限
RATE_PER_MINUTE = int(os.getenv("RATE_PER_MINUTE", "10"))
# 每个用户每日 slices（embedding 条数）上限；0 = 不限
DAILY_EMBED_LIMIT = int(os.getenv("DAILY_EMBED_LIMIT", "1000"))
# 每个用户每日问答次数上限；0 = 不限
DAILY_ASK_LIMIT = int(os.getenv("DAILY_ASK_LIMIT", "60"))
# 每个用户每日「自动调用」次数上限（定时委托投递 + 每日总结生成，kind="auto"）；0 = 不限。
# DAILY_ASK_LIMIT 挡的是用户主动提问，无人值守的定时任务完全在它之外——
# 那条链路没人盯着，一旦写错（比如 is_due 判断反了）就是静默烧钱，所以单独卡一道。
# 默认 120 = 3 人 ×（5 委托 + 1 总结）× 重试与手动补生成余量（见 docs/接口文档.html 调度节）。
#
# ⚠️ 合并说明（10-14 双方各加过一次）：
#   冯靖豪侧原先是 `20`（拍脑袋拍的），林宏亮侧是 `120`（按 3 人 ×(5 委托+1 总结)×
#   重试与手动补生成余量算过）。**取 120**，理由见上面那段计算。
#   ⇒ 原来担心的「取值冲突」不是取舍问题，是对方算得更细，别退回 20。
DAILY_AUTO_LIMIT = int(os.getenv("DAILY_AUTO_LIMIT", "120"))

# ---------- 定时任务（独立 worker 进程）----------
# 调度与解释统一用这个时区。禁止裸 datetime.now()——容器镜像的 TZ 常常是 UTC，
# 一旦漏带时区，「早上七点」会整体偏移 8 小时且不报错，只会安静地错。
SCHEDULER_TZ = os.getenv("SCHEDULER_TZ", "Asia/Shanghai")
# 每日总结的生成时刻（北京时间）。默认 23:30：留出当晚补记的时间，
# 次日跑到点时昨天的会话已经全部落盘。写进 schedule_jobs.cron 的初始值，
# 之后改周期以表为准（改表不用改代码、不用发版）。
REPORT_HOUR = int(os.getenv("REPORT_HOUR", "23"))
REPORT_MINUTE = int(os.getenv("REPORT_MINUTE", "30"))

# 管理员用户名（逗号分隔）。管理员不受配额与每分钟限流约束，相当于主账号。
# 默认把项目作者账号设为管理员；其他部署在 .env 里改成自己的用户名即可
# （例如 ADMIN_USERNAMES=admin,alice）。名单为空则没有管理员。
ADMIN_USERNAMES = [
    x.strip() for x in os.getenv("ADMIN_USERNAMES", "苞米呀").split(",") if x.strip()
]

# ---------- 可选：天气工具 ----------
AMAP_API_KEY = os.getenv("AMAP_API_KEY", "")

# ---------- 可选：联网搜索备选 ----------
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")

# ---------- 混合执行架构（工作流 × 自主 Agent）----------
# 注意与上面的检索混合（HYBRID_ENABLED）区分：这组是 mode=hybrid 执行模式的配置
HYBRID_MODE_ENABLED = bool(int(os.getenv("HYBRID_MODE_ENABLED", "1")))
# 单次任务允许的最大模式切换次数：防规则配错导致工作流⇄Agent 乒乓
HYBRID_MAX_SWITCHES = int(os.getenv("HYBRID_MAX_SWITCHES", "5"))
# 同方向切换全局防抖（秒）：刚切过去又切回来通常是规则打架，不是真需求
HYBRID_SWITCH_COOLDOWN = int(os.getenv("HYBRID_SWITCH_COOLDOWN", "30"))
# Agent 模式三重预算：步数 / 时长（秒）/ 近似 token（chars//2 折算），先到先触发收编
HYBRID_AGENT_MAX_STEPS = int(os.getenv("HYBRID_AGENT_MAX_STEPS", "10"))
HYBRID_AGENT_MAX_SECONDS = int(os.getenv("HYBRID_AGENT_MAX_SECONDS", "300"))
HYBRID_AGENT_TOKEN_BUDGET = int(os.getenv("HYBRID_AGENT_TOKEN_BUDGET", "8000"))
# 工作流整体超时护栏（秒）：单个流程实例不允许跑得更久
HYBRID_WORKFLOW_TIMEOUT = int(os.getenv("HYBRID_WORKFLOW_TIMEOUT", "600"))
# 工作流单节点默认超时（秒），节点定义可覆盖
HYBRID_NODE_TIMEOUT = int(os.getenv("HYBRID_NODE_TIMEOUT", "120"))
# 工作流内 LLM 节点的 max_tokens 上限（project memory 硬约束：内部推理调用必须显式限长）
HYBRID_LLM_MAX_TOKENS = int(os.getenv("HYBRID_LLM_MAX_TOKENS", "2048"))

# ---------- Agent 主循环（api/chat.py 的 agent 模式 / hybrid 的 Agent 引擎）----------
# Agent 一次回答的 max_tokens 上限。0 = 用平台默认（保持改造前行为）。
#
# 为什么留这个开关：hybrid 的 Agent 引擎已配了步数/时长/token 三重预算护栏，
# 但护栏是"绕开"主循环算的；主循环自己不封顶的话，单次回答仍可无界增长。
# 默认 0 是为了不动既有行为（改默认值会让现有回答长度突变），
# 生产建议按 HYBRID_AGENT_TOKEN_BUDGET 同量级设置（如 2048）。
AGENT_LLM_MAX_TOKENS = int(os.getenv("AGENT_LLM_MAX_TOKENS", "0"))
# 标题生成：只要 ≤15 字，给输出兜底防跑偏（原为硬编码 32，提到配置便于调）
TITLE_MAX_TOKENS = int(os.getenv("TITLE_MAX_TOKENS", "32"))

# ---------- 工具守卫（P0-3：让 TOOL_META 的声明真正生效）----------
# 危险动作审批开关（默认开）。needs_approval=True 的工具首次调用只返回确认串，
# 带同一确认串再调才执行。演示期若嫌摩擦大，置 0 可整体关闭。
TOOL_APPROVAL_ENABLED = os.getenv("TOOL_APPROVAL_ENABLED", "1") not in ("0", "false", "False")
# 单个工具输出的硬上限（兜底档）：TOOL_META 里按工具分档（4000/2000/1000），
# 这里只在元数据缺失时兜底使用。
TOOL_OUTPUT_HARD_LIMIT = int(os.getenv("TOOL_OUTPUT_HARD_LIMIT", "8000"))

# 混合模式注入对话历史的消息条数上限（跨轮记忆；0=关闭历史注入，退回旧的无记忆行为）。
# 修复"混合模式每轮失忆"：hybrid 会把最近 N 条历史塞进 ctx.shared["history"]，
# Agent/Workflow 引擎在拼 prompt 时前置，从而获得多轮上下文。
HYBRID_HISTORY_MAX_MESSAGES = int(os.getenv("HYBRID_HISTORY_MAX_MESSAGES", "60"))

# 主链路历史注入的「输入侧字符预算」：窗口放宽到 30 轮（70 条）后，最重真实会话
# 会把 prompt 推过 DeepSeek 64K 上下文（实测 76k tok > 64K）。从最新往旧累计、
# 超预算就丢最旧的，而不是截断最新一轮（那会让模型答非所问）。≈15k tok。
CHAT_HISTORY_MAX_CHARS = int(os.getenv("CHAT_HISTORY_MAX_CHARS", "24000"))

# ---------- 标签钩子（方案 C：入库级主题 + 对话级用户画像）----------
# A 块：文件入库时对每个来源抽主题标签，写入每片 metadata["topics"]（"|t1|t2|" 分隔串），
#   后续检索（D 块）可用 Chroma `where: {"topics": {"$contains": "|t1|"}}` 精确过滤。
#   置 0 关闭；失败静默降级（抽不到就不写 topics，不影响正常入库）。
TOPIC_EXTRACT_ENABLED = bool(int(os.getenv("TOPIC_EXTRACT_ENABLED", "1")))

# B 块：每轮对话结束后异步抽用户画像标签，沉淀到 sessions.db 的 user_tags 表，
#   供后续 C2 聚合 / D 检索偏置使用。置 0 关闭；钩子 fire-and-forget，失败静默。
USER_TAG_HOOK_ENABLED = bool(int(os.getenv("USER_TAG_HOOK_ENABLED", "1")))

# C2 块：worker 定时（默认每天 02:30）聚合每个用户的 user_tags，生成画像快照。
#   聚合取 Top N 标签 + 权重和，存 user_profiles 表（零 LLM 调用，纯结构化）。
PROFILE_TOP_TAGS = int(os.getenv("PROFILE_TOP_TAGS", "30"))

# D 块：检索时按用户画像（user_tags）对命中主题的片段做加权偏置，提升个性化排序。
#   置 0 关闭；偏置只改排序、不改原始 distance（相似度换算/阈值不被污染）。
RETRIEVAL_BIAS_ENABLED = bool(int(os.getenv("RETRIEVAL_BIAS_ENABLED", "1")))
RETRIEVAL_BIAS_ALPHA = float(os.getenv("RETRIEVAL_BIAS_ALPHA", "0.08"))
RETRIEVAL_BIAS_TOP = int(os.getenv("RETRIEVAL_BIAS_TOP", "8"))

# ---------- 检索词增强（roadmap B2 匹配器 / B3 预览接口）----------
# 词表路径。留空 = 自动挑：优先 vocab_v1.csv（X1 正式词表），没有再退回从金标集
# 派生的临时词表。**故意不写死默认路径**：X1 到货那天只需要把文件放进 data/vocab/，
# 不用改 .env、不用重启（service/enhance.py 按 mtime 热加载）。
ENHANCE_VOCAB_PATH = os.getenv("ENHANCE_VOCAB_PATH", "")
ENHANCE_ADJUDICATION_PATH = os.getenv(
    "ENHANCE_ADJUDICATION_PATH", os.path.join(DATA_DIR, "vocab", "adjudication.csv"))
# 预览接口的文本上限。这是"一句话的口语词"场景，不是长文改写，
# 放开长度只会让单次请求变慢，没有收益。
ENHANCE_MAX_CHARS = int(os.getenv("ENHANCE_MAX_CHARS", "2000"))
# B4：把换词接进聊天检索链路。默认开——不接进链路等于没人用；
# 但它会改变检索用文本进而影响召回，所以必须留一个能当场关掉的开关（置 0 即回到旧行为）。
ENHANCE_CHAT_ENABLED = bool(int(os.getenv("ENHANCE_CHAT_ENABLED", "1")))

# ---------- 计划主题识别加固（P2）----------
# 计划请求命不中主题词时不直接退化成通用模板，回看最近 N 条用户消息续上主题
#   （"计划再细一点" → 从前文的"六级备考"推断）。置 0 表示只看本轮提问。
PLAN_TOPIC_HISTORY_MESSAGES = int(os.getenv("PLAN_TOPIC_HISTORY_MESSAGES", "6"))

# ---------- 上下文压缩（P0-2 接线开关）----------
# 总开关：置 0 后 assemble_history 原样返回全量历史、maybe_compress 不落库，
# 即完全退回"滑动窗口"旧行为。这是线上出问题时的**一键回滚开关**，
# 不需要改代码、不需要回滚版本。
COMPRESS_ENABLED = os.getenv("COMPRESS_ENABLED", "1") not in ("0", "false", "False")
# 触发压缩的字符阈值（按**全量历史**估，不是按窗口内）。
# 低于它不压：短会话压了反而丢信息、且压缩块本身要占 token。
COMPRESS_MAX_CONTEXT_CHARS = int(os.getenv("COMPRESS_MAX_CONTEXT_CHARS", "12000"))
# 压缩后保留最近 K 轮原文（1 轮 = user + assistant）
COMPRESS_KEEP_TURNS = int(os.getenv("COMPRESS_KEEP_TURNS", "6"))
# L2 安全阀扩容后的 K（实体丢失且无上一版压缩块时用，压得更少）
COMPRESS_KEEP_TURNS_EXPANDED = int(os.getenv("COMPRESS_KEEP_TURNS_EXPANDED", "12"))
# 压缩块自身预算：字符上限 / 段数上限，超限折叠最早的段
COMPRESS_MAX_SUMMARY_CHARS = int(os.getenv("COMPRESS_MAX_SUMMARY_CHARS", "4000"))
COMPRESS_MAX_SEGMENTS = int(os.getenv("COMPRESS_MAX_SEGMENTS", "12"))

# ---------- Prompt 自进化（GEPA 式 · issue #12）----------
# 总开关：置 0 后 prompt_store.resolve 一律返回内置基线，进化产物不生效
#   （线上出问题的一键回滚开关：不改代码、不回滚版本）。
PROMPT_EVO_ENABLED = os.getenv("PROMPT_EVO_ENABLED", "1") not in ("0", "false", "False")
# 允许进化的提示词路径。agent = agent/builder.py 的行为准则段；
#   rag_note_low / rag_note_medium = kb/rag_core.py 的示弱约束模板。
#   hybrid 路径本期不含（方案 §待讨论已明确）。
PROMPT_EVO_PATHS = tuple(
    p.strip()
    for p in os.getenv("PROMPT_EVO_PATHS", "agent,rag_note_low,rag_note_medium").split(",")
    if p.strip()
)
# 回放集规模：200~300 例（用户 2026-10-20 拍板）。低于 MIN 直接判
#   insufficient_sample —— 样本不足时做显著性判断只会得出假结论。
PROMPT_EVO_REPLAY_MIN = int(os.getenv("PROMPT_EVO_REPLAY_MIN", "200"))
PROMPT_EVO_REPLAY_MAX = int(os.getenv("PROMPT_EVO_REPLAY_MAX", "300"))
# 回放集里 up 样本占比：必须混入，否则模型会退化成"什么都不答"的对抗解。
PROMPT_EVO_REPLAY_UP_RATIO = float(os.getenv("PROMPT_EVO_REPLAY_UP_RATIO", "0.3"))
# 固定抽样种子：同一批数据每轮抽到同一批样本，否则两轮指标不可比。
PROMPT_EVO_SEED = int(os.getenv("PROMPT_EVO_SEED", "20261020"))
# 晋升闸门（用户拍板）：M1-M4 用成对 bootstrap 求 95% 置信区间，
#   下界 ≥ -5pp 才允许进前沿。调这两个值等于改验收口径，要先改文档。
PROMPT_EVO_CI_ALPHA = float(os.getenv("PROMPT_EVO_CI_ALPHA", "0.05"))
PROMPT_EVO_MAX_DROP = float(os.getenv("PROMPT_EVO_MAX_DROP", "0.05"))
PROMPT_EVO_BOOTSTRAP = int(os.getenv("PROMPT_EVO_BOOTSTRAP", "2000"))
# 成本 / 延迟护栏：候选相对基线的放大倍数上限（M5 / M6 是越小越好的指标）。
PROMPT_EVO_MAX_COST_RATIO = float(os.getenv("PROMPT_EVO_MAX_COST_RATIO", "1.15"))
PROMPT_EVO_MAX_LATENCY_RATIO = float(os.getenv("PROMPT_EVO_MAX_LATENCY_RATIO", "1.20"))
# 变异护栏：单条候选允许改动的总行数上限（KV 前缀稳定 + 回归面可控）。
PROMPT_EVO_MAX_DIFF_LINES = int(os.getenv("PROMPT_EVO_MAX_DIFF_LINES", "6"))
# 一轮最多评估几个候选（成本闸门：每候选 = 回放集条数 × 一次生成 + 一次判分）。
PROMPT_EVO_CANDIDATES = int(os.getenv("PROMPT_EVO_CANDIDATES", "2"))
# 单轮评估最多跑多少条 case（预算闸门）。回放集仍是 200~300，
#   可跨轮跑完；给 0 表示不额外截断。
PROMPT_EVO_RUN_MAX_CASES = int(os.getenv("PROMPT_EVO_RUN_MAX_CASES", "200"))
# 自动 promote：默认关（与 CODEOWNERS / PR 文化一致），仅供演示环境打开。
PROMPT_EVO_AUTO_PROMOTE = os.getenv("PROMPT_EVO_AUTO_PROMOTE", "0") not in ("0", "false", "False")
# active 提示词的进程内缓存 TTL（秒）。0 = 每次 build_agent 都读库。
PROMPT_EVO_CACHE_TTL = float(os.getenv("PROMPT_EVO_CACHE_TTL", "2"))
# 单条回放样本的最大字符数（脱敏后截断，控制判分成本）。
PROMPT_EVO_MAX_CASE_CHARS = int(os.getenv("PROMPT_EVO_MAX_CASE_CHARS", "800"))
# 判分器：heuristic（默认，确定性、零成本、离线可跑）| llm（独立模板 + temperature=0）。
#   生产建议用 llm，但必须与被评提示词用**不同**模板——否则等于让模型给自己打分。
PROMPT_EVO_JUDGE = os.getenv("PROMPT_EVO_JUDGE", "heuristic")
PROMPT_EVO_JUDGE_MAX_TOKENS = int(os.getenv("PROMPT_EVO_JUDGE_MAX_TOKENS", "512"))

# ---------- 新建会话自动绑定资料库（P3）----------
# 用户**有且仅有一个**知识库时，新建会话自动绑上它。修复"传过资料、新开会话
#   模型却说没绑定"的割裂（2026-10-16 线上复现）。多库时不自动绑（绑错比不绑更糟），
#   改由前端顶栏「未绑定资料库」提示引导手动选择。置 0 关闭。
AUTO_BIND_SINGLE_KB = bool(int(os.getenv("AUTO_BIND_SINGLE_KB", "1")))


# ---------- 审计修复开关（2026-10-20）----------
# 这一组全部遵循同一条原则：**默认开启修复，出问题一行 env 回退**。
# 沿用 COMPRESS_ENABLED / AUTH_RETURN_TOKEN 的既有惯例。
#
# A1 输出截断检测：模型因 max_tokens / 上下文上限被截断时，上游会在最后一帧给
#   finish_reason="length"。此前这个字段被完整收到、完整丢掉，于是半句话被当成
#   完整答案返回（不报错、日志干净、前端照常渲染）——静默失败里最贵的一种。
#   置 0 退回旧行为（不检测、不发 truncation 帧、不落 truncated 标记）。
TRUNCATION_DETECT_ENABLED = os.getenv("TRUNCATION_DETECT_ENABLED", "1") not in (
    "0",
    "false",
    "False",
)

# A2 时区严格模式：置 0 时 core/timeutil.py 的 day_str / day_bounds 退回
#   time.localtime()（依赖服务器 TZ 的旧行为）。只在修复本身出问题时用。
TZ_STRICT = os.getenv("TZ_STRICT", "1") not in ("0", "false", "False")

# A3 限流双层：置 1 时进程内滑窗超限会再查一次落库计数确认（多 worker 下限流
#   不再变成 N 倍）。置 0 退回纯进程内单层（旧行为）。单 worker 下两者等价。
RATE_LIMIT_SHARED = os.getenv("RATE_LIMIT_SHARED", "1") not in ("0", "false", "False")

# A4 向量空间指纹校验：换了 EMBEDDING_MODEL 但没重建索引时，旧向量与新查询向量
#   不在同一语义空间，余弦仍算得出来、只是不再可比 → 检索退化成噪声且不报错。
#   置 0 关闭校验（不推荐，仅用于紧急绕过）。
EMBED_GUARD_ENABLED = os.getenv("EMBED_GUARD_ENABLED", "1") not in ("0", "false", "False")

# A5 指标埋点：置 0 后 core/metrics.py 的 inc/observe 全部空转（热路径不再加锁）。
#   注意 /api/metrics 端点的权限门是独立的，不靠这个开关保护。
METRICS_ENABLED = os.getenv("METRICS_ENABLED", "1") not in ("0", "false", "False")

# A7 JWT header 校验：校验 header.alg == HS256 / header.typ == JWT（typ 缺失时放行）。
#   当前实现不可利用（验签是硬编码 HS256，不按 alg 分派），这个开关修的是
#   "将来换成 PyJWT" 那天的迁移风险。置 0 退回不校验 header。
JWT_HEADER_CHECK = os.getenv("JWT_HEADER_CHECK", "1") not in ("0", "false", "False")


def validate() -> list[str]:
    """启动前校验关键配置，返回缺失项的中文提示列表。"""
    missing = []
    if not DEEPSEEK_API_KEY:
        missing.append("缺少 DEEPSEEK_API_KEY（大模型对话密钥）")
    if not EMBEDDING_API_KEY:
        missing.append("缺少 EMBEDDING_API_KEY（向量化服务密钥）")
    if not EMBEDDING_BASE_URL:
        missing.append("缺少 EMBEDDING_BASE_URL（向量化服务地址）")
    return missing
