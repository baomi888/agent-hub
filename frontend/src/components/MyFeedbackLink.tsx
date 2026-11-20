"use client";

/**
 * 「我的反馈」入口（2026-11-20）
 *
 * ★ 为什么是一个独立组件而不是两处各写一遍
 *   站内有**两个侧栏**：对话页用 components/Sidebar（会话列表 + 全量入口），
 *   其它控制台页用 components/console/ConsoleLayout 的 .console-nav。
 *   入口两边都要有，但红点数和拉取逻辑只该有一份 —— 写两遍必然会漂移
 *   （改了这边忘了那边，正是"侧栏换了头像没变"那类 bug 的来源）。
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";

export default function MyFeedbackLink({
  authed,
  variant = "row",
}: {
  /** 已登录才发请求（未登录调未读接口会 401，没必要） */
  authed: boolean;
  /** row = 对话页侧栏那行的样式；nav = 控制台导航项样式 */
  variant?: "row" | "nav";
}) {
  const [unread, setUnread] = useState(0);

  useEffect(() => {
    // ★ 不在 effect 体里同步 setUnread(0)：React 19 的编译器 lint 会拦
    //   （set-state-in-effect：同步 setState 会触发一次额外的级联渲染）。
    //   未登录时直接不发请求、也不改状态 —— 未登录本来就渲染不出红点，
    //   保持初值 0 即可，不需要"清一下"。
    if (!authed) return;
    let alive = true;
    const pull = () => {
      void api
        .feedbackUnread()
        .then((r) => {
          if (alive) setUnread(r.unread ?? 0);
        })
        .catch(() => {
          /* 红点是加分项，失败静默 —— 不能因为它挂了让侧栏报错 */
        });
    };
    pull();
    // 切回标签页时刷新：用户去别的设备提了反馈 / 管理员刚回完，回来就该看到
    const onVis = () => {
      if (document.visibilityState === "visible") pull();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      alive = false;
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [authed]);

  const badge =
    unread > 0 ? (
      <span className="sb-badge" aria-label={`${unread} 条新回复`}>
        {unread > 99 ? "99+" : unread}
      </span>
    ) : null;

  if (variant === "nav") {
    return (
      <Link href="/my-feedback" className="nav-item">
        <NavIconInline />
        我的反馈
        {badge}
      </Link>
    );
  }

  return (
    <Link href="/my-feedback" className="sb-myfb mt-2" title="我提过的反馈和处理进度">
      <NavIconInline />
      <span>我的反馈</span>
      {badge}
    </Link>
  );
}

/** 内联小图标：不引 NavIcon 的完整映射表（那边没有"我的反馈"这一项，
 *  为一个图标往共享映射里加名字会牵动其它页面） */
function NavIconInline() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="side-entry-ico"
      aria-hidden="true"
    >
      <path d="M21 11.5a8.5 8.5 0 0 1-12.2 7.6L3 21l1.9-5.8A8.5 8.5 0 1 1 21 11.5z" />
      <path d="M8.5 10.5h7M8.5 14h4" />
    </svg>
  );
}