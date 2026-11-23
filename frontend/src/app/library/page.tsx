"use client";

// 公开知识库广场（独立路由 `/library`）。
//
// ★ 2026-11-23：主体搬到了 components/library/PlazaPane —— 资料库页的
//   「公开资料库」tab 用的是同一份实现。这里只保留"独立访问"这一种形态：
//   带自己的页头（`.lib-top`）、不套控制台外壳，因为分享出去的链接
//   和聊天首屏的卡片都还指向这条路由，它不能失效。
//
//   如果以后要给独立路由也加侧栏，正确做法是把 app/library 搬进 (console)
//   并给 PlazaPane 传 embedded，而不是在这里复制一份业务代码。
import PlazaPane from "@/components/library/PlazaPane";

export default function LibraryPage() {
  return <PlazaPane />;
}
