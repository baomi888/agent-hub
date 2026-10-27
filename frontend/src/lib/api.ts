// 后端 API 封装：统一错误处理 + 类型化（相对路径走 rewrites 代理）
import type { Conversation, KbInfo, KbFile, FilePreview, SearchResult, Defaults, Message } from "./types";
import { detailToMessage } from "./errors";
import type { MeOut, UserOut, Template, ApplyTemplateResult, UsageSummary } from "./types";
import type { ProfileSnapshot, TagTaxonomy, TemplateRecommend } from "./types";
import type { EnhanceResult } from "./types";
import type {
  AgentTool,
  DailyReport,
  Delegate,
  DelegateRun,
  ScheduleHealth,
  ScheduleJob,
  HybridLogRow,
  HybridRunDetail,
  HybridRunRow,
  RuleIn,
  RuleRow,
  WorkflowIn,
  WorkflowRow,
  CheckinStreak,
  CheckinHistoryItem,
  DashboardOverview,
  ApiRankResp,
  AdminFeedbackResp,
  MyFeedbackResp,
  FeedbackDict,
  UserFeedbackItem,
} from "./types";

/**
 * 登录态失效的广播事件名。
 *
 * 后端现在默认对所有业务接口鉴权，过期 / 未登录一律 401。API 层自己不该
 * 跳页面（那是 UI 的事），所以统一在这里广播，由 useAuth 收听并把界面
 * 切回登录门 —— 否则用户看到的就是"点什么都没反应"。
 */
export const UNAUTHORIZED_EVENT = "baomi:unauthorized";

function notifyUnauthorized() {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  }
}

/**
 * 登录态「建立」的广播事件名 —— 与 UNAUTHORIZED_EVENT 对称。
 *
 * 为什么需要：四屏（/delegate /report /checkin /settings /flow）的页面组件
 * 自己持有数据加载 effect，而登录门在 ConsoleShell 那一层。未登录打开页面时
 * 页面 effect 照常执行、请求 401、进入降级态；而「登录完成」发生在
 * ConsoleShell 自己的 useAuth 实例里 —— 不广播的话页面感知不到，会一直停在
 * 登录前的失败结果上（界面显示「接口待上线」，直到用户手动刷新）。
 * 这里在凭证建立（登录/注册 200、cookie 已种上）时广播，让各 useAuth 实例
 * 和页面级数据重新拉一次。
 */
export const AUTHORIZED_EVENT = "baomi:authorized";

function notifyAuthorized() {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(AUTHORIZED_EVENT));
  }
}

/**
 * 统一解析后端错误体：`detail` 可能是字符串（业务错误）也可能是数组（FastAPI 422），
 * 交给 `errors.ts::detailToMessage` 归一成人话 —— 直接 `new Error(body.detail)` 会把
 * 数组/对象转成 "[object Object]"，这正是委托页那个看不懂的报错气泡的成因。
 */
async function httpError(res: Response): Promise<Error> {
  let detail = `HTTP ${res.status}`;
  if (res.status === 401) {
    notifyUnauthorized();
    return new Error("未登录或登录已过期，请重新登录");
  }
  // 429 有两种：每分钟限流带 retry_after，每日配额带 kind/limit。
  // 后端 detail 已是中文说明，这里只补一个"多久能再试"的可操作提示，
  // 避免用户把它当成"系统坏了"而不停重试。
  let retryAfter: number | undefined;
  try {
    const body = (await res.json()) as { detail?: unknown; retry_after?: number };
    detail = detailToMessage(body.detail, detail);
    retryAfter = typeof body.retry_after === "number" ? body.retry_after : undefined;
  } catch {
    /* 忽略非 JSON 错误体 */
  }
  if (res.status === 429) {
    const wait = retryAfter && retryAfter > 0 ? `（约 ${Math.ceil(retryAfter)} 秒后可重试）` : "";
    return new Error(`${detail}${wait}`);
  }
  return new Error(detail);
}

// 默认请求超时（毫秒）。后端部分端点同步跑 LLM（打卡/总结/委托执行约 20–40s），
// 一旦上游卡死，前端既不能取消也看不到"还要多久"——给一个上限，超时直接 fail，
// 页面转入错误提示而不是永远转圈。长操作（生成/执行）可传 opts.timeoutMs 单独放宽。
const DEFAULT_TIMEOUT_MS = 60000;

