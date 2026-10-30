"use client";

// 控制台外壳（持久化版）：侧栏 + 登录门 + 滚动容器。
//
// 为什么从 ConsoleShell 拆出来：原先 8 个页面各自渲染一份 ConsoleShell，
// 于是每次点左侧导航都是「整块卸载 → 整块重新挂载」：
//   · 侧栏的选中态、品牌区、图标全部重建，视觉上闪一下；
//   · 新页面自己那份 useAuth 又从 loading 开始，页面先白一帧再出内容；
//   · 滚动位置丢掉，且每次都要重新算侧栏布局。
// 现在外壳进 app/(console)/layout.tsx，Next 在同一个 layout 内切换 page 时**只换 children**，
// 侧栏是同一个 DOM 实例，从根上不会闪。页面内容由 (console)/template.tsx 做一次淡入，
// 观感与 DeepSeek 侧栏切页一致。
//
// 与之配套的约定：
//   · 页面只负责页头 + 内容（PageShell），不再关心侧栏与登录门；
//   · 当前页由 pathname 推导，页面不用再手传 active（过去 /?kb=1 那种歧义随独立
//     知识库页一起消失了）。
import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useContext, useEffect, useRef, type ReactNode } from "react";
import AuthGate from "../AuthGate";
import CornIcon from "../CornIcon";
import GuideModal from "../guide/GuideModal";
import { useAuth } from "@/lib/hooks/useAuth";
import type { MeOut } from "@/lib/types";

interface NavItem {
  href: string;
  icon: string;
  label: string;
  /** 只有开发者看得到（后端仍会二次校验，这里只是不给入口） */
  adminOnly?: boolean;
}

const NAV: NavItem[] = [
  { href: "/", icon: "notes", label: "对话" },
  // 知识库是**独立页面**（2026-10-30）。原先它指向 /?kb=1，和「对话」落到同一个路由，
  // 只是首页右栏抽屉被拉开 —— 用户的原话是"点击对话和点击知识库都是一样的跳转"。
  { href: "/knowledge", icon: "book", label: "知识库" },
  { href: "/delegate", icon: "clock", label: "每日委托" },
  { href: "/report", icon: "calendar", label: "总结" },
  { href: "/checkin", icon: "star", label: "打卡" },
  // 四六级备考工作区：计划 / 词汇本 / 模考分数（申报书 B3 的专项模块入口）
  { href: "/cet", icon: "globe", label: "四六级" },
  // 关卡式学习路径：12 关由真实学习数据判定（申报书 3.4 / 2.8）
  { href: "/path", icon: "target", label: "学习路径" },
  // 混合架构：工作流定义 + 切换规则。**这是开发者调试面，不是用户功能**
  // —— 界面上摊着 UUID、任务 id、日志入口和可编辑的 JSON 定义。
  // 2026-10-27 内测反馈："这个为啥要给用户看"。与 /dashboard 同一性质，因此同样加 adminOnly。
  // 注意：这只是不给误点的机会，不是安全边界（真隐藏需要后端也返 404，本期没做）。
  { href: "/flow", icon: "target", label: "工作流", adminOnly: true },
  // 数据看板：开发者看"有多少人、在调什么接口、反馈堆了多少"。
  // 只在 is_admin 时渲染入口 —— 但**真正的权限在后端**（非管理员返回 404，见 api/admin.py）。
  { href: "/dashboard", icon: "briefcase", label: "看板", adminOnly: true },
  { href: "/settings", icon: "settings", label: "设置" },
];

/**
 * 登录信息往下传一层。
 *
 * 为什么需要：`useAuth` 是**每个实例各查一次** /api/auth/me。
 * 子页面如果自己再调一次 useAuth，它拿到的是一份独立状态 —— 用户在
 * 登录门里登录时，子页面那份不会跟着变（还是 null），账号名就会空着。
 */
const UserCtx = createContext<MeOut | null>(null);

export function useConsoleUser(): MeOut | null {
  return useContext(UserCtx);
}

export default function ConsoleLayout({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const pathname = usePathname();
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // 切页回到顶部。滚动发生在这个 div 上（html/body 被 base.css 锁了），
  // 所以 Next 自带的 scroll-to-top 管不到 —— 不做这一步，从长页面跳到短页面
  // 会停在半空，看起来像"页面没加载完"。
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0, left: 0, behavior: "auto" });
  }, [pathname]);

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

  // 规范化的当前路径：去掉尾斜杠，查询串不参与比较
  const here = pathname.replace(/\/+$/, "") || "/";

  return (
    <UserCtx.Provider value={auth.me}>
      {/* 滚动容器必须由外壳自己提供。
          base.css 给 html/body 设了 height:100% + overflow:hidden —— 那是首页工作台
          「固定视口、滚动发生在内部面板」的做法；控制台页面直接铺在 body 里，
          没有这层容器就会被内容顶出视口且无法滚动（移动端表现为「页面被固定死」）。
          height 用 dvh（移动端地址栏收起/展开时视口高度会变，vh 会算错）。 */}
      <div className="console-scroll" ref={scrollRef}>
        <div className="console-shell mx-auto" style={{ maxWidth: 1220 }}>
          <aside className="console-nav">
            <div className="nav-brand">
              <CornIcon name="corn" className="logo" title="知穗" />
              <b>知穗</b>
            </div>
            <nav aria-label="主导航">
              {NAV.filter((it) => !it.adminOnly || auth.me?.is_admin).map((it) => {
                // 首页是精确匹配；其余按前缀匹配，将来 /knowledge/xxx 也能点亮同一项
                const current = it.href === "/" ? here === "/" : here === it.href || here.startsWith(it.href + "/");
                return (
                  <Link
                    key={it.href}
                    href={it.href}
                    className="nav-item"
                    aria-current={current ? "page" : undefined}
                    // 滚动容器是自己那层，别让 Next 去动 window
                    scroll={false}
                  >
                    <CornIcon name={it.icon} />
                    {it.label}
                  </Link>
                );
              })}
            </nav>
          </aside>

          <main className="console-page">{children}</main>
        </div>

        {/* 首次进平台的上手说明也挂在这里：用户可能登录后直接落在 /path、/delegate，
            不一定先看到首页。和 ChatArea 那处不会重复 —— 首页不走这个 layout，
            而 localStorage 的"已读"标记本身也是幂等的（两边同读一个 key）。 */}
        <GuideModal userId={auth.me.user.id} />
      </div>
    </UserCtx.Provider>
  );
}
