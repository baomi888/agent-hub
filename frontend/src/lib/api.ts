// 后端 API 封装：统一错误处理 + 类型化（相对路径走 rewrites 代理）
import type { Conversation, KbInfo, KbFile, FilePreview, SearchResult, Defaults, Message } from "./types";
import type { MeOut, UserOut, Template, ApplyTemplateResult, UsageSummary } from "./types";
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

/** 统一解析后端错误体（FastAPI 返回 { detail: string }），非 JSON 时回落状态码 */
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
    const body = (await res.json()) as { detail?: string; retry_after?: number };
    detail = body.detail || detail;
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // Next dev 对带尾斜杠的 /api/* 会 308 重定向，浏览器 fetch 跟随时偶尔丢 body/cookie，
  // 直接把尾斜杠去掉，请求落到 rewrite 目标，彻底绕开重定向。
  const cleanPath = path.length > 1 && path.endsWith("/") ? path.slice(0, -1) : path;
  const res = await fetch(cleanPath, {
    // 登录态在 HttpOnly Cookie 里，同源也要显式带；不写这行浏览器会丢弃
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    throw await httpError(res);
  }
  return res.json() as Promise<T>;
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
async function tryRequest<T>(path: string, init?: RequestInit): Promise<T | null> {
  const cleanPath = path.length > 1 && path.endsWith("/") ? path.slice(0, -1) : path;
  const res = await fetch(cleanPath, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (res.ok) return res.json() as Promise<T>;
  if (res.status === 405 || res.status === 501) return null;
  if (res.status === 404) {
    let detail = "";
    try {
      detail = ((await res.json()) as { detail?: string }).detail ?? "";
    } catch {
      /* 非 JSON 错误体：当作未上线处理 */
    }
    // 空 detail：后端没给 JSON 错误体，多数也是路由未匹配
    // 其余按大小写不敏感匹配，理由见上方注释「按字符串猜」那一段
    if (detail === "" || /^not found$/i.test(detail.trim())) return null;
    // 业务 404：让调用方自己去认（report 页会转成「今天还没有总结」）
    throw new BusinessNotFoundError(detail);
  }
  throw await httpError(res);
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
  login: (username: string, password: string) =>
    request<UserOut>("/api/auth/login/", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  register: (username: string, password: string) =>
    request<UserOut>("/api/auth/register/", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request<{ ok: boolean }>("/api/auth/logout/", { method: "POST" }),
  me: () => request<MeOut>("/api/auth/me/"),

  // ---------- 用量与反馈看板 ----------
  usageSummary: () => request<UsageSummary>("/api/usage/summary"),

  // ---------- 模板广场 ----------
  templates: () =>
    request<{ templates: Template[] }>("/api/templates/"),
  applyTemplate: (id: string) =>
    request<ApplyTemplateResult>(`/api/templates/${id}/apply`, {
      method: "POST",
    }),

  /* ==========================================================
     以下四组走 tryRequest：端点没上线时返回 null，由页面渲染
     「接口待上线」而不是抛错。契约见 types.ts 顶部说明。
     ========================================================== */

  // ---------- P4 每日委托 /api/delegate（7 端点）----------
  delegate: {
    list: () => tryRequest<unknown>("/api/delegate/").then(unwrap<Delegate>("delegates")),
    create: (body: Record<string, unknown>) =>
      tryRequest<Delegate>("/api/delegate/", { method: "POST", body: JSON.stringify(body) }),
    update: (id: string, body: Record<string, unknown>) =>
      tryRequest<Delegate>(`/api/delegate/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
    remove: (id: string) =>
      tryRequest<{ ok: boolean }>(`/api/delegate/${id}`, { method: "DELETE" }),
    runs: (id: string) =>
      tryRequest<unknown>(`/api/delegate/${id}/runs`).then(unwrap<DelegateRun>("items")),
    /**
     * dry_run=true = 只生成、不投递、不改 next_run_at。
     * 这是「先生成后确认」的落地：用户先看到内容，再决定投不投。
     */
    run: (id: string, dryRun: boolean) =>
      tryRequest<{ text?: string; output?: string; delivered?: boolean }>(
        `/api/delegate/${id}/run${dryRun ? "?dry_run=true" : ""}`,
        { method: "POST" }
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