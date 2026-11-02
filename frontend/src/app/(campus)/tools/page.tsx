/**
 * 校园工具集 · 路由薄壳
 *
 * 只做两件事：引入板块实现、挂样式。**不写业务逻辑**。
 * 判断标准：这个文件超过 30 行，说明实现写错地方了（见 campus/docs/01）。
 *
 * URL：/tools
 * 旧静态页 frontend/public/tools.html 已由 next.config.ts 的 redirects 301 到 /tools
 * （2026-11-02）；文件本身保留，作为 campus/logic 里多处算法对照的源码依据。
 */

import type { Metadata } from "next";
import CampusToolsHub from "@/campus/components/CampusToolsHub";
import "@/campus/styles/campus.css";

export const metadata: Metadata = {
  title: "校园工具集 · 知穗",
  description: "GPA 分析、番茄钟、课程表、单词卡等校园小工具，免登录即可用；不登录时数据只存本机。",
};

export default function CampusToolsPage() {
  return <CampusToolsHub />;
}
