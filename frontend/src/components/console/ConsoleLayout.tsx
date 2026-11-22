"use client";

// 应用外壳：**全局唯一的侧栏** + 登录门 + 滚动容器。
//
// 为什么从 ConsoleShell 拆出来：原先 8 个页面各自渲染一份 ConsoleShell，
// 于是每次点左侧导航都是「整块卸载 → 整块重新挂载」：
//   · 侧栏的选中态、品牌区、图标全部重建，视觉上闪一下；
//   · 新页面自己那份 useAuth 又从 loading 开始，页面先白一帧再出内容；
//   · 滚动位置丢掉，且每次都要重新算侧栏布局。
// 现在外壳进 app/(console)/layout.tsx，Next 在同一个 layout 内切换 page 时**只换 children**，
// 侧栏是同一个 DOM 实例，从根上不会闪。页面内容由 (console)/template.tsx 做一次淡入。
//
// ★ 2026-11-22（方案 B）：侧栏从对话页搬到这里，成为**所有页面共用**的那一份。
//   以前对话页用的是 components/Sidebar，其它页用的是 .console-nav —— 两条左栏、
//   两套入口，换页像换了个应用。现在只剩一份：
//     · 会话列表与消息状态在 ChatProvider（layout 层），比页面活得久；
//     · 生成中的回答不会因为"点去论坛"而断——接收方在外壳，不在页面；
//     · 历史列表的滚动位置、搜索词、选中态天然保留（组件没卸载）。
import { usePathname } from "next/navigation";
import { useRouter } from "next/navigation";
import {
  createContext,
  memo,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
  type RefObject,
} from "react";
import AuthGate from "../AuthGate";
import { AuthLoading } from "../boot/BootSplash";
import Sidebar from "../Sidebar";
import UsagePanel from "../UsagePanel";
import ConsoleTabBar from "./ConsoleTabBar";
import GuideModal from "../guide/GuideModal";
import AnnounceModal from "../announce/AnnounceModal";
import { useAuth } from "@/lib/hooks/useAuth";
import { useRailStore } from "@/components/shell/ChatProvider";
import type { MeOut } from "@/lib/types";

/**
 * 登录信息往下传一层。
 *
 * 为什么需要：`useAuth` 是**每个实例各查一次** /api/auth/me。
 * 子页面如果自己再调一次 useAuth，它拿到的是一份独立状态 —— 用户在
 * 登录门里登录时，子页面那份不会跟着变（还是 null），账号名就会空着。
 *
 * 为什么值里要带 refresh：设置页改完昵称/头像后，只更新自己那一份 state 的话，
 * 侧栏（另一个消费者）还显示旧名字，看起来像"没改成"。带上 refresh 让改动
 * 从源头重新取值，所有消费者自然一起变。
 */
export interface ConsoleAccount {
  me: MeOut | null;
  /** 重新拉一次 /api/auth/me/ 并把结果写回登录态 */
  refresh: () => Promise<void>;
}

// 默认值给空实现而不是 null：Context 是对象，消费者解构时不会因为 null 崩掉。
const UserCtx = createContext<ConsoleAccount>({ me: null, refresh: async () => {} });

export function useConsoleUser(): MeOut | null {
  return useContext(UserCtx).me;
}

export function useConsoleAccount(): ConsoleAccount {
  return useContext(UserCtx);
}

/**
 * 外壳的 UI 状态：手机抽屉的开合。
 *
 * 为什么也要往下传：对话页要渲染遮罩、要处理 Esc、输入区上方那个汉堡键要能
 * 打开抽屉 —— 这三件事都发生在页面里，但抽屉的持有者是外壳。
 */
export interface ShellUi {
  /** 窄屏抽屉是否展开（宽屏下恒 false：那一栏本来就常驻） */
  sidebarOpen: boolean;
  openSidebar: () => void;
  closeSidebar: () => void;
  /** 抽屉容器：打开时接管焦点 */
  sidebarRef: RefObject<HTMLElement | null>;
  /** 当前是不是对话页（满屏工作台） */
  isChat: boolean;
  /** 是否窄屏（≤899）：决定要不要用抽屉形态 */
  narrow: boolean;
}

const ShellCtx = createContext<ShellUi>({
  sidebarOpen: false,
  openSidebar: () => {},
  closeSidebar: () => {},
  sidebarRef: { current: null },
  isChat: false,
  narrow: false,
});

