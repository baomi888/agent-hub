// 与后端 api/schemas.py 对齐的前端类型定义

export interface Conversation {
  id: string;
  title: string;
  kb_id: string | null;
  created_at: number;
  updated_at: number;
}

// 知识库内的单个文件（后端 kb_files.json 落盘，重启不丢）
export interface KbFile {
  name: string;
  chunks: number | null;
  added_at: number | null;
  /**
   * 这条资料怎么进库的：upload=用户上传，web=联网抓的，dialogue=从对话里沉淀的。
   * dialogue 的 name 形如 `对话·<会话标题>`，没有原始文件，正文只能从向量片段拼回。
   */
  origin?: "upload" | "web" | "dialogue";
}

export interface KbInfo {
  kb_id: string;
  name: string;
  chunks: number;
  files: KbFile[];
  /** 库主是否已设为站内公开：其他登录用户可把这个库挂进对话只读检索 */
  public?: boolean;
  /** 公开时写在广场卡片上的那句简介。只在公开期间有 —— 收回时随登记一起清掉 */
  public_desc?: string;
  /** 公开期间别人能做什么（2026-11-13）。未设置按"仅观看"给 */
  public_access?: AccessMode;
}

/** GET /api/kb/public 单条：公开知识库广场上的一张卡片。别人的库，没有文件清单 */
/**
 * 公开库的访问方式（2026-11-13）。
 *   preview  —— 仅观看：能看清单、能在线预览，不给下载
 *   download —— 可下载：在上面基础上再给原件
 * 两种都能被检索、都能留言，区别只在最后那一下"存到本地"。
 */
export type AccessMode = "preview" | "download";

export interface PublicKbInfo {
  kb_id: string;
  name: string;
  chunks: number;
  /** 库里的资料份数（只给数量，不给清单） */
  files: number;
  /** 库主显示名：昵称优先，没设过退回用户名；查不到人时为空串 */
  owner_name: string;
  /** 卡片上的简介，可能是空串（老数据或库主没写） */
  desc: string;
  /** 首次分享的时间（秒）。0 = 老数据，没有这条记录 */
  at: number;
  /**
   * 官方课程库（站点自建，非用户分享）。
   * 2026-11-13 起用户分享的库也能点进去看清单，所以它不再决定"能不能进"，
   * 只决定分区、徽标和署名。
   */
  official?: boolean;
  /** 留言板上的条数。0 = 还没人说话，卡片上就不显示这个数字 */
  posts?: number;
  /**
   * 别人能拿这个库做什么：
   *   preview  —— 能看清单、能在线看，不能下载原件（默认）
   *   download —— 再加一项"下载原件"
   * 官方库恒为 download。
   */
  access?: "preview" | "download";
  /** 看过的人数（独立访客，同一个人重复打开只算一次） */
  views?: number;
  /** 收藏数。比浏览更"重"的信号：看过是路过，收藏是有意留着 */
  favs?: number;
  /** 我收藏过这个库没有 */
  favorited?: boolean;
  /**
   * 这个库名下一共有几个库（不同用户可能传来同名的库）。
   * 1 = 没有重名，卡片上不出现同名筛选；>1 时前端给一个「同名 N 个」的入口。
   */
  same_name?: number;
}

/**
 * GET /api/kb/favorites 单条：我收藏的一个公开库。
 *
 * 和广场卡片长得像，但多两件东西 —— 用户明确要求的：
 *   `source` 说明它从哪来（"官方知识库" / "某某 的库"），
 *   `favorited_at` 说明我什么时候收的（列表按它倒序）。
 */
export interface FavoriteKbInfo {
  kb_id: string;
  name: string;
  desc: string;
  official: boolean;
  /** 来源，直接可展示：「官方知识库」/「张三 的库」 */
  source: string;
  /** 作者显示名。官方库是「官方」 */
  owner_name: string;
  access: AccessMode;
  files: number;
  chunks: number;
  /** 库第一次公开的时间（秒）。0 = 老数据 */
  at: number;
  views: number;
  favs: number;
  posts: number;
  favorited: boolean;
  /** 我收藏这件事发生的时间（秒），列表按它倒序 */
  favorited_at: number;
}

/** POST /api/kb/public/{kb_id}/fork 的返回：复制出来的那个库 */
export interface ForkResult {
  kb_id: string;
  name: string;
  files: number;
  chunks: number;
  /** 源库一共多少份资料 —— 和 files 不等时说明有几份原件缺失 */
  from_files: number;
  from_kb_id: string;
}

/** 公开知识库留言板上的一条留言（GET /api/kb/public/{kb_id}/board） */
export interface BoardPost {
  id: number;
  /** 纯文字。表情就是普通字符，和正文一起存 */
  body: string;
  /** Unix 秒 */
  at: number;
  author: {
    name: string;
    avatar: string;
    /** 管理员：名字前挂金色「管理员」标 */
    admin: boolean;
  };
  /** 是不是自己写的（前端据此高亮） */
  mine: boolean;
  /** 作者本人 / 库主 / 管理员能看到删除按钮；真正的判定在后端 */
  can_delete: boolean;
  /**
   * 回复的是楼里某条回复时，点名对象。
   * 直接回复楼主、或新开一楼时为 null —— 位置本身就说明了对象。
   */
  reply_to: { id: number; name: string } | null;
  /** 只有顶层帖有：挂在它下面的回复（时间正序）。两级封顶，回复里不会再嵌套 */
  replies?: BoardPost[];
}

/** 一个公开库的留言板首屏（含官方表情候选） */
export interface BoardData {
  kb_id: string;
  name: string;
  desc: string;
  official: boolean;
  owner_name: string;
  /** 留言总数（不是这一页的条数） */
  total: number;
  /** 「官方表情包」候选，由后端下发 —— 前端不硬编码，加表情不用改两处 */
  stickers: string[];
  posts: BoardPost[];
  /** 还有更早的留言可以翻 */
  has_more: boolean;
}

/** 官方课程库里的一份资料（GET /api/kb/public/{kb_id}） */
export interface OfficialKbFile {
  /** 展示名，可能带文件夹，如 `物理语料/formulas.jsonl` */
  name: string;
  /** 已建库的片段数；0 = 扫描件或抽取失败，只能下载原件 */
  chunks: number;
  added_at: number | null;
  /** 字节数，后端按磁盘现算 */
  size: number;
  /** 小写扩展名，不带点 */
  ext: string;
  /** 原版 PDF 的库内名字（只有 Word 有）。预览读它，下载仍走 name */
  preview_file?: string;
  /** 能否在线预览 */
  preview: boolean;
  /** 原始文件是否还在（不在就只能看正文） */
  download: boolean;
}

/** 官方课程库详情 */
export interface OfficialKbDetail {
  kb_id: string;
  name: string;
  desc: string;
  chunks: number;
  /** 是不是官方课程库。false = 同学公开的库（也有清单，但署名和徽标不同） */
  official: boolean;
  /** 官方 → "官方"；同学库 → 库主的昵称/用户名 */
  owner_name: string;
  /** 见 PublicKbInfo.access */
  access?: "preview" | "download";
  /** 看过的人数（本次打开已计入） */
  views?: number;
  /** 上传/分享日期（秒）。官方库是导入上线那天，用户库是第一次公开那天 */
  uploads_at?: number;
  /** 收藏数 */
  favs?: number;
  /** 我收藏过没有 */
  favorited?: boolean;
  /** 这个库是不是我自己的（自己的库不给收藏/复制按钮） */
  mine?: boolean;
  files: OfficialKbFile[];
}

/**
 * 建库建议的来源。两种场景共用一张卡片，但说法不一样：
 *   web_search        —— 本轮联网答完了，把收集到的外部信息存下来
 *   no_kb_retrieval   —— 勾了「检索」但会话没绑库，把这轮对话存成一个库
 */
