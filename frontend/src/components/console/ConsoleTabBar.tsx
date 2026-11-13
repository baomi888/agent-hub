"use client";

/**
 * 手机底部 tab（≤900 才出现，CSS 控制）。
 *
 * 抽成独立组件的原因：它同时长在**控制台外壳**和**首页工作台**上。
 * 首页（/）不走 ConsoleLayout —— 它有自己的整屏工作台布局 —— 所以只把 tab
 * 挂在外壳里的话，从待办点「对话」回到首页，底部那一条会当场消失，
 * 用户会以为导航坏了。手机上的 tab 是常驻的，不该在一级页面缺席。
 *
 * 内容与排序全部来自 `lib/nav`：前 4 项进 tab，其余进「更多」面板。
 * 桌面侧栏用的是同一份清单，三处不会各自漂移。
 */
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import NavIcon from "../NavIcon";
import { TAB_PRIMARY_COUNT, isCurrentNav, visibleNav } from "@/lib/nav";

type Props = {
  isAdmin?: boolean;
  /** 面板开合变化时通知外层（外壳用它锁住底层滚动） */
  onOpenChange?: (open: boolean) => void;
};

export default function ConsoleTabBar({ isAdmin, onOpenChange }: Props) {
  const pathname = usePathname();
  const here = pathname.replace(/\/+$/, "") || "/";
  const sheetRef = useRef<HTMLDivElement | null>(null);
  const [moreOpen, setMoreOpen] = useState(false);

  const items = visibleNav(isAdmin);
  const primaryNav = items.slice(0, TAB_PRIMARY_COUNT);
  const moreNav = items.slice(TAB_PRIMARY_COUNT);
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

  return (
    <>
      <nav className="tabbar" aria-label="主导航">
        {primaryNav.map((it) => (
          <Link
            key={it.href}
            href={it.href}
            className="tabbar-item"
            aria-current={isCurrentNav(it.href, here) ? "page" : undefined}
            scroll={false}
          >
            <NavIcon name={it.icon} className="h-[20px] w-[20px]" />
            <span>{it.label}</span>
          </Link>
        ))}
        <button
          type="button"
          className="tabbar-item"
          onClick={() => setMoreOpen((v) => !v)}
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
                >
                  <NavIcon name={it.icon} className="h-[20px] w-[20px]" />
                  <span>{it.label}</span>
                </Link>
              ))}
            </div>
          </div>
        </>
      ) : null}
    </>
  );
}