export function useShell(): ShellUi {
  return useContext(ShellCtx);
}

/** 与 page.tsx 同一套断点：≤899 是窄屏（抽屉 + 底部 tab） */
function useNarrow(): boolean {
  const subscribe = useCallback((cb: () => void) => {
    const mq = window.matchMedia("(max-width: 899px)");
    mq.addEventListener("change", cb);
    return () => mq.removeEventListener("change", cb);
  }, []);
  const get = useCallback(() => window.matchMedia("(max-width: 899px)").matches, []);
  return useSyncExternalStore(subscribe, get, () => false);
}

/**
 * 侧栏插槽。
 *
 * ★ 为什么单独一个 memo 组件：流式回答每来一个 token 都会让 ChatProvider 重渲染，
 *   而 Provider 的 children（本组件）如果直接写在里面会被一起重算。
 *   memo + 只从 useRailStore 取数据 = **流式期间这里一次都不重渲染**。
 *   （"保持挂载"只保证不被销毁，不保证不重算 —— 这两件事必须分清。）
 */
const Rail = memo(function Rail({
  overlay,
  open,
  containerRef,
  username,
  displayName,
  avatar,
  isAdmin,
  onOpenUsage,
  onLogout,
  onCloseRail,
}: {
  overlay: boolean;
  open: boolean;
  containerRef: RefObject<HTMLElement | null>;
  username?: string;
  displayName?: string;
  avatar?: string;
  isAdmin?: boolean;
  onOpenUsage: () => void;
  onLogout: () => void;
  /** 窄屏抽屉里点完会话/新建后收起（宽屏下是空操作） */
  onCloseRail: () => void;
}) {
  const rail = useRailStore();
  const router = useRouter();

  const onCreate = useCallback(() => {
    void rail.createSession();
    onCloseRail();
  }, [rail, onCloseRail]);
  const onOpenLibrary = useCallback(() => {
    router.push("/library");
  }, [router]);
  const onSelect = useCallback(
    (sid: string) => {
      rail.selectSession(sid);
      // 抽屉是"选会话"的地方，选完该让位（宽屏下 onCloseRail 是空操作）
      onCloseRail();
    },
    [rail, onCloseRail],
  );

  return (
    <Sidebar
      sessions={rail.sessions}
      activeSid={rail.activeSid}
      onSelect={onSelect}
      onCreate={onCreate}
      onOpenLibrary={onOpenLibrary}
      onRename={rail.renameSession}
      onDelete={rail.deleteSession}
      kbNameMap={rail.kbNames}
      loading={rail.loading}
      loadError={rail.loadError}
      onRetryLoad={rail.reloadSessions}
      username={username}
      displayName={displayName}
      avatar={avatar}
      isAdmin={isAdmin}
      onOpenUsage={onOpenUsage}
      onLogout={onLogout}
      overlay={overlay}
      open={open}
      containerRef={containerRef}
    />
  );
});