export type KbProposalSource = "web_search" | "no_kb_retrieval";

/**
 * 服务端通过 SSE `kb_proposal` 下发的一条「要不要建个库」建议。
 *
 * ★ `expires_at` 是 **Unix 秒**（后端全站用秒），不是毫秒。
 *   直接跟 `Date.now()` 比会晚 1000 倍 —— 卡片永远显示"还能点"，
 *   点了才吃一个 410。组件里用 `expiresAtSec()` 归一，别在下游各写一遍。
 */
export interface KbProposal {
  proposal_id: string;
  /** 这轮聊的主题，给用户一个"它凭什么建议我建库"的锚点 */
  topic: string;
  source: KbProposalSource;
  /** 可沉淀的知识句条数（web 场景可能含来源条数） */
  candidates: number;
  /** 后端建议的库名，用户可以在卡片里改掉 */
  kb_hint: string;
  /** Unix 秒 */
  expires_at: number;
  /** 后端生成的一句话说明（≤40 字），前端不拼接、不加工 */
  reason: string;
  /**
   * 这条建议指的是哪条回答（messages 表自增 id）。
   *
   * 只在「刷新后恢复」时用得上：实时那一帧是直接挂到刚生成的那条回答上的，
   * 不需要对号；而刷新后消息是整段重新拉的，没有这个 id 就只能猜最后一条。
   */
  answer_message_id?: number;
}

/** confirm 的返回值：字段都是可选的 —— 建库成功但嵌入失败时 imported 可能是 0。 */
export interface KbProposalConfirmResult {
  proposal_id: string;
  status: string;
  kb_id?: string;
  name?: string;
  imported?: number;
  chunks?: number;
  staged_segments?: number;
  bound?: boolean;
  batch_id?: string;
  /** 这批内容之前已经沉过（幂等命中），这次一条没写 —— 不是失败 */
  duplicate?: boolean;
}

export interface Message {
  id?: number;
  role: "user" | "assistant" | "system";
  content: string;
  refs?: string;
  /** 联网来源，随 assistant 消息持久化；老消息没有这个字段时按空列表处理。 */
  web_sources?: WebSource[];
  created_at?: number;
}

/**
 * 对话里能存进资料库的一句话（`GET /api/kb/dialogue-candidates/{sid}`）。
 *
 * `text` 是**原句，不改写也不摘要** —— 后端注释写明理由：抽象会不可逆损失原文，
 * 资料库的价值恰恰在可溯源。所以界面上只能原样显示，不要在前端做二次加工。
 *
 * `kind` 是后端正则判出来的类型（事实 / 约束 / 待办 / 偏好 / 目标 / 纠正）。
 * 它是**零 LLM** 的规则判定，不是语义理解，所以只能当弱提示用：
 * 界面上不要写「AI 识别为…」，那是在替一个正则吹牛。
 */
export interface DialogueCandidate {
  text: string;
  kind: string;
  /**
   * 这句出自哪条消息 —— 后端 `kb/dialogue_import.py` 写的是 `refs: [message_id]`，
   * 是**数字**，和界面上 `UiMessage.dbId` 同一套 id。
   *
   * 有了它，「在消息下面点存」才能只勾这一句对应的候选，而不是让用户在
   * 整段对话的候选里自己找。之前这里写成 `string[]`，是因为没人用过它。
   */
  refs?: number[];
  role?: string;
  ts?: number;
  salience?: number;
}

export interface DialogueCandidatesResp {
  sid: string;
  /** 会话标题，后端用它当建库的主题名（`topic_of()` 优先取它），所以界面直接展示 */
  conv_title?: string;
  total: number;
  candidates: DialogueCandidate[];
}

/**
 * 手工入库的结果。
 *
 * ★ `skipped` 是这次被跳过的条数：同一段对话里已经存过的原句不会写第二遍。
 *   这个去重是 `GET/import-dialogue` 入库前做的（`kb/rag_core.py::get_dialogue_texts`），
 *   因为向量库每条用 uuid4 当 id，不去重就是真重复 —— 重复片段会占掉检索
 *   的 top-k 名额。
 *
 *   所以界面上必须把 `imported` 和 `skipped` 分开说：只说 imported 会让
 *   "勾了 3 条只进 2 条"看起来像丢数据；只说 skipped 又像是失败了。
 */
export interface ImportDialogueResult {
  imported: number;
  skipped?: number;
  staged_segments?: number;
  kb_id: string;
  batch_id?: string;
}

// 聊天模式：auto 自动 / rag 强制检索 / agent 强制工具 / llm 纯对话 / hybrid 混合编排
//
// hybrid（10-14 加）：工作流与自主 Agent 在**一次任务内**动态换手。
// 它是唯一需要额外 SSE 事件（hybrid_start / workflow_node / mode_switch）才能正确
// 渲染的模式 —— 其余四种只靠 token + done 就够了。
//
// ⚠️ 后端开关 HYBRID_MODE_ENABLED=0 时它会**静默回退 agent**（不报错）：
// 表现为「收不到 hybrid_start」且「done.mode === 'agent'」。
// 所以前端不能假设选了 hybrid 就一定走混合，得看事件有没有来（见 useHybridTrace）。
export type ChatMode = "auto" | "rag" | "agent" | "llm" | "hybrid";

// 场景预设类型（ScenarioId）已随「场景选择撤出主界面」一并删除（10-14 晚）：
// 场景化入口统一走模板广场（/templates，完整启动包：建库 + 会话 + 提示词）。
// 后端 /api/chat 的 scenario 字段与 core/scenarios.py 机制保留（旧前端兼容 + 未来
// 模板联动可复用），前端不再传。

export interface SearchResult {
  title: string;
  body: string;
  href: string;
}

export interface Defaults {
  chunk_size: number;
  chunk_overlap: number;
  top_k: number;
  supported_extensions: string[];
}

// 引用溯源条目（SSE sources 事件）：点角标可展开原文
export interface RefSource {
  id: number;
  source: string;
  page: number | null;
  // 余弦相似度（越大越相关）。后端换算不了时给 null，此时前端不显示这一项
  score: number | null;
  // 依据强度分级，由后端按 RETRIEVE_LOW_SIM 判定。
  // 只给数字的话用户看不出 0.31 算不算数，所以要和百分比一起显示。
  strength?: "high" | "medium" | "low" | null;
  preview: string;
  text: string;
}

/**
 * 联网来源条目（SSE `web_sources` 事件）。
 *
 * 为什么不复用 RefSource：那个类型描述的是「资料库里的一份文件」——有页码、
 * 相似度、原文全文，点开是看原文；这里是「网上的一个页面」——有网址，点开是
 * 新窗口。混成一个类型，界面里到处要写 `if (有没有 url)` 来分叉，最后必然
 * 有一处漏判把本地文件渲染成可点外链。
 *
 * `id` 与正文里 `[n]` 的 n 是同一个数（后端在工具返回时就定好、全局递增），
 * 所以正文角标能直接按 id 找到对应网址。
 */
export interface WebSource {
  id: number;
  title: string;
  url: string;
  /** 展示用域名（后端已去掉 www.），取不到时为空串 */
  domain: string;
  snippet: string;
  /**
   * 搜索结果自带的发布日期，原样透传（"2 days ago" / "2026-07-22" / "Jul 15, 2025"）。
   * Serper 只对部分站点给这个字段，取不到时是空串 —— 前端据此决定显不显示，
   * 不要在这里做格式归一，各家站点的写法本来就不统一。
   */
  date?: string;
}

// 依据强度（SSE grounding 事件）：weak=true 时回答上方挂「依据较弱」提示条
export interface Grounding {
  strength: "high" | "medium" | "low" | "none";
  /** top1 余弦相似度，换算不出来时为 null */
  best: number | null;
  count: number;
  /** 检索到了，但全部不够格当依据 */
  weak: boolean;
}

