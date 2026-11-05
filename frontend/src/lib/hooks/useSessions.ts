"use client";

// 会话域：会话列表 CRUD、当前会话消息加载、整轮删除、首屏加载/重试
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { uid } from "@/lib/id";
import { stripPlanHint } from "@/components/ChatArea";
import type { Conversation, WebSource } from "@/lib/types";
import type { UiMessage } from "@/components/chat/types";
import type { ShowToast } from "./useToast";

function toUi(
  role: string,
  content: string,
  refs?: string,
  dbId?: number,
  webSources?: WebSource[]
): UiMessage | null {
  if (role === "system") return null;
  return {
    id: uid(),
    role: role === "user" ? "user" : "assistant",
    content: stripPlanHint(content),
    refs,
    dbId,
    webSources: webSources?.length ? webSources : undefined,
  };
}

function toUiList(
  messages: {
    role: string;
    content: string;
    refs?: string;
    id?: number;
    web_sources?: WebSource[];
  }[]
) {
  return messages
    .map((m) => toUi(m.role, m.content, m.refs, m.id, m.web_sources))
    .filter(Boolean) as UiMessage[];
}

/** 把「还在等我决定的建库建议」挂回它对应的那条回答上。
 *
 * 为什么必须做：建议卡不是历史内容，但**那条待确认的记录是真实存在的**
 * （后端 kb_proposals 里一条 pending，带 72 小时有效期）。刷新之后如果只在内存里
 * 丢掉卡片，用户会遇到两件说不通的事：① 刚才那张"要不要建库"的卡凭空消失了；
 * ② 10 分钟内再问同一个会话，因为冷却判定还能看到那条 pending，**不会再出新的建议**
 * —— 功能看起来就是坏的。
 *
 * 挂错人比不挂更糟，所以严格按 `answer_message_id` 对号；那条回答要是被删了
 * （用户删掉了整轮对话）就对不上，宁可不显示。
 */
async function attachProposal(sid: string, list: UiMessage[]): Promise<UiMessage[]> {
  try {
    const res = await api.getPendingKbProposal(sid);
    const p = res?.proposal;
    if (!p || p.status !== "pending") return list;
    const mid = Number(p.answer_message_id || 0);
    if (!mid) return list;
    const idx = list.findIndex((m) => m.dbId === mid);
    if (idx < 0) return list;
    const next = list.slice();
    next[idx] = { ...next[idx], proposal: p };
    return next;
  } catch {
    // 恢复失败绝不能连累看历史消息：这是锦上添花的一条请求
    return list;
  }
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
        if (!alive) return;
        const base = toUiList(detail.messages);
        setMessages(base);
        // 建议卡要等消息落地之后才知道挂哪条，所以先显示消息、再补挂
        void attachProposal(recent.id, base).then((next) => {
          if (!alive || next === base) return;
          setMessages((prev) => (prev === base ? next : prev));
        });
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
      const base = toUiList(detail.messages);
      setMessages(base);
      // 切回一个还有待确认建议的会话时，卡片要跟着回来（见 attachProposal 的注释）。
      // 用 `prev === base` 做守卫：这期间用户可能已经发了新消息，
      // 那就不能再拿旧的 base 覆盖回去。
      void attachProposal(sid, base).then((next) => {
        if (next === base) return;
        setMessages((prev) => (prev === base ? next : prev));
      });
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