async function request<T>(
  path: string,
  init?: RequestInit,
  opts?: { timeoutMs?: number }
): Promise<T> {
  // Next dev 对带尾斜杠的 /api/* 会 308 重定向，浏览器 fetch 跟随时偶尔丢 body/cookie，
  // 直接把尾斜杠去掉，请求落到 rewrite 目标，彻底绕开重定向。
  const cleanPath = path.length > 1 && path.endsWith("/") ? path.slice(0, -1) : path;
  const controller = new AbortController();
  const timer: ReturnType<typeof setTimeout> | undefined =
    opts?.timeoutMs !== 0
      ? setTimeout(() => controller.abort(), opts?.timeoutMs ?? DEFAULT_TIMEOUT_MS)
      : undefined;
  try {
    const res = await fetch(cleanPath, {
      // 登录态在 HttpOnly Cookie 里，同源也要显式带；不写这行浏览器会丢弃
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      ...init,
      signal: controller.signal,
    });
    if (!res.ok) {
      throw await httpError(res);
    }
    return res.json() as Promise<T>;
  } catch (e) {
    // 超时（AbortController 触发）转成可懂的提示，而不是一堆原生 abort 噪音
    if ((e as Error)?.name === "AbortError") {
      throw new Error("请求超时，请稍后重试或检查网络");
    }
    throw e;
  } finally {
    if (timer) clearTimeout(timer);
  }
}

/**
 * 业务侧 404（区别于「端点不存在」）。
 * 目前唯一的场景是 `GET /api/report/{date}` 的「今日总结尚未生成」——
 * 这不是错误，页面该渲染成「今天还没有总结 + 生成按钮」。
 */
export class BusinessNotFoundError extends Error {
  constructor(detail: string) {
    super(detail);
    this.name = "BusinessNotFoundError";
  }
}

/**
 * 探测式请求：把「后端还没有这个端点」和「真的出错了」分开。
 *
 * 为什么需要它：`/api/delegate`、`/api/report`、`/api/agent/tools`、`/api/schedule/*`
 * 目前只存在于队友的 `feat/ai-module-20261013` 分支，本分支跑起来会 404。
 * 页面要是把 404 当错误抛，用户看到一片红；要是静默吞掉，又变成「点了没反应」。
 * 所以显式区分：**端点不存在 → 返回 null**，页面亮一块「接口待上线」；
 * 其余错误照常抛，走正常的错误提示。
 *
 * 判定依据：FastAPI 找不到路由时 `detail` 恒为 `"Not Found"`。
 * 业务侧的 404（如 report 的「今日总结尚未生成」）detail 是中文说明，
 * 因此不会被误判成「未上线」。
 *
 * ⚠️ 这是**按字符串猜**，不是按契约问。代价不对称：把「业务 404」误判成「未上线」
 * 用户看不出差别，但反过来 —— 后端改了 detail 的大小写/空白，或合入的端点
 * 路径与这里拼写不一致 —— 一个**真实的集成 bug 会长得和一个还没做的功能一模一样**，
 * 不报错、不打日志。所以匹配刻意放宽：大小写不敏感 + 容忍首尾空白。
 * 终局解法是让后端给一份能力清单（见 docs/前后端可更新点_2026-10-14.md 后端 #2），
 * 与队友那批接口一起定契约时再换。
 */