// 库内文件正文预览：origin=archive 读原始留档，chunks 表示老库从向量片段拼回
export interface FilePreview {
  name: string;
  origin: "archive" | "chunks";
  truncated: boolean;
  content: string;
}

// 工具调用（SSE tool 事件）
export interface ToolEvent {
  name: string;
  input?: string;
  output?: string;
}

// ---------- 登录 ----------
export interface QuotaInfo {
  used: number;
  /** 0 表示不限 */
  limit: number;
  /** 管理员标记：为 true 时该维度无配额约束 */
  unlimited?: boolean;
}

// 与后端 api/auth.py 的 UserOut / MeOut 对齐
export interface UserOut {
  id: string;
  username: string;
  /**
   * 显示名。空串 = 没设过 → 全站显示 username。
   * 与 username 分开是因为两者代价差太远：昵称随便改，登录名要验密码还限次。
   */
  nickname?: string;
  /**
   * 头像。三种形态，用前缀区分（后端 account.normalize_avatar 校验）：
   *   ""              没设过 → 昵称首字母
   *   "icon:<name>"   内置图标（CornIcon 的 name）
   *   "img:<hash>"    上传的图片（第 2 批）
   */
  avatar?: string;
  /**
   * 登录名还能改几次（后端 MAX_NAME_CHANGES=3）。
   * 老后端不返回时按"还能改"处理 —— 缺字段就把按钮变灰是最糟的降级方向。
   */
  name_changes_left?: number;
}

export interface MeOut {
  user: UserOut;
  quota: Record<string, QuotaInfo>;
  /** 是否开发者（后端 ADMIN_USERNAMES）。老后端不返回这个字段 → undefined → 按 false 处理 */
  is_admin?: boolean;
}

// ---------- 用量与反馈看板（P2）----------
export interface UsageDay {
  /** YYYY-MM-DD */
  day: string;
  ask: number;
  embed: number;
}

export interface FeedbackItem {
  rating: "up" | "down";
  comment: string;
  title: string;
  sid: string;
  created_at: number;
}

export interface FeedbackStats {
  up: number;
  down: number;
  total: number;
  recent: FeedbackItem[];
}

export interface UsageSummary {
  quota: Record<string, QuotaInfo>;
  history: UsageDay[];
  /** 0 表示未启用每分钟限流 */
  rate_per_minute: number;
  feedback: FeedbackStats;
}

// ---------- 模板广场 ----------
export interface Template {
  id: string;
  name: string;
  category: string;
  /** 卡片图标语义 key（CornIcon 组件按知穗吉祥物风格渲染；历史 emoji 值回退 mini 知穗） */
  icon: string;
  tagline: string;
  description: string;
  tags: string[];
  suggested_kb_name?: string | null;
  session_title?: string;
  starter_prompt?: string | null;
  /** 点「设为每日委托」时预填的委托指令（持续型表述、不带频率词——频率由用户在表单里选）。
   *  目录里没有这个字段时前端回退到 tagline，所以后端没升级也不会空着。 */
  delegate_instruction?: string | null;
  recommended_mode?: string;
  philosophy?: string;
}

export interface ApplyTemplateResult {
  session_id: string;
  kb_id: string | null;
  title: string;
  starter_prompt?: string | null;
  recommended_mode?: string;
}

/* ============================================================
   用户兴趣标签（/api/profile）
   ────────────────────────────────────────────────────────────
   词表权威在 core/tag_taxonomy.py（30 条定稿，4 位组员清单合并）。
   前端**不硬编码任何标签名**：大类、可选项、说明全部由 /api/profile/tags 下发，
   加标签只改后端一处。这里的类型只描述形状。
   ============================================================ */

/** 单个标签的定义。type 是"目标/身份/任务/学科/方向/考试/场景/能力/状态"之一 */
export interface TagDef {
  name: string;
  category: string;
  type: string;
  /** 父标签名（如"考研数学"的父级是"考研备考"）；顶层为 null */
  parent: string | null;
  desc: string;
}

export interface TagGroup {
  category: string;
  tags: TagDef[];
}

export interface TagTaxonomy {
  categories: string[];
  types: string[];
  groups: TagGroup[];
  /** 首屏可选标签（已排除"能力/状态"两类） */
  pickable: string[];
  total: number;
}

/** 画像里的一条标签（带权重与元数据；词表外的自由主题 category/type 为 null） */
export interface ProfileTag {
  tag: string;
  weight: number;
  category: string | null;
  type: string | null;
  desc: string | null;
}

export interface ProfileSnapshot {
  /** 用户亲手勾选过的标签（覆盖式保存，前端据此回显已选状态） */
  picks: string[];
  /** 按权重降序的全部标签（含对话自动抽的） */
  tags: ProfileTag[];
  /** 按大类聚合的权重，权重降序 */
  categories: { category: string; weight: number }[];
  has_tags: boolean;
}

/** 「为你推荐」的一条：模板 id + 得分 + 命中的标签（前端拿来显示"因为你在准备英语四六级"） */
export interface TemplateRecommend {
  id: string;
  score: number;
  matched: string[];
}

/* ============================================================
   以下四组类型对应「队友分支 feat/ai-module-20261013」新增的端点。
   ────────────────────────────────────────────────────────────
   ⚠️ 契约来源是 `docs/前端页面与接口映射_v0.2.md`（按分支代码反推的字段清单），
   不是联调结果。用 tryRequest 拉取，端点不存在时页面走「接口待上线」态，
   所以字段对不上也不会让页面崩掉；联调时逐项核对即可。
   ============================================================ */

// ---------- P4 每日委托 /api/delegate ----------

export type DelegateFreq = "daily" | "weekdays" | "weekly" | "interval";
export type DelegateKind = "study_plan" | "review" | "encourage" | "custom";

/**
 * 触发计划。注意**不是 cron**：后端用的是结构化字段，
 * 前端把它渲染成「每天 21:00」这种人话，而不是把 crontab 摊给用户看。
 */
export interface DelegateSchedule {
  freq: DelegateFreq;
  /** 触发时刻 HH:MM；interval 频率下可为空 */
  at?: string | null;
  /** weekly 用：0=周一 … 6=周日（与后端 ScheduleIn.weekday 对齐） */
  weekday?: number | null;
  /** interval 用：每多少分钟 */
  every_minutes?: number | null;
  tz: string;
}

export interface Delegate {
  id: string;
  /** 3–32 字，建后不可改 */
  name: string;
  kind: DelegateKind;
  schedule: DelegateSchedule;
  /** 投递目标会话；null = 默认会话 */
  session_id: string | null;
  session_title?: string | null;
  kb_id: string | null;
  kb_name?: string | null;
  /** 给模型看的生成指令 */
  instruction: string;
  tone: string;
  max_chars: number;
  /** 后端没有 status，只有这个布尔 */
  enabled: boolean;
  /** 暂停时为 null —— 页面上要能看出「它不会再来了」 */
  next_run_at: number | null;
  /** 被 worker 自动停用时记录的原因（如投递目标会话失效）；手动暂停时为 null */
  last_error?: string | null;
  created_at?: number | null;
}

export type DelegateRunStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "skipped";

export interface DelegateRun {
  run_id: string;
  job_key: string;
  subject_id?: string | null;
  status: DelegateRunStatus;
  planned_at: number | null;
  started_at?: number | null;
  finished_at?: number | null;
  error?: string | null;
  tokens_out?: number | null;
  /** 排查「今天为什么没收到」先看这一列 */
  trigger: "schedule" | "manual";
}

// ---------- P5 每日总结 /api/report ----------

