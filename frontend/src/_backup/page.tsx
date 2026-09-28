"use client";

// 主页面：三栏布局（会话栏 + 对话区 + 知识库面板）与全局状态
import { useCallback, useEffect, useRef, useState } from "react";
import Sidebar from "@/components/Sidebar";
import ChatArea, { type UiMessage } from "@/components/ChatArea";
import KnowledgePanel from "@/components/KnowledgePanel";
import { api } from "@/lib/api";
import { streamChat } from "@/lib/sse";
import type { ChatMode, Conversation, KbInfo, SearchResult, ToolEvent } from "@/lib/types";

const uid = () =>
  typeof crypto !== "undefined" && crypto.randomUUID
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2);

function toUi(role: string, content: string, refs?: string): UiMessage | null {
  if (role === "system") return null;
  return {
    id: uid(),
    role: role === "user" ? "user" : "assistant",
    content,
    refs,
  };
}

export default function Home() {
  const [sessions, setSessions] = useState<Conversation[]>([]);
  const [activeSid, setActiveSid] = useState<string | null>(null);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [kbs, setKbs] = useState<KbInfo[]>([]);
  const [mode, setMode] = useState<ChatMode>("auto");
  const [sending, setSending] = useState(false);
  const [topK, setTopK] = useState(3);
  const [chunkSize, setChunkSize] = useState(500);
  const [chunkOverlap, setChunkOverlap] = useState(50);
  const abortRef = useRef<AbortController | null>(null);

  const boundKb = sessions.find((s) => s.id === activeSid)?.kb_id ?? null;

  const refreshSessions = useCallback(async () => {
    try {
      const { sessions: list } = await api.listSessions();
      setSessions(list);
    } catch (e) {
      console.error(e);
    }
  }, []);

  const refreshKbs = useCallback(async () => {
    try {
      const { kbs: list } = await api.listKbs();
      setKbs(list);
    } catch (e) {
      console.error(e);
    }
  }, []);

  // 初始化
  useEffect(() => {
    refreshSessions();
    refreshKbs();
    api
      .defaults()
      .then((d) => {
        setTopK(d.top_k);
        setChunkSize(d.chunk_size);
        setChunkOverlap(d.chunk_overlap);
      })
      .catch(() => {});
  }, [refreshSessions, refreshKbs]);

  // 新建会话
  const createSession = async () => {
    try {
      const conv = await api.createSession();
      setSessions((prev) => [conv, ...prev]);
      setActiveSid(conv.id);
      setMessages([]);
    } catch (e) {
      alert(`新建会话失败：${e instanceof Error ? e.message : e}`);
    }
  };

  // 选择会话并加载历史
  const selectSession = async (sid: string) => {
    setActiveSid(sid);
    setMessages([]);
    try {
      const detail = await api.getSession(sid);
      setMessages(detail.messages.map((m) => toUi(m.role, m.content, m.refs)).filter(Boolean) as UiMessage[]);
    } catch (e) {
      console.error(e);
    }
  };

  const renameSession = async (sid: string, title: string) => {
    try {
      await api.renameSession(sid, title);
      setSessions((prev) => prev.map((c) => (c.id === sid ? { ...c, title } : c)));
    } catch (e) {
      alert(`重命名失败：${e instanceof Error ? e.message : e}`);
    }
  };

  const deleteSession = async (sid: string) => {
    try {
      await api.deleteSession(sid);
      setSessions((prev) => prev.filter((c) => c.id !== sid));
      if (activeSid === sid) {
        setActiveSid(null);
        setMessages([]);
      }
    } catch (e) {
      alert(`删除失败：${e instanceof Error ? e.message : e}`);
    }
  };

  // 绑定/解绑知识库
  const bindKb = async (kbId: string | null) => {
    if (!activeSid) return;
    try {
      const conv = await api.bindKb(activeSid, kbId);
      setSessions((prev) => prev.map((c) => (c.id === activeSid ? conv : c)));
    } catch (e) {
      alert(`绑定失败：${e instanceof Error ? e.message : e}`);
    }
  };

  // 发送消息（SSE 流式）
  const sendMessage = async (question: string) => {
    let sid = activeSid;
    // 无会话时自动新建
    if (!sid) {
      try {
        const conv = await api.createSession();
        sid = conv.id;
        setSessions((prev) => [conv, ...prev]);
        setActiveSid(conv.id);
      } catch (e) {
        alert(`新建会话失败：${e instanceof Error ? e.message : e}`);
        return;
      }
    }

    setMessages((prev) => [...prev, { id: uid(), role: "user", content: question }]);
    const asstId = uid();
    setMessages((prev) => [
      ...prev,
      { id: asstId, role: "assistant", content: "", streaming: true, toolCalls: [] },
    ]);
    const controller = new AbortController();
    abortRef.current = controller;
    setSending(true);

    try {
      const gen = streamChat({ sid, question, top_k: topK, mode }, controller.signal);
      let finalRefs = "";
      for await (const ev of gen) {
        switch (ev.event) {
          case "token": {
            const text = String(ev.data.text ?? "");
            finalRefs = String(ev.data.refs ?? "") || finalRefs;
            setMessages((prev) =>
              prev.map((m) =>
                m.id === asstId ? { ...m, content: text, refs: finalRefs || m.refs } : m
              )
            );
            break;
          }
          case "tool": {
            const t = ev.data as unknown as ToolEvent;
            setMessages((prev) =>
              prev.map((m) =>
                m.id === asstId
                  ? { ...m, toolCalls: [...(m.toolCalls ?? []), t] }
                  : m
              )
            );
            break;
          }
          case "title": {
            const title = String(ev.data.title ?? "");
            if (title) {
              setSessions((prev) =>
                prev.map((c) => (c.id === sid ? { ...c, title } : c))
              );
            }
            break;
          }
          case "done": {
            const refs = String(ev.data.refs ?? "");
            setMessages((prev) =>
              prev.map((m) =>
                m.id === asstId ? { ...m, streaming: false, refs: refs || m.refs } : m
              )
            );
            break;
          }
          case "error": {
            const detail = String(ev.data.detail ?? "未知错误");
            setMessages((prev) =>
              prev.map((m) =>
                m.id === asstId ? { ...m, error: detail, streaming: false } : m
              )
            );
            break;
          }
        }
      }
      // 兜底：结束流式状态
      setMessages((prev) =>
        prev.map((m) => (m.id === asstId ? { ...m, streaming: false } : m))
      );
    } catch (e) {
      // 用户主动停止（AbortError）不视为错误
      if ((e as Error)?.name !== "AbortError") {
        const detail = e instanceof Error ? e.message : String(e);
        setMessages((prev) =>
          prev.map((m) =>
            m.id === asstId ? { ...m, error: detail, streaming: false } : m
          )
        );
      } else {
        setMessages((prev) =>
          prev.map((m) => (m.id === asstId ? { ...m, streaming: false } : m))
        );
      }
    } finally {
      abortRef.current = null;
      setSending(false);
      refreshSessions();
    }
  };

  // 停止当前流式生成（中断 SSE 请求）
  const stopSending = () => {
    abortRef.current?.abort();
  };

  // 上传文档建库
  const uploadFiles = async (kbId: string, files: File[]) => {
    try {
      const r = await api.uploadFiles(kbId, files);
      alert(`已导入 ${r.chunks} 个片段到「${kbId}」`);
      refreshKbs();
    } catch (e) {
      alert(`上传失败：${e instanceof Error ? e.message : e}`);
    }
  };

  const searchPreview = async (keyword: string): Promise<SearchResult[]> => {
    const r = await api.searchPreview(keyword);
    return r.results;
  };

  const importUrls = async (kbId: string, urls: string[]) => {
    try {
      const r = await api.importUrls(kbId, urls);
      alert(`导入完成：成功 ${r.ok}，失败 ${r.failed}`);
      refreshKbs();
    } catch (e) {
      alert(`导入失败：${e instanceof Error ? e.message : e}`);
    }
  };

  const deleteKb = async (kbId: string) => {
    try {
      await api.deleteKb(kbId);
      refreshKbs();
    } catch (e) {
      alert(`删除失败：${e instanceof Error ? e.message : e}`);
    }
  };

  return (
    <main className="flex h-screen overflow-hidden">
      <Sidebar
        sessions={sessions}
        activeSid={activeSid}
        onSelect={selectSession}
        onCreate={createSession}
        onRename={renameSession}
        onDelete={deleteSession}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        <ChatArea
          messages={messages}
          mode={mode}
          sending={sending}
          onModeChange={setMode}
          onSend={sendMessage}
          onStop={stopSending}
          topK={topK}
          chunkSize={chunkSize}
          chunkOverlap={chunkOverlap}
          onParamChange={({ topK: t, chunkSize: c, chunkOverlap: o }) => {
            if (t !== undefined) setTopK(t);
            if (c !== undefined) setChunkSize(c);
            if (o !== undefined) setChunkOverlap(o);
          }}
        />
      </div>

      <KnowledgePanel
        kbs={kbs}
        boundKb={boundKb}
        onUpload={uploadFiles}
        onSearch={searchPreview}
        onImport={importUrls}
        onDeleteKb={deleteKb}
        onBind={bindKb}
        chunkSize={chunkSize}
        chunkOverlap={chunkOverlap}
      />
    </main>
  );
}