"use client";

// 四屏（每日委托 / 总结 / 打卡 / 设置）共用的应用外壳。
//
// 为什么不复用 Sidebar：那个侧栏的主语是**会话列表**，这四屏的主语是**页面**。
// 把页面导航硬塞进会话栏，会让「我要找昨天那个对话」和「我要去设置」挤在同一条轴线上。
// 所以这里另起一个 196px 的 mini-nav —— 与设计稿一致，也与工作台共用同一套令牌。
//
// 登录门放在这一层：四屏的数据（用量、会话、知识库）全部按用户隔离，
// 和 / 一样必须整页挡住，不能只挡数据。
import Link from "next/link";
import { createContext, useContext, type ReactNode } from "react";
import AuthGate from "../AuthGate";
import CornIcon from "../CornIcon";
import GuideModal from "../guide/GuideModal";
import { useAuth } from "@/lib/hooks/useAuth";
import type { MeOut } from "@/lib/types";

export type ConsolePage =
  | "delegate"
  | "report"
  | "checkin"
  | "cet"
  | "path"
  | "settings"
  | "flow"
  | "dashboard";

interface NavItem {
  key: ConsolePage | null;
  href: string;
  icon: string;
  label: string;
  /** 只有开发者看得到（后端仍会二次校验，这里只是不给入口） */
  adminOnly?: boolean;
}

const NAV: NavItem[] = [
  { key: null, href: "/", icon: "notes", label: "对话" },
  { key: null, href: "/?kb=1", icon: "book", label: "知识库" },
  { key: "delegate", href: "/delegate", icon: "clock", label: "每日委托" },
  { key: "report", href: "/report", icon: "calendar", label: "总结" },
  { key: "checkin", href: "/checkin", icon: "star", label: "打卡" },
  // 四六级备考工作区：计划 / 词汇本 / 模考分数（申报书 B3 的专项模块入口）
  { key: "cet", href: "/cet", icon: "globe", label: "四六级" },
  // 关卡式学习路径：12 关由真实学习数据判定（申报书 3.4 / 2.8）
  { key: "path", href: "/path", icon: "target", label: "学习路径" },
  // 混合架构：工作流定义 + 切换规则。**这是开发者调试面，不是用户功能**
  // —— 界面上摊着 UUID、任务 id、日志入口和可编辑的 JSON 定义。
  // 2026-10-27 内测反馈："这个为啥要给用户看"。与 /dashboard 同一性质，
  // 因此同样加 adminOnly。注意：这只是不给误点的机会，不是安全边界
  // （真隐藏需要后端也返 404，本期没做 —— 用户确认"前端看不到就行"）。
  { key: "flow", href: "/flow", icon: "target", label: "工作流", adminOnly: true },
  // 数据看板：开发者看"有多少人、在调什么接口、反馈堆了多少"。
  // 只在 is_admin 时渲染入口 —— 但**真正的权限在后端**（非管理员返回 404，
  // 伪装成接口不存在，见 api/admin.py）。
  // 前端隐藏只是不给误点的机会，不是安全边界
  { key: "dashboard", href: "/dashboard", icon: "briefcase", label: "看板", adminOnly: true },
  { key: "settings", href: "/settings", icon: "settings", label: "设置" },
];

interface Props {
  /** 当前页 —— 由页面自己传，不靠 pathname 反推：
   *  「知识库」指向 /?kb=1，它的 pathname 也是 /，光看路径分不清 */
  active: ConsolePage;
  /** 眉题（设计稿里的 P4 · /delegate） */
  eyebrow: string;
  title: string;
  sub: ReactNode;
  /** 页头右侧的操作区 */
  actions?: ReactNode;
  children: ReactNode;
}

/**
 * 登录信息往下传一层。
 *
 * 为什么需要：`useAuth` 是**每个实例各查一次** /api/auth/me。
 * 子页面如果自己再调一次 useAuth，它拿到的是一份独立状态 —— 用户在
 * 登录门里登录时，子页面那份不会跟着变（还是 null），账号名就会空着。
 * 所以由外壳查一次、往下发。
 */
const UserCtx = createContext<MeOut | null>(null);

export function useConsoleUser(): MeOut | null {
  return useContext(UserCtx);
}

export default function ConsoleShell({ active, eyebrow, title, sub, actions, children }: Props) {
  const auth = useAuth();

  // 与 / 同一套门：身份没确认完不给内容，免得空列表闪一下又跳回登录页
  if (auth.loading) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-canvas">
        <p className="text-fs-sm text-faint">正在确认登录状态...</p>
      </div>
    );
  }
  if (!auth.me) {
    return (
      <AuthGate allowSignup={auth.allowSignup} onLogin={auth.login} onRegister={auth.register} />
    );
  }

  return (
    <UserCtx.Provider value={auth.me}>
      {/* 滚动容器必须由外壳自己提供。
          base.css 给 html/body 设了 height:100% + overflow:hidden —— 那是首页工作台
          「固定视口、滚动发生在内部面板」的做法；四屏直接铺在 body 里，没有这层容器
          就会被内容顶出视口且无法滚动（移动端表现为「页面被固定死」）。
          height 用 dvh（移动端地址栏收起/展开时视口高度会变，vh 会算错）。 */}
      <div className="console-scroll">
        <div className="console-shell mx-auto" style={{ maxWidth: 1220 }}>
          <aside className="console-nav">
            <div className="nav-brand">
              <CornIcon name="corn" className="logo" title="知穗" />
              <b>知穗</b>
            </div>
            <nav aria-label="主导航">
              {NAV.filter((it) => !it.adminOnly || auth.me?.is_admin).map((it) => {
                const current = it.key !== null && it.key === active;
                return (
                  <Link
                    key={it.href}
                    href={it.href}
                    className="nav-item"
                    aria-current={current ? "page" : undefined}
                  >
                    <CornIcon name={it.icon} />
                    {it.label}
                  </Link>
                );
              })}
            </nav>
          </aside>

          <div className="console-page">
            <div className="pg-head">
              <div>
                <p className="eyebrow">{eyebrow}</p>
                <h2>{title}</h2>
                <p className="pg-sub">{sub}</p>
              </div>
              {actions ? <div className="pg-actions">{actions}</div> : null}
            </div>
            <div style={{ marginTop: 18 }}>{children}</div>
          </div>
        </div>

        {/* 首次进平台的上手说明也挂在这里：用户可能登录后直接落在 /path、/delegate，
            不一定先看到首页。和 ChatArea 那处不会重复 —— 首页不走 ConsoleShell，
            而 localStorage 的"已读"标记本身也是幂等的（两边同读一个 key）。 */}
        <GuideModal userId={auth.me.user.id} />
      </div>
    </UserCtx.Provider>
  );
}