export interface ReportStats {
  date: string;
  conversation_count: number;
  message_count: number;
  user_msg_count: number;
  assistant_msg_count: number;
  /** { 小时: 条数 }，确定性统计，不走模型 */
  active_hours: Record<string, number>;
  tool_call_count: number;
}

/** 一条结论 + 它必须指得回的真实消息 id（后端强制溯源，指不到的直接丢） */
export interface ReportConclusion {
  text: string;
  source_refs: number[];
}

export type ReportStatus = "pending" | "generating" | "ready" | "empty";

export interface DailyReport {
  report_id: string;
  date: string;
  status: ReportStatus;
  stats: ReportStats;
  content: ReportConclusion[];
  model?: string | null;
  generated_at?: number | null;
  read_at?: number | null;
}

// ---------- P6 打卡 /api/checkin ----------
//
// ⚠️ 后端打卡语义 = 「那天有没有生成每日总结（status='ready'）」，所以打卡数据
// 全部从 daily_reports 派生，不另造数据。下面两个类型对应 api/checkin.py。

/** GET /api/checkin/streak：连续天数 / 最长连续 / 已打卡日期集合 */
export interface CheckinStreak {
  /** 所有打卡日（升序） */
  dates: string[];
  /** 当前连续天数 */
  streak: number;
  /** 近 30 天内最长连续 */
  longest: number;
}

/** GET /api/checkin/history 单条：{date, points, shared} */
export interface CheckinHistoryItem {
  /** YYYY-MM-DD */
  date: string;
  /** 当天总结的结论条数 */
  points: number;
  /** 是否已分享（POST /api/checkin/share 标记） */
  shared: boolean;
}

// ---------- P7 设置：Agent 工具元数据 /api/agent/tools ----------

export interface AgentTool {
  name: string;
  /** read = 只读；write = 会改状态，页面上要用暖色区分 */
  category: "read" | "write" | string;
  /** 延迟下发（false = 每轮全量带给模型） */
  defer: boolean;
  retryable: boolean;
  needs_approval: boolean;
  max_output_chars: number;
  cost_hint: string;
}

// ---------- P7 设置：调度进程 /api/schedule/* ----------

/** 只看心跳年龄：>300s stalled、>600s down */
export type WorkerHealth = "up" | "stalled" | "down";

export interface ScheduleHealth {
  status: WorkerHealth;
  last_heartbeat: number | null;
  pid?: number | null;
  age_seconds?: number | null;
}

export interface ScheduleJob {
  job_key: string;
  cron?: string | null;
  enabled: boolean;
  last_run_at?: number | null;
  last_status?: string | null;
}
// ============ P6 混合架构（工作流 × 自主 Agent）============
// 契约来源：docs/前端对接接口文档_混合架构_2026-10-14.md（林宏亮 v1）
// ⚠️ 后端代码截至 10-14 尚未推到任何远端分支（hybrid/ 与 api/hybrid.py 均为 0 文件），
//    所以这一段是**照文档实现**，等后端上线后要按真实响应回归一次。

// ---------- SSE 事件 ----------

/** hybrid_start：起跑即发，mode 是**初始**模式（workflow / agent），不是最终模式 */
export interface HybridStartEvent {
  run_id: string;
  mode: HybridRuntimeMode;
}

/** 工作流节点类型（NodeDef.type） */
export type HybridNodeType = "llm" | "tool" | "agent" | "condition" | "human";

export interface WorkflowNodeEnter {
  phase: "enter";
  node_id: string;
  type: HybridNodeType;
  /** 展示名；后端保证空则回填为 node_id */
  name: string;
}

export interface WorkflowNodeExit {
  phase: "exit";
  node_id: string;
  status: "ok" | "failed";
  /** 失败跳转目标；null 才会进入切换规则判定 */
  on_fail?: string | null;
  error?: string | null;
}

export type WorkflowNodeEvent = WorkflowNodeEnter | WorkflowNodeExit;

/** 模式切换；rule="manual" 是人工干预，"__max_switches__" 是切换次数达上限被强制收工 */
export interface ModeSwitchEvent {
  rule: string;
  /** 人类可读决策依据，可直接展示在轨迹里 */
  reason: string;
  from: HybridRuntimeMode;
  to: HybridRuntimeMode;
}

export type HybridRuntimeMode = "workflow" | "agent";

// ---------- 执行轨迹（消息上持久化，刷新后靠 /logs 重建）----------

/** 轨迹里的一段：要么是某个节点的一次进出，要么是一次模式切换 */
export type TraceItem =
  | {
      kind: "node";
      node_id: string;
      type: HybridNodeType;
      name: string;
      /** 进行中 / 已完成 / 失败 */
      status: "running" | "ok" | "failed";
      on_fail?: string | null;
      error?: string | null;
    }
  | { kind: "switch"; rule: string; reason: string; from: HybridRuntimeMode; to: HybridRuntimeMode };

// ---------- 工作流定义（管理面 CRUD）----------

export interface WorkflowNodeDef {
  id: string;
  type: HybridNodeType;
  name?: string;
  config?: Record<string, unknown>;
  timeout_seconds?: number | null;
  /** 节点内重试次数（先重试，再谈失败跳转/切换） */
  retry?: number;
  /** 失败跳转目标；null 才进入切换规则判定 */
  on_fail?: string | null;
  /** 执行到本节点主动让出给 Agent 模式 */
  requires_agent?: boolean;
}

export interface WorkflowEdgeDef {
  source: string;
  target: string;
  /** 受限表达式，仅 shared.<key> 作用域；null = 默认兜底边 */
  condition?: string | null;
}

export interface WorkflowDef {
  /** ⚠️ 不参与落库主键，后端会自己生成 12 位 hex 作为行 id；建议留空 */
  id?: string;
  name?: string;
  version?: number;
  entry?: string | null;
  nodes: WorkflowNodeDef[];
  edges?: WorkflowEdgeDef[];
  /** 0 = 用服务端默认 */
  timeout_seconds?: number;
}

export interface WorkflowIn {
  name: string;
  description?: string;
  definition: WorkflowDef;
}

export interface WorkflowRow {
  id: string;
  owner_id: string;
  name: string;
  description: string;
  definition: WorkflowDef;
  version: number;
  created_at: number;
  updated_at: number;
  /** 内置只读：改/删一律 404 */
  builtin: boolean;
}

// ---------- 切换规则 ----------

export type RuleAction = "to_agent" | "to_workflow" | "terminate";

export interface RuleIn {
  /** ≤ 40 字符。⚠️ 引擎实际只会上报 4 个 signal，自定义的存得下但不会触发 */
  signal: string;
  /** ≤ 500 字符；空 = 命中信号即触发。仅做语法校验，语义运行期才判 */
  condition?: string;
  action: RuleAction;
  reason?: string;
  /** 0–9999，数字小者先求值 */
  priority?: number;
  enabled?: boolean;
  /** 0–86400，显式 0 = 结构型规则豁免防抖 */
  cooldown_seconds?: number;
}

export interface RuleRow {
  id: string;
  owner_id: string;
  name: string;
  signal: string;
  condition?: string | null;
  action: RuleAction;
  reason?: string | null;
  priority: number;
  enabled: boolean;
  cooldown_seconds: number;
  builtin: boolean;
}

/** 引擎只会上报这 4 个 signal；界面上自定义 signal 要提示「不会触发」 */
export const KNOWN_SIGNALS = [
  "requires_agent",
  "node_failed",
  "budget_exhausted",
  "goal_satisfied",
] as const;

// ---------- 执行记录与日志 ----------

export type HybridRunStatus = "running" | "completed" | "failed" | "timeout" | "stopped";

export interface HybridRunRow {
  run_id: string;
  task_id: string;
  workflow_id?: string | null;
  status: HybridRunStatus;
  created_at: number;
  updated_at: number;
}

