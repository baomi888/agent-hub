"use client";

/**
 * 手机底部 tab（≤900 才出现，CSS 控制）。
 *
 * 挂载点有四处，全部共用这一份：
 *   · `(console)/layout.tsx` → ConsoleLayout（8 个控制台页面）
 *   · 首页 `/`（2026-11-16 恢复，用 variant="dock" 与输入区连成一块）
 *   · 论坛 `/forum`（2026-11-16 补上：它是一级 tab，原来却没有底栏）
 * 内容与排序全部来自 `lib/nav`：被 `mobileTab` 标记的进 tab，其余进「更多」面板。
 * 桌面侧栏用的是同一份清单，三处不会各自漂移。
 *
 * ★ 首页曾经撤掉过（2026-11-13「影响整体性」），2026-11-16 恢复。
 *   当时的形状是「满屏卡片 + 底部一条独立的 50px 导航」，两条边线挨着，
 *   卡片看着被切了两截。现在的做法是让导航**并进输入区**：
 *   高度由 main 让出、导航去掉自己的上边框与投影（variant="dock"），
 *   由输入区那条线当唯一分隔。撤与恢复的理由都写在 page.tsx 里。
 *   ★ 撤掉时连带要改 fx.css 里 `main.workbench-main` 的窄屏高度 ——
 *     不扣 --tabbar-h 会空出 50px 白边（tests/test_console_nav.py 钉住这一对）。
 *
 * 内容与排序全部来自 `lib/nav`：被 `mobileTab` 标记的进 tab，其余进「更多」面板。
 * 桌面侧栏用的是同一份清单，三处不会各自漂移。
 *
 * ★ 2026-11-14 iOS 内测改造：**不再是"前 4 项切片"**。
 *   旧写法 `items.slice(0, TAB_PRIMARY_COUNT)` 跟着桌面顺序走，导致「学习路径」
 *   （桌面排第 9）在手机上没有任何一级入口 —— 而它的定位是和「对话」平行的。
 *   现在改由 `mobileTab` 显式指定格位、`desktopOnly` 标记「桌面有、手机没有」，
 *   详见 lib/nav.ts 顶部注释。
 */
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import NavIcon from "../NavIcon";
import { haptic } from "@/lib/haptics";
import { isCurrentNav, mobileMoreNav, mobileTabNav } from "@/lib/nav";

type Props = {
  isAdmin?: boolean;
  /** 面板开合变化时通知外层（外壳用它锁住底层滚动） */
  onOpenChange?: (open: boolean) => void;
  /**
   * 外观变体。
   * `dock`：和页面底部的输入区连成一块 —— 去掉自己的上边框与投影，
   *   让输入区那条线当唯一的分隔。专给首页用：首页的输入区紧贴在导航上方，
   *   两条边线挨在一起会把「一张纸」看成「内容 + 工具栏」两截（2026-11-16）。
   * 不传 = 控制台那 8 个页面的独立条（自带边框与投影，压在纸面上）。
   */
  variant?: "dock";
};

