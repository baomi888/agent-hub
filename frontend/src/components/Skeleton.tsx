"use client";

/**
 * 骨架占位。换掉「正在加载…」那一行字。
 *
 * 为什么值得换：文字加载态是「先出现一行小字，再跳成内容」，切页时屏幕会闪一下；
 * 骨架屏把这一步藏起来 —— 首帧就是内容的**形状**，数据到了只换颜色不换布局。
 * 这不是"更好看"，是减少一次布局跳变（评审 A4）。
 *
 * 样式：`.skel` / `.skel-block` 在 console.css 与 fx.css，两者都在 globals.css 里
 * 全局引入，所以公开页（/forum、留言板）也能直接用，不必把 console 的样式拖过去。
 *
 * 无障碍：灰条对读屏没有意义，所以整块 `aria-busy` + 一句 sr-only 的 label；
 * 视觉用户看灰条，读屏用户听到「正在加载」。**别只留灰条不写 label** ——
 * 那样读屏用户会遇到一块完全空白的区域（比原来那行文字更糟）。
 *
 * 动效：`.skel` 的呼吸动画在 a11y.css 里已被 `prefers-reduced-motion` 全局关掉，
 * 这里不用再判一次。
 */
import type { ReactNode } from "react";

type LinesProps = {
  /** 每条灰条的宽度。给 2–4 条最像正文；条数超过 4 会让首屏看起来"很重" */
  widths?: string[];
  /** 读屏文案。默认「正在加载」，页面可给更具体的（如「正在加载话题」） */
  label?: string;
};

export function SkeletonLines({ widths = ["80%", "100%", "60%"], label = "正在加载" }: LinesProps) {
  return (
    <div role="status" aria-busy="true" aria-live="polite">
      <span className="sr-only">{label}</span>
      {widths.map((w, i) => (
        <div
          key={i}
          className="skel"
          // 第一条不加 margin：交给外层容器的 gap 或自身位置决定上边距，
          // 否则骨架整体会比真实内容低 8px，数据到了再往上一跳
          style={{ width: w, marginTop: i ? 8 : 0 }}
          aria-hidden="true"
        />
      ))}
    </div>
  );
}

type CardsProps = {
  /** 骨架卡片张数。列表页给 3 张即可 —— 再多只会把首屏塞满灰块 */
  rows?: number;
  widths?: string[];
  label?: string;
  /** 卡片外面再包一层时的额外内容（如分组标题位），一般不用 */
  children?: ReactNode;
};

/**
 * 「列表型」页面的骨架：N 张带边框的空卡，每张几行灰条。
 *
 * 与 `SkeletonLines` 的区别：这个有卡片外壳（`.skel-block`），长得像帖子列表 / 留言列表；
 * 那个只有几条线，适合塞在已有卡片内部（打卡页、看板就是这么用的）。
 */
export function SkeletonCards({ rows = 3, widths = ["68%", "94%", "52%"], label = "正在加载" }: CardsProps) {
  return (
    <div className="skel-cards" role="status" aria-busy="true" aria-live="polite">
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="skel-block" aria-hidden="true">
          {widths.map((w, i) => (
            <div key={i} className="skel" style={{ width: w }} />
          ))}
        </div>
      ))}
    </div>
  );
}