export interface HybridRunDetail extends HybridRunRow {
  owner_id: string;
  /** TaskContext 快照：goal / current_mode / workflow_state / agent_state / shared / mode_trace */
  context: Record<string, unknown>;
}

export type HybridLogEventType =
  | "node_enter"
  | "node_exit"
  | "tool_call"
  | "token"
  | "switch"
  | "error"
  | "status"
  | "human_intervention";

export interface HybridLogRow {
  id: number;
  event_type: HybridLogEventType;
  mode?: HybridRuntimeMode | null;
  node_id?: string | null;
  /** 仅 switch 事件 */
  decision?: { rule: string; reason: string; from: HybridRuntimeMode; to: HybridRuntimeMode } | null;
  /** 摘要级数据，后端序列化后截断 2000 字符 */
  payload?: Record<string, unknown>;
  ts: number;
}

// ---------- 开发者数据看板（/dashboard）----------
// 口径定义见 docs/数据看板_2026-10-27/口径与数据字典.md，
// 这里的字段名与后端 api/admin.py 的响应一一对应，改名要两边一起改。

export interface DashboardRange {
  days: number;
  /** Unix 秒 */
  start: number;
  end: number;
  tz: string;
}

export interface DashboardUsers {
  /** 注册总数。**当前快照，不随 days 变化** */
  total: number;
  /** 区间内新增 */
  new: number;
  /** 区间内被删除的账号数（清理测试号 / 用户注销）。
   *  没有这个数，「总数从 177 掉到 169」看起来就是用户流失，实际多半是清了测试号。
   *  老库还没建墓碑表时后端返回 0。 */
  purged?: number;
}

export interface DashboardCalls {
  total: number;
  ok: number;
  /** 预期行为（401 未登录探测 + 报告空态 404）：不算失败，也不进成功率分母 */
  expected: number;
  failed: number;
  /** 分母为 0 时为 null —— 不是 0，也不是 100 */
  success_rate: number | null;
  avg_ms: number;
  max_ms: number;
}

export interface DashboardSeriesItem {
  /** YYYY-MM-DD（业务时区） */
  day: string;
  calls: number;
  failed: number;
}

export type FeedbackStatus = "open" | "doing" | "done" | "closed";

export interface DashboardOverview {
  range: DashboardRange;
  users: DashboardUsers;
  calls: DashboardCalls;
  feedback: Record<FeedbackStatus, number>;
  series: DashboardSeriesItem[];
}

export interface ApiRankItem {
  path: string;
  method: string;
  /** 中文业务名（后端 core/api_labels.py 是唯一来源，前端不许自己拼） */
  label: string;
  calls: number;
  failed: number;
  avg_ms: number;
  max_ms: number;
}

export interface ApiRankResp {
  items: ApiRankItem[];
  total_paths: number;
  sort: "calls" | "slow" | "fail";
  range: { days: number; start: number; end: number };
}

/** 一条失败调用 + 人话原因（后端 core/dashboard.explain_status 是唯一来源） */
export interface FailureItem {
  path: string;
  method: string;
  status: number;
  /** 中文业务名 */
  label: string;
  /** 为什么失败 */
  hint: string;
  /** 是否为预期行为（未登录探测 / 报告空态）—— 不算系统报错 */
  expected: boolean;
  /** 仅 recent 里有 */
  ts?: number;
  day?: string;
  dur_ms?: number;
  /** 仅 top 聚合里有 */
  calls?: number;
}

export interface FailuresResp {
  days: number;
  recent: FailureItem[];
  need_attention: FailureItem[];
  ignorable: FailureItem[];
  top: FailureItem[];
  range: { days: number; start: number; end: number };
}

export interface UserFeedbackItem {
  id: number;
  uid: string;
  username: string;
  category: string;
  category_label: string;
  content: string;
  contact: string;
  page: string;
  status: FeedbackStatus;
  status_label: string;
  created_at: number;
  updated_at: number;
  /** 开发者回复。空串 = 还没回。用户侧「我提过的」靠它显示 */
  reply: string;
  /** 回复时间；没回过就是 null */
  replied_at: number | null;
}

export interface AdminFeedbackResp {
  items: UserFeedbackItem[];
  total: number;
  counts: Record<FeedbackStatus, number>;
}

export interface MyFeedbackResp {
  items: UserFeedbackItem[];
  total: number;
}

export interface FeedbackDict {
  categories: { value: string; label: string }[];
  statuses: { value: string; label: string }[];
  transitions: Record<string, string[]>;
  limits: {
    min_content: number;
    max_content: number;
    max_contact: number;
    max_page: number;
    /** 回复长度（管理员 → 用户）。后端下发的，前端不硬编码 */
    min_reply: number;
    max_reply: number;
  };
}

// ---------- 检索词校准（B3 预览接口 / B5 输入框提示条）----------

/**
 * 一次命中：raw 是用户原话里的口语词，canonical 是它对应的规范术语。
 *
 * ★ 增强是**追加**不是替换：原话里的 raw 一个字母都不会被改掉，
 *   只是检索时额外带上 canonical（口语词和术语都能被命中）。
 *
 * `start` / `end` 是**在 `original` 上的下标**，前端直接切片做高亮。
 * 千万不要拿 `raw` 去 `indexOf` —— 同一个词在一句话里出现两次时，
 * 两次都会定位到第一个位置，高亮就会错位。
 */
export interface EnhanceHit {
  raw: string;
  canonical: string;
  start: number;
  end: number;
}

/** 命中但故意没换的词（反例 / 歧义词），只给排障看，界面上不展示。 */
export interface EnhanceSkipped {
  raw: string;
  canonical: string;
  reason: string;
}

export interface EnhanceResult {
  /** 用户原话（可能与输入有前后空白差异，后端会 strip） */
  original: string;
  /**
   * 检索用文本 = **原话 + 空格 + `added` 里的术语**。
   *
   * 原话恒为前缀，后端只追加不改写（替换会造病句：`查重率`→`重复率检测率`）。
   * 界面上不要整行渲染它 —— 那等于把用户原话再念一遍。
   * 要展示"多加了什么"，渲染 `added`。
   */
  retrieval: string;
  /** 追加给检索的规范术语（已去重、保持出现顺序）。空数组 = 无需增强。 */
  added: string[];
  changed: boolean;
  hits: EnhanceHit[];
  skipped: EnhanceSkipped[];
  /** 歧义词的追问句，可直接渲染 */
  clarify: string[];
  /** 当前生效的词表（截图报 bug 时看这个就知道是哪版） */
  vocab: { file: string; terms: number };
  warnings: string[];
}

/* ==========================================================================
   今日一屏（GET /api/today）
   ==========================================================================
   首屏「今天」那条的唯一数据源。四个域压成一个请求，理由见 api/today.py
   模块注释（弱网下并发三个端点会有"时有时无"的观感）。

   ★ 这里**没有**任何由前端二次计算出来的字段：streak 是后端按
   "今天没打就从昨天起算"给的，today_done 是把 today_tasks 里 done 求和，
   days_left 由后端按 exam_date - today 算（前端自己减会跨时区差一天）。
   ========================================================================== */