export default function ConsoleTabBar({ isAdmin, onOpenChange, variant }: Props) {
  const pathname = usePathname();
  const here = pathname.replace(/\/+$/, "") || "/";
  const sheetRef = useRef<HTMLDivElement | null>(null);
  const [moreOpen, setMoreOpen] = useState(false);

  const primaryNav = mobileTabNav(isAdmin);
  const moreNav = mobileMoreNav(isAdmin);
  const moreActive = moreNav.some((it) => isCurrentNav(it.href, here));

  // 换页就把面板收起来：从面板里点「设置」跳过去后，面板还盖在新页面上，
  // 用户会以为没跳成功。
  useEffect(() => {
    setMoreOpen(false);
  }, [pathname]);

  useEffect(() => {
    onOpenChange?.(moreOpen);
  }, [moreOpen, onOpenChange]);

  // 面板打开时：焦点移进面板、Esc 关闭。焦点必须进来 ——
  // 否则键盘用户点开「更多」以后还停在原来那个 tab 上，
  // Tab 键会走进被遮罩盖住的页面里。
  useEffect(() => {
    if (!moreOpen) return;
    sheetRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMoreOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [moreOpen]);

  /* ---------- 接管系统返回键（评审 A3 / P2，2026-11-16）----------
   *
   * 症状：壳（mobile/App.tsx）的 hardwareBackPress 只会 `webRef.goBack()`，
   * 网页侧根本不知道面板开着 —— 安卓上按返回，整页退后一屏，面板还留在屏幕上。
   *
   * 做法：面板打开时往历史里压一条空条目，`popstate` 里关面板。
   * 这样「返回」先命中我们这条：页面不动、面板关掉；再按一次才真的离开。
   *
   * 两条关闭路径必须分开处理，否则一定会踩到其中一个：
   *   · 按返回关 → 浏览器已经退掉那条，**不能**再退一次（不然连上一步都没了）
   *   · 点关闭 / 点遮罩 / Esc 关 → 那条还留在历史里，得自己 `back()` 退掉，
   *     否则用户下次真按返回会「按了没反应」
   *
   * 「点面板里的链接跳走」是第三个坑：换页时面板由上面的 effect 自动关闭，
   * 此时 URL 已经变了 —— 绝不能在这里 `back()`，那会把刚跳过去的那一页退回来
   * （表现为"点设置又弹回首页"）。判据就是「URL 变没变」。
   * 代价是留下一层无名历史：从设置页按返回会先回到原页，而设置页返回原页本来
   * 就是用户想要的，所以这层残留不改变可感知的行为。
   */
  const sheetHistory = useRef<{ href: string; pushed: boolean }>({ href: "", pushed: false });
  useEffect(() => {
    if (!moreOpen) return;
    const at = window.location.href;
    // 带上面 Next 自己的状态字段：只加一个标记，别把它的 __NA 之类冲掉，
    // 冲掉了 Next 会认为"这不是我的历史条目"，回退时不知道该恢复哪棵树
    window.history.pushState({ ...window.history.state, __moreSheet: 1 }, "");
    sheetHistory.current = { href: at, pushed: true };

    const onPop = () => {
      sheetHistory.current = { href: "", pushed: false };
      setMoreOpen(false);
    };
    window.addEventListener("popstate", onPop);

    return () => {
      window.removeEventListener("popstate", onPop);
      const st = sheetHistory.current;
      sheetHistory.current = { href: "", pushed: false };
      if (!st.pushed) return;
      // 换页导致的关闭：那条历史已被新页面顶掉，退它等于退掉新页面
      if (window.location.href !== st.href) return;
      window.history.back();
    };
  }, [moreOpen]);

  return (
    <>
      <nav className={variant === "dock" ? "tabbar tabbar--dock" : "tabbar"} aria-label="主导航">
        {primaryNav.map((it) => (
          <Link
            key={it.href}
            href={it.href}
            className="tabbar-item"
            aria-current={isCurrentNav(it.href, here) ? "page" : undefined}
            scroll={false}
            // 触觉：只在**真的换页**时给一次。点当前这一格不给 ——
            // 原生 App 里点已选中的 tab 也不给反馈，给了反而像"操作成功了"
            onClick={() => {
              if (!isCurrentNav(it.href, here)) haptic("select");
            }}
          >
            <NavIcon name={it.icon} className="h-[20px] w-[20px]" />
            <span>{it.label}</span>
          </Link>
        ))}
        <button
          type="button"
          className="tabbar-item"
          onClick={() => {
            haptic("select");
            setMoreOpen((v) => !v);
          }}
          aria-expanded={moreOpen}
          aria-haspopup="dialog"
          // 当前页在「更多」里时，「更多」自己要点亮 —— 否则整条 tab 一个亮的都没有，
          // 用户会以为自己不在任何页面里
          data-active={moreActive ? "1" : undefined}
        >
          <NavIcon name="more" className="h-[20px] w-[20px]" />
          <span>更多</span>
        </button>
      </nav>

      {moreOpen ? (
        <>
          <div className="tab-sheet-scrim" onClick={() => setMoreOpen(false)} aria-hidden="true" />
          <div
            className="tab-sheet"
            role="dialog"
            aria-modal="true"
            aria-label="更多入口"
            tabIndex={-1}
            ref={sheetRef}
          >
            <div className="tab-sheet-grab" aria-hidden="true" />
            <div className="tab-sheet-head">
              <b>更多</b>
              <button type="button" className="icon-btn" onClick={() => setMoreOpen(false)}>
                关闭
              </button>
            </div>
            <div className="tab-sheet-grid">
              {moreNav.map((it) => (
                <Link
                  key={it.href}
                  href={it.href}
                  className="tab-sheet-item"
                  aria-current={isCurrentNav(it.href, here) ? "page" : undefined}
                  scroll={false}
                  onClick={() => haptic("select")}
                >
                  <NavIcon name={it.icon} className="tab-sheet-ico" />
                  {/* 名称 + 一句用途。hint 是可选的：没写 hint 的项只显示名称，
                      靠 .tab-sheet-item 的 align-items 保持图标与文字仍然对齐。
                      不用 title 属性兜底 —— 手机上没有 hover，写了也看不到。 */}
                  <span className="tab-sheet-tx">
                    <b>{it.label}</b>
                    {it.hint ? <em>{it.hint}</em> : null}
                  </span>
                </Link>
              ))}
            </div>
          </div>
        </>
      ) : null}
    </>
  );
}
