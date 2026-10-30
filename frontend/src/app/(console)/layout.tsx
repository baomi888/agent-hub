// 控制台页面组的布局：侧栏、登录门、滚动容器都在这里，**只挂载一次**。
//
// 把 8 个页面放进同一个 route group 的唯一目的就是这个：
// Next 在同一个 layout 内切换 page 时不会重建 layout，所以点左侧导航时
// 侧栏不闪、滚动容器不重建、登录门不重跑（详见 components/console/ConsoleLayout.tsx）。
//
// 本文件保持为 Server Component：客户端状态全部在 ConsoleLayout 里，
// 这样 layout 这一层不额外引入 "use client" 边界。
import type { ReactNode } from "react";
import ConsoleLayout from "@/components/console/ConsoleLayout";

export default function Layout({ children }: { children: ReactNode }) {
  return <ConsoleLayout>{children}</ConsoleLayout>;
}