export type TodaySnapshot = {
  /** 服务端认定的今天（YYYY-MM-DD），打卡与任务都按它对齐 */
  date: string;
  /**
   * 四件事一件都没建时为 false —— 首页整条不渲染。
   * 不渲染而不是渲染成空卡：一个从没用过的人看到「0 天 · 0 个任务」，
   * 只会以为这是又一张要填的表单。
   */
  has_any: boolean;
  checkin: {
    /** 连续打卡天数（今天还没打则从昨天起算，不是 0） */
    streak: number;
    longest: number;
    /** 今天是否已经生成过总结 */
    done_today: boolean;
  };
  cet: {
    has_plan: boolean;
    /** 距考试天数；没设考试日期时为 null */
    days_left: number | null;
    /** 整个计划的进度 */
    total: number;
    done: number;
    today_total: number;
    today_done: number;
    /** 今天第一个还没做完的任务标题；全做完或今天没任务时为 null */
    next_task: string | null;
  };
  delegate: {
    /** 启用中的委托数量 */
    enabled: number;
    /**
     * 最近一次执行的时间戳（秒）；没有启用的委托、或算出的时间已经过期时为 null。
     * ★ 后端**只给未来**的时间点：worker 停过一阵后 next_run_at 会停在过去，
     *   照原样返回会让首页写「21:00 每日学习复盘」，用户读成"今晚"，是假信息。
     */
    next_at: number | null;
    next_name: string | null;
  };
};

/* ==========================================================================
   推送（GET /api/push/status）
   ==========================================================================
   `supported` 只表示**服务端**具备发送能力（开关打开 + VAPID 密钥齐全）。
   浏览器侧能不能订阅由 lib/push.ts 自己探（PushManager / Notification 是否可用），
   两者都要满足才显示授权入口——只信一个会在没配密钥的部署里弹一个必然失败的框。
   ========================================================================== */
export type PushStatus = {
  supported: boolean;
  /**
   * ★★ **账号级**：名下任意一台设备订阅了就是 true。
   * 千万不要拿它渲染「这台设备已开启」——电脑上从没开过、只因手机开了，
   * 界面就会显示"已开启"。要判断本机，用 `findMyDevice()` 比对 `device_list`
   * 里的 `ep_key`（见 lib/push.ts）。
   */
  subscribed: boolean;
  /** 这个账号下有几台**还活着**的设备订阅了（停用的不算） */
  devices: number;
  /**
   * 名下所有设备，含已停用的（用户问"我手机怎么不响了"时得看得见"已失效"）。
   * 不含 endpoint / 密钥——服务端只给 sha256 短指纹，够认自己，不可逆。
   */
  device_list: PushDevice[];
  /** VAPID 公钥（base64url），浏览器 subscribe 时作为 applicationServerKey */
  key: string;
  /**
   * 这个人的提醒时刻 "HH:MM"。
   *
   * ★ 跟着 /api/remind 里用户自己设的值走，不是全站默认值。
   *   首页那条提示和设置页都要显示"每天几点"，让它们各自再请求一次 /api/remind
   *   不值得，所以由 status 顺手带回来。写死 20:00 的话，改成 7:30 的人
   *   会在界面上看到两个不同的时间，第一反应是"改了没生效"。
   */
  time: string;
  hour: number;
  minute: number;
};

/**
 * 邮件提醒的状态（GET /api/mail）。
 *
 * 为什么需要它：安卓的网页通知底层走 Google FCM，国内连不通，日历订阅又已经
 * 删掉，所以安卓上「提醒」只能打开 App 才看得见。邮件是那边唯一还能把人
 * 叫回来的通道 —— 服务端只在通知零投递时才发信，不是并行发两份。
 */
export type MailState = {
  /** 服务端配没配发信账号。false 时界面不能摆输入框，否则用户会白填一遍 */
  ready: boolean;
  /** 填过的地址（可能还没验证） */
  address: string | null;
  /** 验证过才算生效。填了但没验的地址在派发时会被跳过 */
  verified: boolean;
  /** 用户手动开关。verified 且 enabled 才真的发 */
  enabled: boolean;
  /** 有码等着填，且还没过期 */
  pending: boolean;
  /** 当前这个码还剩几秒有效 */
  code_expires_in: number;
  /** 还能试几次，用完要重新发 */
  attempts_left: number;
  /** 发件人地址，用户要能在自己邮箱里搜到「这封是谁发的」 */
  from: string | null;
};

/**
 * 日历订阅链接（GET /api/calendar/link）。
 *
 * 一条 token 同时管两份日历：`*.ics` 是每天一条的摘要，`courses.ics` 是课表。
 * 现在前端只用课表那份 —— 摘要日历 2026-11-11 删过（它固定时刻，
 * 和「每条待办各自设时间」对不上），课表不一样：每门课的真实上课时间就在里面。
 */
export type CalendarLink = {
  token: string;
  /** 每天摘要的时刻（HH:MM）。只在摘要那份日历里有意义 */
  time: string;
  subscribe_url: string;
  webcal_url: string;
  download_url: string;
  /** 课表订阅地址。可拼 `?before=N` 改课前提前几分钟 */
  courses_url: string;
  /** 同上，webcal:// 形式，iOS/macOS 点一下直接订阅 */
  courses_webcal_url: string;
  hour: number;
  minute: number;
};

/**
 * 上课提醒（`GET/PUT /api/course-reminder`）。
 *
 * 和 `CalendarLink` 是两条并行的路：iOS 走这里的系统通知（APNs 能到），
 * 安卓走日历那份 `.ics`（网页通知在国内走不通）。
 */
export type CourseReminderState = {
  enabled: boolean;
  /** 课前几分钟提醒。只可能是 `leads` 里的某一档 */
  lead_minutes: number;
  /** 可选档位，由后端给 —— 前端自己列一份就会出现两边对不上 */
  leads: number[];
  default_lead: number;
  /** 接下来几节课（已含提醒时刻），最多 3 条 */
  next: {
    title: string;
    start_at: number;
    fire_at: number;
    lead: number;
  }[];
  /** 本次保存真的重建了几条；只用于「保存后立刻看到下一次」 */
  synced: number | null;
};

/** 一台登记过的设备（GET /api/push/status 的 device_list 元素） */export type PushDevice = {
  /** 服务端 sub_id，移除时回传 */
  id: string;
  /** endpoint 的 sha256 前 12 位。前端拿本机 endpoint 算同一个值来认自己 */
  ep_key: string;
  /** 'ios' | 'android' | 'desktop' | 'other'（老数据可能是空串） */
  platform: string;
  ua: string;
  created_at: number;
  /** 最近一次真发成功的时间戳；0/null 表示还没成功发过 */
  last_ok_at: number | null;
  fail_count: number;
  /** true = 推送服务方说这条订阅没了（卸载/清数据），不会再发 */
  disabled: boolean;
};

/* ==========================================================================
   提醒时刻（GET / POST /api/remind）
   ==========================================================================
   一个值管两个通道：worker 到点发的 Web Push，和日历里那条每日事件的起点。
   所以设置页只放一处入口，别做成"推送时间"和"日历时间"两个开关。

   `steps` 由服务端给：分钟只能落 0 / 15 / 30 / 45，因为调度是每 15 分钟一跳。
   前端不要自己写死这四档——两边一旦不同步，用户设了时间也不会响。
   ========================================================================== */
export type RemindPref = {
  hour: number;
  minute: number;
  /** "HH:MM"，服务端格式化好了 */
  time: string;
  /** 没设置过时用的全站默认值，用于文案区分"你设的"和"默认的" */
  default_hour: number;
  default_minute: number;
  steps: number[];
  /** 服务端没配 VAPID 时为 false —— 此时改时间只影响日历 */
  push_enabled: boolean;
  /** 「每日回顾」那条提醒是否已经在库里（false = 从没设过，hour/minute 是默认值） */
  exists: boolean;
  /** 那条提醒现在是否开着。关掉后改时间会把它重新打开（POST 语义如此） */
  enabled: boolean;
};

export type PushSubscribeBody = {
  endpoint: string;
  keys: { p256dh: string; auth: string };
  ua?: string;
  platform?: string;
};

/**
 * 「发一条测试」的结果（POST /api/push/test）。
 *
 * `sent` 只是**推送服务方收下了几条投递请求**，不等于那几台的系统真弹了通知
 * ——Windows 的通知开关、勿扰模式、iOS 的专注模式都会在最后一步吞掉。
 * 所以多给一份 `items` 明细：设置页据此能说清"已投递到 2 台：iPhone ✓、
 * Windows 上的 Edge ✓"，把"我电脑为什么没弹"的排查方向挪到系统设置上。
 */
