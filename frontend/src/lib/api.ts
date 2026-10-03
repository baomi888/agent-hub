// 后端 API 封装：统一错误处理 + 类型化（相对路径走 rewrites 代理）
import type { Conversation, KbInfo, KbFile, FilePreview, SearchResult, Defaults, Message } from "./types";

/** 统一解析后端错误体（FastAPI 返回 { detail: string }），非 JSON 时回落状态码 */
async function httpError(res: Response): Promise<Error> {
  let detail = `HTTP ${res.status}`;
  try {
    const body = (await res.json()) as { detail?: string };
    detail = body.detail || detail;
  } catch {
    /* 忽略非 JSON 错误体 */
  }
  return new Error(detail);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    throw await httpError(res);
  }
  return res.json() as Promise<T>;
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
      { method: "POST", body: fd }
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
  // 同一条消息重复提交是"改"不是"追加"：点赞再点踩后端只留最后一次
  sendFeedback: (sid: string, messageId: number, rating: "up" | "down") =>
    request<{ ok: boolean; rating: string }>("/api/feedback/", {
      method: "POST",
      body: JSON.stringify({ sid, message_id: messageId, rating }),
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
    }).then(async (res) => {
      if (!res.ok) throw await httpError(res);
      return res.json() as Promise<{
        files: { filename: string; path: string; size: number; type: string }[];
      }>;
    });
  },
};