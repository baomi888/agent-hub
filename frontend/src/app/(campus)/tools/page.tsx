/**
 * 校园工具集 · 路由薄壳
 *
 * 只做三件事：引入板块实现、挂手机底部导航、挂样式。**不写业务逻辑**。
 * 判断标准：这个文件超过 30 行，说明实现写错地方了（见 campus/docs/01）。
 *
 * URL：/tools
 * 旧静态页 frontend/public/tools.html 已由 next.config.ts 的 redirects 301 到 /tools
 * （2026-11-02）；文件本身保留，作为 campus/logic 里多处算法对照的源码依据。
 */

import type { Metadata } from "next";
import CampusToolsHub from "@/campus/components/CampusToolsHub";
import CampusTabBar from "@/campus/components/CampusTabBar";
import "@/campus/styles/campus.css";

export const metadata: Metadata = {
  title: "校园工具集 · 知穗",
  description: "GPA 分析、番茄钟、课程表、单词卡等校园小工具，免登录即可用；不登录时数据只存本机。",
};

/**
 * 底部导航为什么挂在这里（2026-11-13 第二轮）：
 * 这一页在 (campus) 路由组里，不套 ConsoleLayout —— 用户从「更多」点进工具集后，
 * 底部那条 tab 当场消失，想换个功能只能先点「← 回首页」。这是一级入口该有的样子吗？
 *
 * 和首页（/）是同一条路：页面自己掌管布局，只单独挂一条 tab。
 * 桌面端不受影响（.tabbar 默认 display:none，≤900 才开），侧栏仍然不在这页出现。
 */
export default function CampusToolsPage() {
  return (
    <>
      <CampusToolsHub />
      <CampusTabBar />
    </>
  );
}