async function tryRequest<T>(
  path: string,
  init?: RequestInit,
  opts?: { timeoutMs?: number }
): Promise<T | null> {
  const cleanPath = path.length > 1 && path.endsWith("/") ? path.slice(0, -1) : path;
  const controller = new AbortController();
  const timer: ReturnType<typeof setTimeout> | undefined =
    opts?.timeoutMs !== 0
      ? setTimeout(() => controller.abort(), opts?.timeoutMs ?? DEFAULT_TIMEOUT_MS)
      : undefined;
  try {
    const res = await fetch(cleanPath, {
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      ...init,
      signal: controller.signal,
    });
    if (res.ok) return res.json() as Promise<T>;
    if (res.status === 405 || res.status === 501) return null;
    if (res.status === 404) {
      // 否则 detail.trim() 会在数组上抛 TypeError，把一次普通 404 变成未捕获异常。
      let rawDetail: unknown = "";
      try {
        rawDetail = ((await res.json()) as { detail?: unknown }).detail ?? "";
      } catch {
        /* 非 JSON 错误体：当作未上线处理 */
      }
      const detail = typeof rawDetail === "string" ? rawDetail : "";
      // 空 detail：后端没给 JSON 错误体，多数也是路由未匹配
      // 其余按大小写不敏感匹配，理由见上方注释「按字符串猜」那一段
      if (detail === "" || /^not found$/i.test(detail.trim())) return null;
      // 业务 404：让调用方自己去认（report 页会转成「今天还没有总结」）
      throw new BusinessNotFoundError(detail);
    }
    throw await httpError(res);
  } catch (e) {
    // 超时（AbortController 触发）转成可懂的提示，而不是一堆原生 abort 噪音
    if ((e as Error)?.name === "AbortError") {
      throw new Error("请求超时，请稍后重试或检查网络");
    }
    throw e;
  } finally {
    if (timer) clearTimeout(timer);
  }
}

/**
 * 检索词校准预览（B3 接口）。**失败一律返回 null，绝不抛错。**
 *
 * 为什么和 api 里其它方法不一样：
 * 这是一个"用户每打一句话就可能在后台跑一次"的辅助能力，不是用户主动发起的业务操作。
 * 它挂在输入框上方，一旦限流（429）或超时就弹红字，用户会以为聊天坏了——
 * 而实际上换词只是个增益，没有它检索照样跑。
 *
 * 所以：连不上、被限流、没登录，全部静默降级为"不提示"。
 * 唯一例外是不传 signal：超时由 tryRequest 自己的 AbortController 管（8s），
 * 这个接口是纯本地计算，正常都在 20ms 内返回。
 */
export async function previewEnhance(
  text: string,
  withClarify = true
): Promise<EnhanceResult | null> {
  const q = text.trim();
  if (q.length < 2) return null;
  try {
    return await tryRequest<EnhanceResult>(
      "/api/query/enhance",
      { method: "POST", body: JSON.stringify({ text: q, with_clarify: withClarify }) },
      { timeoutMs: 8000 }
    );
  } catch {
    // 429 / 401 / 网络错误：提示条不出现就是了，不要打扰正在打字的人
    return null;
  }
}

