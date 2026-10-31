// SSE 流式解析：fetch + ReadableStream（EventSource 不支持 POST 流式）
// 后端帧格式：`event: X\ndata: {json}\n\n`

import { detailToMessage } from "./errors";

export interface StreamEvent {
  event: string;
  data: Record<string, unknown>;
}

function parseFrame(frame: string): StreamEvent | null {
  let event = "message";
  let dataStr = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataStr += line.slice(5).trim();
  }
  if (!dataStr) return null;
  try {
    return { event, data: JSON.parse(dataStr) };
  } catch {
    return { event, data: { text: dataStr } };
  }
}

export async function* streamChat(
  body: {
    sid: string;
    question: string;
    top_k?: number;
    /**
     * 能力开关（2026-10-30 后端新契约）：
     * - `use_kb`  本轮是否使用知识库（有库时后端先跑一轮检索，模型可再补）
     * - `use_web` 本轮是否允许模型自主联网
     *
     * ⚠️ **两个必须一起发**。后端 `_resolve_switches` 的判定是
     * `if req.use_kb is not None or req.use_web is not None: return bool(use_kb), bool(use_web)`
     * —— 只传一个时，另一个 `None` 会被 `bool()` 压成 `False`，静默改变语义。
     * 两个都是 `undefined`（字段不出现）才回落到下面的 `mode`。
     */
    use_kb?: boolean;
    use_web?: boolean;
    /** 兼容字段：后端仅在 use_kb/use_web 都没传时使用。前端继续带着，作为兜底。 */
    mode?: string;
    kb_id_override?: string;
    /**
     * 混合架构（mode=hybrid）专用：指定工作流起跑。
     * 缺省（null/undefined）时由后端按问题粗分决定走工作流还是裸跑 Agent。
     * ⚠️ 传了不存在的 id 后端会直接回 error 事件，所以发之前要校验它还在列表里。
     */
    workflow_id?: string | null;
    attachments?: { path: string; type: string; filename?: string }[];
    // 两种形态：{ lat, lon }（浏览器定位）或 { city }（公网 http 下由服务端按 IP 推断）。
    // 字段都可选 —— 只有 city 没有坐标是合法的。
    location?: { lat?: number; lon?: number; city?: string } | null;
    /**
     * 计划类请求。true 时后端把「结构化计划格式要求」拼到本轮提问上（服务端注入）。
     * 前端不再改写 question 文本——改写会让当前轮丢上下文、并污染历史。
     */
    plan?: boolean;
  },
  signal?: AbortSignal
): AsyncGenerator<StreamEvent> {
  const res = await fetch("/api/chat/stream", {
    method: "POST",
    // 流式请求同样要带 Cookie，否则第一帧就被后端的鉴权拦下
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });

  if (!res.ok || !res.body) {
    let detail = `HTTP ${res.status}`;
    if (res.status === 401) {
      window.dispatchEvent(new Event("baomi:unauthorized"));
      throw new Error("未登录或登录已过期，请重新登录");
    }
    try {
      const j = await res.json();
      // 同 api.ts：detail 可能是字符串，也可能是 FastAPI 422 的数组。
      // 直接 `new Error(j.detail)` 会把它变成 "[object Object]"。
      detail = detailToMessage(j.detail, detail);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let idx: number;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const ev = parseFrame(frame);
        if (ev) yield ev;
      }
    }
  } finally {
    // 中途 abort / 抛错都要归还 reader：以前漏了这一步，取消回答会泄漏连接
    try {
      await reader.cancel();
    } catch {
      /* 已关闭时忽略 */
    }
    reader.releaseLock();
  }
}