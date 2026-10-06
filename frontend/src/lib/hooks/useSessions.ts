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
      // 自动落进最近一个会话。少了这一步 activeSid 会一直是 null，
      // 而「绑定资料库」这类按会话生效的操作会静默失败——点了没反应，
      // 用户只会以为那个勾选框是坏的（10-04 实测：不点会话直接勾，一个请求都不发）。
      const recent = s?.sessions?.[0];
      if (!recent) return;
      setActiveSid(recent.id);
      try {
        const detail = await api.getSession(recent.id);
        if (alive) setMessages(toUiList(detail.messages));
      } catch (e) {
        console.error(e);
        if (alive) showToast("error", "会话历史加载失败，请稍后重试");
      }
    })();
    return () => {
      alive = false;
    };
  }, [showToast]);

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

  // 列表有内容但还没选中任何会话时，自动落进最近一个。
  // 覆盖的是 init effect 管不到的场景：登录门后面请求被 401 挡掉，
  // 登录成功后补拉的列表如果不选一个，activeSid 就一直是 null，
  // 「绑定知识库」这类按会话生效的操作又会变成"点了没反应"（10-04 的老坑）。
  useEffect(() => {
    if (activeSid || !sessions.length) return;
    // 推迟到下一个宏任务：effect 体里同步 setState 会被判成级联渲染
    const t = window.setTimeout(() => void selectSession(sessions[0].id), 0);
    return () => window.clearTimeout(t);
    // selectSession 每次渲染都是新引用，进 deps 会让这个 effect 反复触发
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessions, activeSid]);

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
