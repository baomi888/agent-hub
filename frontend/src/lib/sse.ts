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
    /**
     * L4 一次性确认令牌：**只有**用户在确认卡片上点了「允许执行」之后才有值。
     *
     * 与 `turn_id` 必须成对出现：服务端把 token 绑在 (user, tool, 参数, turn) 上，
     * 只带 token 不带 turn（或换一个 turn）一律按"令牌无效"处理，
     * 工具会再次被拒。两个都不传 = 正常提问。
     */
    approval_token?: string;
    turn_id?: string;
  },
  signal?: AbortSignal,
  resume = false,
  resumeMessageId?: number
): AsyncGenerator<StreamEvent> {
  let cursor = 0;
  let reconnect = resume;
  let messageId = resumeMessageId;
  while (!signal?.aborted) {
    try {
      const res = await fetch(reconnect
        ? `/api/chat/stream/${encodeURIComponent(body.sid)}?after=${cursor}${messageId ? `&message_id=${messageId}` : ""}`
        : "/api/chat/stream", {
        method: reconnect ? "GET" : "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        ...(reconnect ? {} : { body: JSON.stringify(body) }),
        signal,
      });
      if (res.status === 204 || (reconnect && res.status === 409)) return;
      if (!res.ok || !res.body) {
        let detail = `HTTP ${res.status}`;
        if (res.status === 401) {
          window.dispatchEvent(new Event("baomi:unauthorized"));
        }
        try {
          const j = await res.json();
          detail = detailToMessage(j.detail, detail);
        } catch { /* no JSON body */ }
        if (res.status >= 400 && res.status < 500) {
          yield { event: "error", data: { detail } };
          return;
        }
        throw new Error(detail);
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let replaying = reconnect;

      try {
        while (!signal?.aborted) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");
          let idx: number;
          while ((idx = buffer.indexOf("\n\n")) !== -1) {
            const ev = parseFrame(buffer.slice(0, idx));
            buffer = buffer.slice(idx + 2);
            if (!ev) continue;
            if (ev.event === "meta") {
              const receivedId = Number(ev.data.message_id) || undefined;
              if (messageId && receivedId && messageId !== receivedId) return;
              messageId = receivedId ?? messageId;
            }
            if (ev.event === "replay_end") replaying = false;
            const next = Number(ev.data.cursor ?? 0);
            if (next && next <= cursor) continue;
            if (next) cursor = next;
            if (replaying) ev.data.recovered = true;
            yield ev;
            if (ev.event === "done") return;
          }
        }
      } finally {
        try { await reader.cancel(); } catch { /* closed */ }
        reader.releaseLock();
      }

    } catch (error) {
      if (signal?.aborted) throw error;
      // A failed POST may already have started a task. Never submit it again.
    }
    reconnect = true;
    await new Promise<void>((resolve) => {
      if (signal?.aborted) return resolve();
      const finish = () => {
        clearTimeout(timer);
        signal?.removeEventListener("abort", finish);
        resolve();
      };
      const timer = setTimeout(finish, 1500);
      signal?.addEventListener("abort", finish, { once: true });
    });
  }
}
