// 控制台页面组的布局：侧栏、登录门、滚动容器都在这里，**只挂载一次**。
//
// 把页面放进同一个 route group 的唯一目的就是这个：
// Next 在同一个 layout 内切换 page 时不会重建 layout，所以点左侧导航时
// 侧栏不闪、滚动容器不重建、登录门不重跑（详见 components/console/ConsoleLayout.tsx）。
//
// ★ 2026-11-22：会话域（ChatProvider）也提到这一层。
//   原来会话列表与消息状态住在 `(console)/page.tsx` 里 —— 一切到别的页面，
//   列表滚动位置就丢，正在生成的回答也因为接收方（页面）卸载而断掉。
//   现在状态比页面活得久：页面只负责渲染，接收方在外壳。
//
// 本文件保持为 Server Component：客户端状态全部在 ChatProvider / ConsoleLayout 里，
// 这样 layout 这一层不额外引入 "use client" 边界。
import type { ReactNode } from "react";
import ConsoleLayout from "@/components/console/ConsoleLayout";
import ChatProvider from "@/components/shell/ChatProvider";

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <ChatProvider>
      <ConsoleLayout>{children}</ConsoleLayout>
    </ChatProvider>
  );
}