export type PushTestItem = {
  /** 对应 PushDevice.id */
  sub_id: string;
  ok: boolean;
  /** true = 推送服务方说这条订阅没了（410/404） */
  gone: boolean;
  err: string | null;
};

export type PushTestResult = {
  ok: boolean;
  sent: number;
  failed: number;
  gone: number;
  items: PushTestItem[];
};

// ---------- 提醒中心（2026-11-09，规格见 docs/提醒中心_实施规格） ----------

/** 重复规则。前四个必须与后端 core/reminder_core.py 的 FREQS 一致 */
export type ReminderFreq = "once" | "daily" | "weekdays" | "weekly" | "interval";

/** 到点之后做什么。`agent_deliver` 是「让 Agent 帮我写」，不是「弹个提醒」 */
export type ReminderAction = "notify" | "agent_deliver" | "suggest_focus" | "open_tool";

/** 一条提醒。`at` 是**来源值**，`override.at` 才是用户改过的值 —— 展示要用后者 */
export interface ReminderItem {
  id: string;
  owner_id: string;
  title: string;
  note: string | null;
  source: string;
  source_ref: string | null;
  /** 只覆盖 title / at；改标题不写回来源（委托那行一个字节都不动） */
  override: { title?: string; at?: string } | null;
  freq: ReminderFreq;
  at: string | null;
  /** 0 = 周一。`weekly` 必填 */
  weekday: number | null;
  every_minutes: number | null;
  trigger_kind: string;
  anchor: string | null;
  offset_minutes: number | null;
  action: ReminderAction;
  action_payload: unknown;
  /** 逗号分隔，如 `push` / `push,inapp` */
  channels: string;
  enabled: boolean;
  /** 秒级时间戳；`once` 触发后变 null */
  next_fire_at: number | null;
  created_at: number;
  updated_at: number;
  /** 委托来源才有：那一行现在的标题/正文（只读，改了也不回写） */
  source_title?: string;
  source_note?: string;
}

export interface ReminderSource {
  key: string;
  label: string;
  enabled: boolean;
}

export interface ReminderSummary {
  day: string;
  today: ReminderItem[];
  today_count: number;
  paused_count: number;
  upcoming: ReminderItem[];
}

export interface ReminderPrefs {
  quiet_start: string;
  quiet_end: string;
  daily_cap: number;
  cap_min: number;
  cap_max: number;
  active_windows: unknown;
}

/** 新建/局部修改提醒时的入参（字段与后端 ReminderIn/ReminderPatch 对齐） */
export interface ReminderDraft {
  title: string;
  note?: string | null;
  source?: string;
  freq?: ReminderFreq;
  at?: string | null;
  weekday?: number | null;
  every_minutes?: number | null;
  /** 绝对触发时刻（Unix 秒）。只有 `freq: "once"` 能用：过去的时间后端 400 */
  fire_at?: number | null;
  action?: ReminderAction;
  channels?: string | string[];
  enabled?: boolean;
}

/** 修改时的补丁：在草稿基础上多一个 override（只覆盖 title / at） */
export type ReminderPatchBody = Partial<ReminderDraft> & {
  override?: { title?: string; at?: string } | null;
};

/** 触发流水。status 五档见后端：sent / skipped_quiet / skipped_cap / skipped_empty / failed */
export interface ReminderFire {
  reminder_id: string;
  planned_at: number;
  fired_at: number;
  status: string;
  detail: string | null;
  clicked_at: number | null;
}

// ---------- 待办 /api/tasks（2026-11-10，规格见 docs/待办与提醒_Phase1实施单） ----------

/** 清单键。**英文键落库**，中文只在这里映射 —— 改文案不该触发数据迁移 */
export type TaskListKey = "inbox" | "course" | "homework" | "club" | "life" | "exam";

/** 0 普通 / 1 日常 / 2 重要 / 3 紧急。**有序**：后端 ORDER BY priority DESC 靠它排 */
export type TaskPriority = 0 | 1 | 2 | 3;

export type TaskStatus = "open" | "done";

/** 待办的联动提醒状态。`last_*` 来自最近一条 reminder_fires，用于显示「已响 / 已跳过」 */
export interface TaskReminderState {
  id: string;
  enabled: boolean;
  next_fire_at: number | null;
  last_status: string | null;
  last_fired_at: number | null;
  last_detail: string | null;
}

/**
 * 一条待办。
 *
 * ★ `due_at`（最晚什么时候交）与 `remind_at`（什么时候提醒我）是**两个字段**，
 *   永远不要在前端合成一个：混起来之后用户会问「我明明设了提醒，
 *   为什么显示今天是截止」。只设 `due_at` 不会响（后端不建联动提醒）。
 * ★ 时间一律 **Unix 秒**，不是毫秒。
 */
export interface Task {
  id: string;
  owner_id: string;
  title: string;
  note: string | null;
  list_key: TaskListKey;
  /**
   * 用户自选的图标名（`CornIcon` 的 key）。`null` = 没选过，
   * 界面按清单兜底（见 `taskMark`）—— 所以老数据升级后外观不变。
   */
  icon: string | null;
  priority: TaskPriority;
  status: TaskStatus;
  due_at: number | null;
  remind_at: number | null;
  completed_at: number | null;
  recurrence: "none";
  parent_id: string | null;
  sort_order: number;
  created_at: number;
  updated_at: number;
  /** null = 没设提醒时间（或联动提醒还不存在） */
  reminder: TaskReminderState | null;
}

export interface TaskDraft {
  title: string;
  note?: string | null;
  list_key?: TaskListKey;
  icon?: string | null;
  priority?: TaskPriority;
  due_at?: number | null;
  remind_at?: number | null;
}

/** 局部更新。显式 `null` = 清空（端点用 exclude_unset，能把 null 传下去） */
export interface TaskPatch {
  title?: string;
  note?: string | null;
  list_key?: TaskListKey;
  icon?: string | null;
  priority?: TaskPriority;
  due_at?: number | null;
  remind_at?: number | null;
  /**
   * 手排序号。**0 = 没排过**（落回自动规则），正数才是用户排的位置。
   *
   * ★ 批量排序走 `POST /api/tasks/reorder`，不要逐条 PATCH —— 拖一次发 N 个
   *   请求，中途失败会留下半新半旧的顺序。
   */
  sort_order?: number;
}

/**
 * 批量动作。
 *
 * `move` 必须带 `list_key`：不传时后端会 400 而不是默认搬进 inbox ——
 * 漏传一次就把几十条静默挪走，比报错难查得多。
 */
export type TaskBulkAction = "complete" | "reopen" | "delete" | "move";

export interface TaskSummary {
  today: number;
  overdue: number;
  upcoming: number;
  all_open: number;
  completed: number;
  by_list: Partial<Record<TaskListKey, number>>;
  /** 今天 / 逾期里最早的一条；没有就是 null */
  next: Task | null;
}

// ==================== AI 整理（capture）====================
//
// 这一组是 `/api/capture/parse` 的返回形状，**不是**数据库模型。
// 三处硬边界：
//   1. `kind` 只有两种，前端不能把两类混成一张卡：
//      task 有 ○ 能勾完成，reminder 是 ◇ 只能推迟/暂停/关来源。
//   2. `due_at` 与 `remind_at` 分开。只设 due_at 不会响 —— 界面上要写出来。
//   3. `source_start/source_end` 由**服务端**算，前端不算 offset。
//      null = 找不到原文，此时不高亮、不给编号，但条目仍然保留。

export type CaptureKind = "task" | "reminder";

