/**
 * 校园工具集 · 路由薄壳
 *
 * 只做三件事：引入板块实现、套上控制台页头、挂样式。**不写业务逻辑**。
 * 判断标准：这个文件超过 40 行，说明实现写错地方了（见 campus/docs/01）。
 *
 * URL：/tools
 * 旧静态页 frontend/public/tools.html 已由 next.config.ts 的 redirects 301 到 /tools
 * （2026-11-02）；文件本身保留，作为 campus/logic 里多处算法对照的源码依据。
 *
 * ★ 为什么现在在 (console) 组里（2026-11-13 第三轮）：
 *   它原本在 (campus) 组，不套外壳 —— 从侧栏或「更多」点进来等于**整屏换掉**：
 *   宽屏没有侧栏、手机没有底部 tab，想换功能只能先点「← 回首页」。
 *   用户的诉求是「和跳转设置一样」，所以并进 (console)，外壳（侧栏 + tab +
 *   滚动容器 + 登录门）由 app/(console)/layout.tsx 统一挂，这里不再自己造。
 *
 *   连带的两件事，都在这个 PR 里做掉了：
 *     · CampusTabBar 薄壳删除 —— 那是「不套外壳」时临时补的一条 tab；
 *     · CampusToolsHub 里的 .campus-scroll / .campus-bar / .campus-head 全部拆掉，
 *       滚动交给 .console-scroll，页头交给 PageShell，返回入口交给侧栏。
 *
 *   代价：这一页从「免登录可用」变成需要登录（ConsoleLayout 有登录门）。
 *   真实入口本来就都在登录后（侧栏、首页工具行），直连 /tools 的访客会被挡到登录页。
 */
import type { Metadata } from "next";
import PageShell from "@/components/console/PageShell";
import CampusToolsHub from "@/campus/components/CampusToolsHub";
import "@/campus/styles/campus.css";

export const metadata: Metadata = {
  title: "校园工具集 · 知穗",
  description: "GPA 分析、番茄钟、课程表、单词卡等校园小工具。数据先存本机，登录后同步到云端。",
};

export default function CampusToolsPage() {
  return (
    <PageShell
      eyebrow="校园生活"
      title="工具集"
      sub="不用连教务，打开就能用。数据先存在这台设备上，登录后会同步到云端，换设备也在。"
    >
      <CampusToolsHub />
    </PageShell>
  );
}