export const api = {
  // ---------- 会话 ----------
  listSessions: () =>
    request<{ sessions: Conversation[] }>("/api/sessions/"),
  createSession: (title: string = "新对话") =>
    request<Conversation>("/api/sessions/", {
      method: "POST",
      body: JSON.stringify({ title }),
    }),
  getSession: (sid: string) =>
    request<Conversation & { messages: Message[] }>(`/api/sessions/${sid}/`),
  renameSession: (sid: string, title: string) =>
    request<Conversation>(`/api/sessions/${sid}/`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),
  bindKb: (sid: string, kb_id: string | null) =>
    request<Conversation>(`/api/sessions/${sid}/`, {
      method: "PATCH",
      body: JSON.stringify({ kb_id }),
    }),
  deleteSession: (sid: string) =>
    request<{ ok: boolean }>(`/api/sessions/${sid}/`, { method: "DELETE" }),
  deleteMessageTurn: (sid: string, msgId: number) =>
    request<{ ok: boolean; deleted: number }>(
      `/api/sessions/${sid}/messages/${msgId}/`,
      { method: "DELETE" }
    ),

  // ---------- 知识库 ----------
  listKbs: () => request<{ kbs: KbInfo[] }>("/api/kb/"),
  createKb: (name: string) =>
    request<{ kb_id: string; name: string }>("/api/kb/create", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  deleteKb: (kbId: string) =>
    request<{ ok: boolean }>(`/api/kb/${kbId}/`, { method: "DELETE" }),
  uploadFiles: (kbId: string, files: FileList | File[], chunkSize = 500, chunkOverlap = 50) => {
    const fd = new FormData();
    Array.from(files).forEach((f) => fd.append("files", f));
    return fetch(
      `/api/kb/${kbId}/upload?chunk_size=${chunkSize}&chunk_overlap=${chunkOverlap}`,
      // FormData 请求不能手动设 Content-Type（boundary 会被覆盖），
      // 但 credentials 必须带上，否则上传会被当成未登录
      { method: "POST", body: fd, credentials: "include" }
    ).then(async (res) => {
      if (!res.ok) throw await httpError(res);
      return res.json() as Promise<{ kb_id: string; chunks: number; files: string[] }>;
    });
  },
  // ---------- 知识库文件级管理 ----------
  listKbFiles: (kbId: string) =>
    request<{ kb_id: string; chunks: number; files: KbFile[] }>(`/api/kb/${kbId}/files`),
  deleteKbFile: (kbId: string, name: string) =>
    request<{ ok: boolean; removed: number; chunks: number }>(`/api/kb/${kbId}/files`, {
      method: "DELETE",
      body: JSON.stringify({ name }),
    }),
  rebuildKb: (kbId: string, chunkSize: number, chunkOverlap: number) =>
    request<{ kb_id: string; chunks: number; files: KbFile[] }>(`/api/kb/${kbId}/rebuild`, {
      method: "POST",
      body: JSON.stringify({ chunk_size: chunkSize, chunk_overlap: chunkOverlap }),
    }),
  // 预览库内文件正文（老库没有留档时后端会用向量片段拼回）
  getFileContent: (kbId: string, name: string) =>
    request<FilePreview>(
      `/api/kb/${encodeURIComponent(kbId)}/file-content?name=${encodeURIComponent(name)}`
    ),
  searchPreview: (keyword: string, maxResults = 10) =>
    request<{ keyword: string; results: SearchResult[] }>(
      "/api/kb/import-search",
      { method: "POST", body: JSON.stringify({ keyword, max_results: maxResults }) }
    ),
  importUrls: (kbId: string, urls: string[]) =>
    request<{ total: number; ok: number; failed: number }>(
      `/api/kb/${kbId}/import-batch`,
      { method: "POST", body: JSON.stringify({ urls }) }
    ),
  // 单条抓：批量接口一次性跑完不给反馈，逐条调它才能报「正在抓第 3/10 篇」
  importUrl: (kbId: string, url: string, chunkSize = 500, chunkOverlap = 50) =>
    request<{ url: string; title: string; chunks: number; file: string }>(
      `/api/kb/${encodeURIComponent(kbId)}/import-url`,
      {
        method: "POST",
        body: JSON.stringify({
          url,
          chunk_size: chunkSize,
          chunk_overlap: chunkOverlap,
        }),
      }
    ),

  // ---------- 消息反馈（赞 / 踩）----------
  // 同一条消息重复提交是"改"不是"追加"：点赞再点踩后端只留最后一次。
  // comment 是「踩」时可选的改进原因，后端覆盖写入同一行。
  sendFeedback: (
    sid: string,
    messageId: number,
    rating: "up" | "down",
    comment?: string,
  ) =>
    request<{ ok: boolean; rating: string }>("/api/feedback/", {
      method: "POST",
      body: JSON.stringify({ sid, message_id: messageId, rating, comment: comment ?? "" }),
    }),

  // ---------- 配置 ----------
  defaults: () => request<Defaults>("/api/config/defaults"),

  // ---------- 聊天附件上传（图片理解）----------
  uploadChatAttachments: (files: FileList | File[]) => {
    const fd = new FormData();
    Array.from(files).forEach((f) => fd.append("files", f));
    return fetch("/api/chat/upload", {
      method: "POST",
      body: fd,
      credentials: "include",
    }).then(async (res) => {
      if (!res.ok) throw await httpError(res);
      return res.json() as Promise<{
        files: { filename: string; path: string; size: number; type: string }[];
      }>;
    });
  },

  // ---------- 登录 ----------
  authConfig: () => request<{ allow_signup: boolean }>("/api/auth/config/"),
  login: async (username: string, password: string) => {
    const u = await request<UserOut>("/api/auth/login/", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    // cookie 已种上：广播「登录成功」，让页面级数据重新加载（与 401 的收回对称）
    notifyAuthorized();
    return u;
  },
  register: async (username: string, password: string) => {
    const u = await request<UserOut>("/api/auth/register/", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    notifyAuthorized();
    return u;
  },
  logout: () => request<{ ok: boolean }>("/api/auth/logout/", { method: "POST" }),
  me: () => request<MeOut>("/api/auth/me/"),

  // ---------- 用量与反馈看板 ----------
  usageSummary: () => request<UsageSummary>("/api/usage/summary"),

  // ---------- 用户反馈（DeepSeek 式：分类按钮 + 一段话）----------
  // 与下面的 admin.* 分开：这个走 /api/user-feedback（登录即可、只看自己的），
  // admin.* 走 /api/admin（非管理员后端直接 403）
  feedbackDict: () => request<FeedbackDict>("/api/user-feedback/dict"),
  submitFeedback: (body: { category: string; content: string; contact?: string; page?: string }) =>
    request<{ ok: boolean; id: number }>("/api/user-feedback/", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  myFeedback: (limit = 20, offset = 0) =>
    request<MyFeedbackResp>(`/api/user-feedback/mine?limit=${limit}&offset=${offset}`),

  // ---------- 开发者数据看板（仅管理员）----------
  admin: {
    overview: (days = 7) => request<DashboardOverview>(`/api/admin/overview?days=${days}`),
    apis: (days = 7, limit = 20, sort: "calls" | "slow" | "fail" = "calls") =>
      request<ApiRankResp>(`/api/admin/apis?days=${days}&limit=${limit}&sort=${sort}`),
    feedback: (status = "", limit = 20, offset = 0) =>
      request<AdminFeedbackResp>(
        `/api/admin/feedback?status=${encodeURIComponent(status)}&limit=${limit}&offset=${offset}`
      ),
    patchFeedback: (id: number, status: string) =>
      request<{ ok: boolean; status: string }>(`/api/admin/feedback/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      }),
    dict: () => request<FeedbackDict>("/api/admin/dict"),
  },

  // ---------- 模板广场 ----------
  templates: () =>
    request<{ templates: Template[]; recommended?: TemplateRecommend[] }>("/api/templates/"),
  applyTemplate: (id: string) =>
    request<ApplyTemplateResult>(`/api/templates/${id}/apply`, {
      method: "POST",
    }),

  // ---------- 用户兴趣标签（画像）----------
  //
  // 路径不带尾斜杠：后端路由是 ""/"/tags"，带斜杠会先吃一个 308。
  // saveTags 是覆盖式（PUT）：传什么就是最终选择，取消勾选靠"不传"表达。
  tagTaxonomy: () => request<TagTaxonomy>("/api/profile/tags"),
  myProfile: () => request<ProfileSnapshot>("/api/profile"),
  saveTags: (tags: string[]) =>
    request<ProfileSnapshot>("/api/profile/tags", {
      method: "PUT",
      body: JSON.stringify({ tags }),
    }),

  /* ==========================================================
     以下四组走 tryRequest：端点没上线时返回 null，由页面渲染
     「接口待上线」而不是抛错。契约见 types.ts 顶部说明。
     ========================================================== */

  // ---------- P4 每日委托 /api/delegate（7 端点）----------
  //
  // ⚠️ 路径**不带尾斜杠**（2026-10-14 实测确认）：
  //   GET /api/delegate  → 401（存在，只差登录）✅
  //   GET /api/delegate/ → 404（后端路由是 ""，不匹配）❌
  // 后端已把 5 处路由去掉尾斜杠、与项目其余 ~35 个端点统一，前端跟着统一。
  // 之前这里写的是带斜杠的版本 ⇒ 接口一上线列表就是空的（404 被 tryRequest 归一成 null
  // ⇒ 走「接口待上线」降级，看起来像后端没部署，实际是路径不对）。
  delegate: {
    list: () => tryRequest<unknown>("/api/delegate").then(unwrap<Delegate>("delegates")),
    create: (body: Record<string, unknown>) =>
      tryRequest<Delegate>("/api/delegate", { method: "POST", body: JSON.stringify(body) }),
    update: (id: string, body: Record<string, unknown>) =>
      tryRequest<Delegate>(`/api/delegate/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
    remove: (id: string) =>
      tryRequest<{ ok: boolean }>(`/api/delegate/${id}`, { method: "DELETE" }),
    runs: (id: string) =>
      tryRequest<unknown>(`/api/delegate/${id}/runs`).then(unwrap<DelegateRun>("items")),
    /**
     * dry_run=true = 只生成、不投递、不改 next_run_at。
     * 这是「先生成后确认」的落地：用户先看到内容，再决定投不投。
     * 生成内容在响应的 `content` 字段（此前这里按 text/output 取，永远是 undefined
     * ⇒ 预览弹层恒显示「后端未返回内容」）。
     */
    run: (id: string, dryRun: boolean) =>
      tryRequest<{ content?: string; delivered?: boolean }>(
        `/api/delegate/${id}/run${dryRun ? "?dry_run=true" : ""}`,
        { method: "POST" },
        { timeoutMs: 120000 }
      ),
  },

  // ---------- P5 每日总结 /api/report（3 端点）----------
  report: {
    today: () => tryRequest<DailyReport>("/api/report/today"),
    byDate: (date: string) => tryRequest<DailyReport>(`/api/report/${date}`),
    /** MVP 是同步生成（约 20–40 秒），前端要用「生成中」态兜住 */
    generate: (date?: string) =>
      tryRequest<DailyReport>("/api/report/generate", {
        method: "POST",
        body: JSON.stringify(date ? { date } : {}),
      }, { timeoutMs: 120000 }),
  },

  // ---------- P6 打卡 /api/checkin（4 端点）----------
  //
  // 打卡语义 = 「那天有没有生成每日总结（status='ready'）」：数据全从 daily_reports 派生。
  // 端点**不带尾斜杠**（与项目 ~62 个端点统一，避免 308 黑洞）。
  // streak / history 走 tryRequest：端点未部署时返回 null，页面退回本地/演示数据；
  // generate / share 是写操作，走 request —— 真失败要让用户知道。
  checkin: {
    streak: () => tryRequest<CheckinStreak>("/api/checkin/streak"),
    /** 后端返回 { history: [...] }，这里直接解开成数组 */
    history: () =>
      tryRequest<{ history: CheckinHistoryItem[] }>("/api/checkin/history").then(
        (v) => (v ? v.history : null)
      ),
    /** 触发今日总结（同步，约 20–40 秒）；返回 {date, status, points} */
    generate: () =>
      request<{ date: string; status: string; points: number }>(
        "/api/checkin/generate",
        { method: "POST" },
        { timeoutMs: 120000 }
      ),
    /** 标记某天已分享（幂等 upsert） */
    share: (date: string) =>
      request<{ ok: boolean; date: string; shared: boolean }>("/api/checkin/share", {
        method: "POST",
        body: JSON.stringify({ date }),
      }),
  },

  // ---------- P7 设置：工具元数据 /api/agent/tools（2 端点）----------
  agent: {
    tools: () => tryRequest<unknown>("/api/agent/tools").then(unwrap<AgentTool>("items")),
  },

  // ---------- P7 设置：调度 /api/schedule（5 端点，2 个管理员）----------
  schedule: {
    health: () => tryRequest<ScheduleHealth>("/api/schedule/health"),
    jobs: () => tryRequest<unknown>("/api/schedule/jobs").then(unwrap<ScheduleJob>("jobs")),
    /** 全量执行历史（跨所有委托）。委托页的「执行历史」读它 —— 逐条去问 N 个委托要 N 次请求 */
    runs: () => tryRequest<unknown>("/api/schedule/runs").then(unwrap<DelegateRun>("items")),
    /** 仅管理员；非管理员 403 —— 页面据此只读展示 */
    runNow: (jobKey: string) =>
      tryRequest<{ ok: boolean }>("/api/schedule/run-now", {
        method: "POST",
        body: JSON.stringify({ job_key: jobKey }),
      }),
    retry: (runId: string) =>
      tryRequest<{ ok: boolean }>("/api/schedule/retry", {
        method: "POST",
        body: JSON.stringify({ run_id: runId }),
      }),
  },

  // ---------- P6 混合架构 /api/hybrid（12 端点）----------
  //
  // ⚠️ 后端截至 10-14 未推到任何远端分支，这一组是**照接口文档 v1 实现**的。
  //    全部走 tryRequest ⇒ 端点不存在时返回 null，页面自动退到演示数据。
  hybrid: {
    // 工作流定义 CRUD
    workflows: () => tryRequest<unknown>("/api/hybrid/workflows").then(unwrap<WorkflowRow>("items")),
    workflow: (id: string) => tryRequest<WorkflowRow>(`/api/hybrid/workflows/${id}`),
    createWorkflow: (body: WorkflowIn) =>
      tryRequest<WorkflowRow>("/api/hybrid/workflows", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    /** ⚠️ 内置工作流（builtin=true）改删一律 404，界面要置灰而不是报错 */
    updateWorkflow: (id: string, body: WorkflowIn) =>
      tryRequest<WorkflowRow>(`/api/hybrid/workflows/${id}`, {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    deleteWorkflow: (id: string) =>
      tryRequest<{ ok: boolean }>(`/api/hybrid/workflows/${id}`, { method: "DELETE" }),

    // 切换规则（系统默认 + 用户覆盖）
    /** ⚠️ 只返回 enabled=1 的规则；停用项会从列表消失，不是「显示成已停用」 */
    rules: () => tryRequest<unknown>("/api/hybrid/rules").then(unwrap<RuleRow>("items")),
    putRule: (name: string, body: RuleIn) =>
      tryRequest<{ ok: boolean }>(`/api/hybrid/rules/${name}`, {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    /** 只能删用户覆盖；系统默认不可删（404） */
    deleteRule: (name: string) =>
      tryRequest<{ ok: boolean }>(`/api/hybrid/rules/${name}`, { method: "DELETE" }),

    // 执行记录与全链路日志
    /** limit 默认 20，上限 100 */
    runs: (limit = 20) =>
      tryRequest<unknown>(`/api/hybrid/runs?limit=${limit}`).then(unwrap<HybridRunRow>("items")),
    run: (runId: string) => tryRequest<HybridRunDetail>(`/api/hybrid/runs/${runId}`),
    /** limit 默认 500，上限 2000。刷新页面后靠它重建执行轨迹 */
    logs: (runId: string, limit = 500) =>
      tryRequest<unknown>(`/api/hybrid/runs/${runId}/logs?limit=${limit}`).then(
        unwrap<HybridLogRow>("items")
      ),
    /**
     * 人工接管。指令**不立即中断**：引擎在安全点（节点/事件边界）让出后才生效，
     * 随后会推一条 rule="manual" 的 mode_switch。
     * 409 = 任务已结束，界面据此提示「任务已结束，无需切换」而不是报错。
     */
    switchRun: (runId: string, action: "to_agent" | "to_workflow" | "terminate") =>
      tryRequest<{ ok: boolean }>(`/api/hybrid/runs/${runId}/switch`, {
        method: "POST",
        body: JSON.stringify({ action }),
      }),
  },
};

/**
 * 把列表响应统一成数组。**认三种壳层**，页面侧不用各写一遍：
 *   1. 裸数组 `[...]`
 *   2. `{ [key]: [...] }`          —— 传 primary，如 `{items:[...]}` / `{delegates:[...]}`
 *   3. `{ items: [...] }` 兜底    —— 传 "items" 时额外认 `{runs:[...]}` / `{tools:[...]}`
 *
 * ⚠️ 为什么要第 3 条：后端这三个端点（/api/schedule/runs、/api/agent/tools、
 * /api/delegate/{id}/runs）实际都返回 `{"items":[...],"next_cursor":...}`，
 * 而当时我按直觉写成了各自的资源名（runs / tools）。契约联调前这层差异不致命
 * （页面有演示数据兜底），但**后端一上线就会静默变成空列表**——
 * 「不报错、只是永远空着」是最难查的一类问题。
 * 与其赌后端用哪个 key，不如这里多认几个候选。
 */
function unwrap<T>(key: string) {
  return (v: unknown): T[] | null => {
    if (v === null || v === undefined) return null;
    if (Array.isArray(v)) return v as T[];
    const rec = v as Record<string, unknown>;
    const primary = rec[key];
    if (Array.isArray(primary)) return primary as T[];
    // 兜底：任何值为数组的字段都认（只认一层，避免把分页元信息当数据）
    for (const k of Object.keys(rec)) {
      if (k === "next_cursor" || k === "total") continue;
      const val = rec[k];
      if (Array.isArray(val)) return val as T[];
    }
    return [];
  };
}