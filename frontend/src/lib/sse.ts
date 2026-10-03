// SSE 流式解析：fetch + ReadableStream（EventSource 不支持 POST 流式）
// 后端帧格式：`event: X\ndata: {json}\n\n`

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
    mode?: string;
    kb_id_override?: string;
    attachments?: { path: string; type: string; filename?: string }[];
    // 两种形态：{ lat, lon }（浏览器定位）或 { city }（公网 http 下由服务端按 IP 推断）。
    // 字段都可选 —— 只有 city 没有坐标是合法的。
    location?: { lat?: number; lon?: number; city?: string } | null;
  },
  signal?: AbortSignal
): AsyncGenerator<StreamEvent> {
  const res = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });

  if (!res.ok || !res.body) {
    let detail = `HTTP ${res.status}`;
    try {
      const j = await res.json();
      detail = j.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

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
}