export interface CaptureQuestion {
  id: string;
  text: string;
  options: { label: string; value: string }[];
}

/** 提醒的重复规则。只有 reminder 有；task 的 `recurrence` 恒为 null。 */
export interface CaptureRecurrence {
  freq: ReminderFreq;
  at?: string | null;
  weekday?: number | null;
  every_minutes?: number | null;
  /** 只允许和 `freq="once"` 一起出现 */
  fire_at?: number | null;
}

export interface CaptureProposal {
  id: string;
  kind: CaptureKind;
  title: string;
  note: string | null;
  /** 只有 task 有值；reminder 为 null（它不属于任何清单） */
  list_key: TaskListKey | null;
  /**
   * 用户在这一屏里为它挑的图标（`CornIcon` 名）。`null` = 没挑，落库为空，
   * 列表按清单自动兜底。模型不参与猜图标 —— 猜错的成本比"用默认的"高。
   */
  icon: string | null;
  priority: TaskPriority;
  due_at: number | null;
  remind_at: number | null;
  recurrence: CaptureRecurrence | null;
  /** 必须是原话的连续子串 */
  source_text: string;
  source_start: number | null;
  source_end: number | null;
  needs_confirm: boolean;
  questions: CaptureQuestion[];
}

export interface CaptureParseResult {
  items: CaptureProposal[];
  /** 没被采纳的句子 / 超出的条目，一句话说明 */
  unresolved: string[];
}

// ============================================================
// 论坛中心（/forum，2026-11-14）
//
// 与公开知识库留言板共用同一套楼层结构（`BoardPost`）：一个话题就是
// 「楼主一层 + 若干回复」，`posts[0]` 固定是楼主。所以详情页的楼层渲染
// 和 `KbBoard` 长得一样，不需要第二套。
//
// 区别只在：话题有 `title` / `cat`（板块），并且可以挂一个来源库。
//
// ★ 刻意**没有**「已解决 / 未解决」：这个论坛是讨论与分享用的，不是答疑系统。
//   没有"提问—解决"的闭环，也就不该有"这题答上了吗"这个状态。
// ============================================================

/** 两种排序。`hot` 不支持翻页（后端按分数排序，游标会失效） */
export type ForumSort = "new" | "hot";

export interface ForumCat {
  key: string;
  label: string;
  hint: string;
  /** 这个板块下的话题数 */
  n: number;
  /** 我能不能往这里发（站务公告只有管理员能发） */
  postable: boolean;
}

export interface ForumCats {
  cats: ForumCat[];
  /** 全部板块的话题总数 */
  total: number;
  /** 全站回复条数 */
  replies: number;
  /** 今日新增条数（话题 + 回复） */
  today: number;
  /**
   * 举报原因选项。**由服务端下发，不在前端抄一份** —— 举报理由和"下架原因"
   * 是两套词（读者不会举报"与板块无关"，但必须能举报"违法信息"），
   * 前端硬编码一份就会和 backend 的枚举慢慢漂开。
   */
  report_reasons: ForumModReason[];
}

/** 列表里的一条话题 */
export interface ForumTopic {
  id: number;
  /** 标题。用户没填时后端用正文前 24 字兜底，所以这里一定有值 */
  title: string;
  /** 正文摘要，纯文本、已压成一行 */
  excerpt: string;
  cat: string;
  cat_label: string;
  /** 发布时间（Unix 秒） */
  at: number;
  /** 楼里最后一次活动的时间；没回复时等于 at */
  last_at: number;
  reply_n: number;
  mine: boolean;
  /**
   * 先发后审的两种状态。
   *
   * 后端库里正常态是空串，到这一层已经被归一成 "live" —— 前端只判这两个值，
   * 不用在每处同时想着 "" 和 "live" 是同一件事。
   * 没有"审核中"：发出去就是公开的，多一个中间态会让人以为要等审核。
   */
  status: "live" | "removed";
  /** 下架原因（中文 label，未下架时为空串）。**只在自己发的帖里给** */
  removed_reason: string;
  removed_at: number;
  /** 作者点过申诉的时间；0 = 没申诉过 */
  appeal_at: number;
  /** 被举报次数。列表里不显示，管理台用它排序 */
  report_n: number;
  author: { name: string; avatar: string; admin: boolean };
  /** 来源知识库；不挂库的话题为 null */
  kb: { id: string; name: string; official: boolean } | null;
  /**
   * 配图的内容哈希列表（最多 9 个），空数组 = 没图。取图用 `/api/forum/img/<hash>`。
   *
   * 存哈希而不是 URL：同一张图被多人发时磁盘上只有一份，而且 URL 里带内容指纹
   * 意味着这张图能永久缓存（图一变哈希就变，不会看到旧图）。
   */
  images: string[];
  /**
   * 楼中楼预览：最后一条可见回复。
   *
   * 没有回复时是 `null`（而不是一个空 body 的对象）—— 前端判真假一次就够，
   * 不用再判 body 是否为空。列表卡只显示一行，长回复由后端截到摘要长度。
   */
  last: {
    /** 最后一条回复的正文，已压成一行并截断 */
    body: string;
    /** 回复者昵称；查不到（注销）时为空串 */
    by: string;
    /** 最后这条是我自己回的 */
    mine: boolean;
  } | null;
}

/** `POST /api/forum/upload` 的返回 */
export interface ForumUpload {
  /** 内容指纹，发帖时把它放进 `images` 数组 */
  hash: string;
  /** 方便直接塞进 `<img src>` */
  url: string;
  w: number;
  h: number;
  bytes: number;
  /** 服务端是否重压过（体积超标 / 带 EXIF 的原图才会走这条路） */
  rescued: boolean;
}

/** `GET /api/forum/mine` —— 这台设备发过的帖，含已下架的 */
export interface ForumMinePage {
  posts: ForumTopic[];
  /** 其中被下架了几条。左栏角标用它，0 时不显示 */
  removed_n: number;
}

/** 管理台的一个下架原因选项 */
export interface ForumModReason {
  key: string;
  label: string;
}

/** `GET /api/forum/mod/queue` —— 待审 + 被申诉 + 被举报 + 四个计数 */
export interface ForumModQueue {
  stats: { pending: number; appeals: number; removed: number; reported: number };
  pending: ForumTopic[];
  appeals: ForumTopic[];
  /** 被读者举报、还没下架的。和 `pending` 会重叠（一条新帖可能同时两处都在） */
  reported: ForumTopic[];
  reasons: ForumModReason[];
}

export interface ForumTopicPage {
  posts: ForumTopic[];
  /**
   * 置顶的站务公告（最多 2 条，最新的在前）。
   * 只有「全部话题 + 第一页 + 没搜索」时非空，其余场景后端给空数组，
   * 所以前端不用自己判断"该不该显示公告"。
   */
  pinned?: ForumTopic[];
  has_more: boolean;
}

export interface ForumDetail {
  topic: ForumTopic;
  /** 楼主正文（完整，不截断）。`posts[0].body` 也是它 */
  body: string;
  /** 楼主 + 全部回复。第 0 项恒为楼主；**下架时为空数组** */
  posts: BoardPost[];
  stickers: string[];
  /** 回复字数上限，由后端下发 —— 前端不硬编码。下架时是 0（= 不能回复） */
  max_reply: number;
  /**
   * 这条被下架了。
   *
   * 原链接仍然 200：直接 404 会让人以为网站坏了 —— 尤其链接可能已经
   * 被转发到班级群里。三种状态字段在正常态也返回（false / "" / false），
   * 所以前端不用判 `"removed" in data`。
   */
  removed: boolean;
  /** 下架原因。**只在自己发的帖里有值**，别人恒为空串 */
  reason: string;
  /** 还能不能点申诉（作者且没申诉过） */
  can_appeal: boolean;
}
