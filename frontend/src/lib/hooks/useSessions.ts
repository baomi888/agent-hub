"use client";

// 会话域：会话列表 CRUD、当前会话消息加载、整轮删除、首屏加载/重试
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { uid } from "@/lib/id";
import { stripPlanHint } from "@/components/ChatArea";
import type { Conversation } from "@/lib/types";
import type { UiMessage } from "@/components/chat/types";
import type { ShowToast } from "./useToast";

function toUi(role: string, content: string, refs?: string, dbId?: number): UiMessage | null {
  if (role === "system") return null;
  return {
    id: uid(),
    role: role === "user" ? "user" : "assistant",
    content: stripPlanHint(content),
    refs,
    dbId,
  };
}

function toUiList(messages: { role: string; content: string; refs?: string; id?: number }[]) {
  return messages
    .map((m) => toUi(m.role, m.content, m.refs, m.id))
    .filter(Boolean) as UiMessage[];
}

export function useSessions(showToast: ShowToast) {
  const [sessions, setSessions] = useState<Conversation[]>([]);
  const [activeSid, setActiveSid] = useState<string | null>(null);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  // 首屏会话列表还没回来：侧栏渲染骨架，避免先闪一下"还没有会话"
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const refreshSessions = useCallback(async () => {
    try {
      const { sessions: list } = await api.listSessions();
      setSessions(list);
    } catch (e) {
      console.error(e);
      // 同文件里 rename / delete 失败都会 toast，只有这里静默：
      // 改完名刷新失败，侧栏还显示旧名，用户会以为改名没生效。
      showToast("error", "会话列表刷新失败，请稍后重试");
    }
  }, [showToast]);

  // 初始化：拉会话列表（与知识库/defaults 的请求由各自 hook 并发发起）
  useEffect(() => {
    let alive = true;
    (async () => {
      const s = await api.listSessions().catch(() => null);
      if (!alive) return;
      if (s) setSessions(s.sessions);
      setLoadError(s ? null : "会话列表加载失败");
      setLoading(false);
    })();
    return () => {
      alive = false;
    };
  }, []);

  // 新建会话
  const createSession = async () => {
    try {
      const conv = await api.createSession();
      setSessions((prev) => [conv, ...prev]);
      setActiveSid(conv.id);
      setMessages([]);
    } catch (e) {
      showToast("error", "新建会话失败，请稍后重试");
    }
  };

  // 选择会话并加载历史
  const selectSession = async (sid: string) => {
    setActiveSid(sid);
    setMessages([]);
    try {
      const detail = await api.getSession(sid);
      setMessages(toUiList(detail.messages));
    } catch (e) {
      console.error(e);
      // 历史加载失败时主区是空的，不说一声用户只会以为这个会话本来就没聊过
      showToast("error", "会话历史加载失败，请稍后重试");
    }
  };

  const renameSession = async (sid: string, title: string) => {
    try {
      await api.renameSession(sid, title);
      setSessions((prev) => prev.map((c) => (c.id === sid ? { ...c, title } : c)));
    } catch (e) {
      showToast("error", "重命名失败，请稍后重试");
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
      showToast("error", "删除失败，请稍后重试");
    }
  };

  /** 删除某条消息及其所在的一轮对话（user + 后续连续 assistant） */
  const deleteTurn = async (msgDbId: number) => {
    if (!activeSid || !msgDbId) return;
    try {
      const r = await api.deleteMessageTurn(activeSid, msgDbId);
      if (r.ok) {
        // 重新拉取最新消息列表，保证前端与后端一致
        const detail = await api.getSession(activeSid);
        setMessages(toUiList(detail.messages));
      }
    } catch (e) {
      showToast("error", "删除失败，请稍后重试");
    }
  };

  // 首屏加载失败后手动重试：清掉错误态并重新拉一次
  const reloadSessions = useCallback(async () => {
    setLoadError(null);
    try {
      const { sessions: list } = await api.listSessions();
      setSessions(list);
    } catch {
      setLoadError("会话列表加载失败");
    }
  }, []);

  return {
    sessions,
    setSessions,
    activeSid,
    setActiveSid,
    messages,
    setMessages,
    loading,
    loadError,
    refreshSessions,
    createSession,
    selectSession,
    renameSession,
    deleteSession,
    deleteTurn,
    reloadSessions,
  };
}
