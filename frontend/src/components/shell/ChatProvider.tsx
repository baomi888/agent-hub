"use client";

// 会话与消息域，提升到**应用外壳**（2026-11-22）
//
// 为什么不在页面里了：
//   · 侧栏要在路由之间保持挂载（历史列表滚动位置、搜索词、展开状态都得留住），
//     而它要显示会话列表、执行新建/改名/删除 —— 这些能力必须比页面活得久；
//   · 生成中的回答也不能因为"你点去论坛"就断掉：流的发起方一旦是页面组件，
//     页面一卸载就没人接收了。现在接收方是这一层，页面只是渲染。
//
// ★ 为什么拆两个 Context 而不是一个：
//   "保持挂载" ≠ "不重渲染"。消息每来一个 token 就会 setState，
//   如果侧栏和消息共用一个 value，它会跟着每秒重算几十次（DOM 不变，CPU 白烧）。
//   这里把**侧栏需要的切片**单独 memo 一份，侧栏只订阅它 —— 流式期间它一次都不重渲染。
import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { useSessions } from "@/lib/hooks/useSessions";
import { useToast } from "@/lib/hooks/useToast";
import { ToastHost } from "@/components/console/ui";

type SessionsStore = ReturnType<typeof useSessions>;

/**
 * 会话域 + 侧栏需要的一点派生数据。
 *
 * `kbNames`：会话卡片上那行「资料库 · XXX」需要 id→名字。
 * 数据源仍归 useKnowledge（页面里那份），这里只做**上浮**：
 * 页面把它推进来，侧栏在别的路由也能显示对的名字，且列表更新时一起更新。
 */
type Store = SessionsStore & {
  kbNames: Record<string, string>;
  setKbNames: (next: Record<string, string>) => void;
};

/** 侧栏需要的那一片：列表 + 当前选中 + 增删改。**不含 messages** */
export interface RailStore {
  sessions: SessionsStore["sessions"];
  activeSid: SessionsStore["activeSid"];
  loading: SessionsStore["loading"];
  loadError: SessionsStore["loadError"];
  selectSession: SessionsStore["selectSession"];
  createSession: SessionsStore["createSession"];
  renameSession: SessionsStore["renameSession"];
  deleteSession: SessionsStore["deleteSession"];
  reloadSessions: SessionsStore["reloadSessions"];
  kbNames: Record<string, string>;
}

const FullCtx = createContext<Store | null>(null);
const RailCtx = createContext<RailStore | null>(null);

/** 聊天页用：消息、流式、全部 CRUD */
export function useChatStore(): Store {
  const v = useContext(FullCtx);
  if (!v) throw new Error("useChatStore 只能在 ChatProvider 内使用");
  return v;
}

/** 侧栏用：只有列表那一片，流式期间不会跟着重渲染 */
export function useRailStore(): RailStore {
  const v = useContext(RailCtx);
  if (!v) throw new Error("useRailStore 只能在 ChatProvider 内使用");
  return v;
}

export default function ChatProvider({ children }: { children: ReactNode }) {
  // toast 挂在最外层：改名/删除失败发生在侧栏，而侧栏在别的页面也要能报错
  const { toasts, showToast } = useToast();
  const sessionsStore = useSessions(showToast);
  const [kbNames, setKbNames] = useState<Record<string, string>>({});

  const store = useMemo<Store>(
    () => ({ ...sessionsStore, kbNames, setKbNames }),
    [sessionsStore, kbNames],
  );

  const rail = useMemo<RailStore>(
    () => ({
      sessions: sessionsStore.sessions,
      activeSid: sessionsStore.activeSid,
      loading: sessionsStore.loading,
      loadError: sessionsStore.loadError,
      selectSession: sessionsStore.selectSession,
      createSession: sessionsStore.createSession,
      renameSession: sessionsStore.renameSession,
      deleteSession: sessionsStore.deleteSession,
      reloadSessions: sessionsStore.reloadSessions,
      kbNames,
    }),
    // 刻意不依赖 messages：它每个 token 都变，放进来等于白拆
    [
      sessionsStore.sessions,
      sessionsStore.activeSid,
      sessionsStore.loading,
      sessionsStore.loadError,
      sessionsStore.selectSession,
      sessionsStore.createSession,
      sessionsStore.renameSession,
      sessionsStore.deleteSession,
      sessionsStore.reloadSessions,
      kbNames,
    ],
  );

  return (
    <FullCtx.Provider value={store}>
      <RailCtx.Provider value={rail}>
        {children}
        <ToastHost toasts={toasts} />
      </RailCtx.Provider>
    </FullCtx.Provider>
  );
}