export default function ConsoleLayout({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const pathname = usePathname();
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const sidebarRef = useRef<HTMLElement | null>(null);
  /* 「更多」面板开着时锁住底层滚动。开合状态归 ConsoleTabBar 自己管 ——
     外壳只关心"要不要锁滚动"，所以它只收一个布尔值。 */
  const [tabOpen, setTabOpen] = useState(false);
  const [sidebarPref, setSidebarPref] = useState(false);
  const [usageOpen, setUsageOpen] = useState(false);

  const narrow = useNarrow();
  const sidebarOpen = narrow && sidebarPref;

  // 必须 useMemo：Provider 的 value 每次渲染新建对象的话，所有消费者都会
  // 跟着重渲染，而这个外壳的子组件是整棵页面树。
  const account = useMemo<ConsoleAccount>(
    () => ({ me: auth.me, refresh: auth.refresh }),
    [auth.me, auth.refresh],
  );

  // 切页回到顶部。滚动发生在这个 div 上（html/body 被 base.css 锁了），
  // 所以 Next 自带的 scroll-to-top 管不到 —— 不做这一步，从长页面跳到短页面
  // 会停在半空，看起来像"页面没加载完"。
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0, left: 0, behavior: "auto" });
  }, [pathname]);

  const closeSidebar = useCallback(() => setSidebarPref(false), []);
  const openSidebar = useCallback(() => setSidebarPref(true), []);
  const openUsage = useCallback(() => setUsageOpen(true), []);
  const onLogout = useCallback(() => {
    void auth.logout();
  }, [auth]);

  // 规范化的当前路径：去掉尾斜杠，查询串不参与比较
  const here = pathname.replace(/\/+$/, "") || "/";
  /**
   * 对话页（`/`）是这套外壳里唯一的**满屏工作台**（2026-11-18 合并外壳）。
   *
   * 它要三处例外，都是"满屏"这一件事的后果：
   *   · 外壳不再铺卡片（无边框/圆角/投影），高度直接铺满，
   *     滚动交给对话自己的 .chat-scroll；
   *   · 内容区不能是 <main>：那一页自己就是 <main class="workbench-main">，
   *     套起来会变成 <main><main>；
   *   · 底栏给 dock 变体，和输入区连成一块。
   */
  const isChat = here === "/";

  const shell = useMemo<ShellUi>(
    () => ({
      sidebarOpen,
      openSidebar,
      closeSidebar,
      sidebarRef,
      isChat,
      narrow,
    }),
    [sidebarOpen, openSidebar, closeSidebar, isChat, narrow],
  );

  // 与 / 同一套门：身份没确认完不给内容，免得空列表闪一下又跳回登录页
  if (auth.loading) {
    return <AuthLoading />;
  }
  if (!auth.me) {
    // 对话页在**未登录**时是落地页（Landing），那是给人看"这是什么"的地方，
    // 不能套登录卡。这一条必须在门之前分流：其余页面照旧走 AuthGate。
    if (isChat) return <>{children}</>;
    return (
      <AuthGate allowSignup={auth.allowSignup} onLogin={auth.login} onRegister={auth.register} />
    );
  }

  const rail = (
    <Rail
      overlay={narrow}
      open={sidebarOpen}
      containerRef={sidebarRef}
      username={auth.me.user.username}
      displayName={auth.me.user.nickname || auth.me.user.username}
      avatar={auth.me.user.avatar}
      isAdmin={auth.me.is_admin}
      onOpenUsage={openUsage}
      onLogout={onLogout}
      onCloseRail={closeSidebar}
    />
  );

  return (
    <UserCtx.Provider value={account}>
      <ShellCtx.Provider value={shell}>
        {/* 滚动容器必须由外壳自己提供。
            base.css 给 html/body 设了 height:100% + overflow:hidden —— 那是首页工作台
            「固定视口、滚动发生在内部面板」的做法；控制台页面直接铺在 body 里，
            没有这层容器就会被内容顶出视口且无法滚动（移动端表现为「页面被固定死」）。
            height 用 dvh（移动端地址栏收起/展开时视口高度会变，vh 会算错）。 */}
        <div
          className={
            "console-scroll" + (isChat ? " console-scroll--chat" : "") + (tabOpen ? " is-locked" : "")
          }
          ref={scrollRef}
        >
          <div
            className={"console-shell mx-auto" + (isChat ? " console-shell--chat" : "")}
            style={{ maxWidth: isChat ? undefined : 1220 }}
          >
            {/* 全局唯一的左栏。窄屏下它自己变成抽屉（CSS 控制），
                所以这里不再有"对话页渲染 / 其它页隐藏"的分支。 */}
            {rail}

            {/* 对话页的内容区不能是 <main>：那一页自己就是 <main class="workbench-main">
                （它承担满屏工作台的布局与层级），套起来会变成 <main><main>。 */}
            {isChat ? (
              <div className="console-page console-page--chat">{children}</div>
            ) : (
              <main className="console-page">{children}</main>
            )}
          </div>

          {/* 手机底部 tab + 「更多」面板。固定在视口底部而不是贴在卡片里：
              贴在卡片里会跟着内容滚走，滚到第二屏就没有导航了。
              组件默认 display:none，≤900 才出现（CSS 控制，首屏不会抖）。
              对话页给 dock 变体：它的输入区紧贴在底栏上方。 */}
          <ConsoleTabBar
            isAdmin={auth.me?.is_admin}
            onOpenChange={setTabOpen}
            variant={isChat ? "dock" : undefined}
          />

          {/* 首次进平台的上手说明也挂在这里：用户可能登录后直接落在 /path、/delegate，
              不一定先看到首页。 */}
          <GuideModal userId={auth.me.user.id} />
          <AnnounceModal userId={auth.me.user.id} />
        </div>

        {usageOpen && <UsagePanel onClose={() => setUsageOpen(false)} />}
      </ShellCtx.Provider>
    </UserCtx.Provider>
  );
}
