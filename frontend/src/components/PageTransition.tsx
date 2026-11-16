"use client";

/**
 * 切页道具：区分「切一级 tab」和「进二级页」（评审 A5 / P2，2026-11-16）。
 *
 * 问题：原来所有导航都播同一个 `.page-enter`（180ms 淡入 + 上移 2px），
 * 于是"点底栏换 tab"和"点进资料库详情"看起来一模一样，用户分不清
 * "我切换了一个区"和"我往深处走了一层"。
 *
 * 做法：目标是一级 tab（手机底栏那几格）→ 只淡入；否则 → 从右 8px 推入。
 * 一级 tab 的集合从 `lib/nav` 推导（`mobileTab` 有值的那些），不另抄一份 ——
 * 抄一份的话以后改导航，动画就会跟错。
 *
 * ★ 为什么用模块级变量记「上一页」：Next 的 `template.tsx` 在**每次导航时重新挂载**
 *   （这正是它适合放进场动画的原因），state / ref 都活不过一次导航，
 *   只有模块作用域的变量能跨挂载保留。同一次会话的标签页里是单例，够用。
 *
 * ★ 首帧（直接打开链接、刷新）不播位移动画：没有"从哪来"，播了会像页面自己在抖。
 *   服务端渲染时 `lastPath` 恒为 null，输出的是基础类，客户端首帧也一样 ——
 *   不会出现 hydration 前后 className 不一致的报错。
 *
 * ★ 位移幅度只有 8px，且用 `animation-fill-mode: backwards`：动画结束后
 *   不留 `transform` 残值。残值会持续创建 containing block，把页面里那些
 *   `position: fixed` 的浮层（遮罩、抽屉、toast）的定位基准从视口改成这层 div ——
 *   首页早年为这个坑专门只动 opacity（见 `.page-fade` 的注释）。
 */
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { NAV } from "@/lib/nav";

const TAB_HREFS = new Set(
  NAV.filter((it) => typeof it.mobileTab === "number").map((it) => it.href),
);

let lastPath: string | null = null;

export default function PageTransition({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const here = (pathname || "/").replace(/\/+$/, "") || "/";
  const prev = lastPath;
  lastPath = here;

  // 首帧不判方向，保持原来的基础进场
  const kind = prev === null ? "" : TAB_HREFS.has(here) ? " page-enter--tab" : " page-enter--push";

  return <div className={"page-enter" + kind}>{children}</div>;
}
