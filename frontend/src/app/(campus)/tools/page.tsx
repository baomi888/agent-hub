/**
 * 校园工具集 · 路由薄壳
 *
 * 只做两件事：引入板块实现、挂样式。**不写业务逻辑**。
 * 判断标准：这个文件超过 30 行，说明实现写错地方了（见 campus/docs/01）。
 *
 * URL：/tools
 * 旧版仍可通过 /tools.html 访问，切换入口前两者并存。
 */

import type { Metadata } from "next";
import CampusToolsHub from "@/campus/components/CampusToolsHub";
import "@/campus/styles/campus.css";

export const metadata: Metadata = {
  title: "校园工具集 · 知穗",
  description: "GPA 分析、番茄钟、课程表、单词卡等校园小工具，免登录、数据只存本机。",
};

export default function CampusToolsPage() {
  return <CampusToolsHub />;
}
