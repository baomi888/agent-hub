"use client";

/**
 * 工具集页（/tools）的手机底部导航。
 *
 * 为什么需要这层薄壳：`ConsoleTabBar` 要 `isAdmin` 才能决定「更多」面板里
 * 有没有「工作流 / 看板」这两格，而 `/tools` 的 page.tsx 是**服务端组件**
 *（要导出 metadata），拿不到登录态。这里只补 useAuth 这一层，
 * 导航本体仍然是同一个组件、同一份 `lib/nav` 清单。
 *
 * ★ 为什么不干脆不传 isAdmin（省掉这次 auth 请求）：
 *   那样管理员在待办页看到「更多」里 9 格、点进工具集变 8 格 ——
 *   同一个面板在不同页面上不一样，比多一次 /auth/me 难解释得多。
 *   而且 authStore 是单例：从控制台点进来时 `me` 已经在内存里，不会重复请求。
 *
 * ★ 为什么不套 ConsoleLayout（那才是"和别的页完全一样"）：
 *   这一页的滚动容器 `.campus-scroll` 是 `height:100dvh` 自成一屏，
 *   塞进外壳（有 padding、有 console-scroll）会出现两层滚动、版心被压两次。
 *   所以走的是和首页（/）同一条路：**页面自己布局，只单独挂一条 tab**。
 */
import ConsoleTabBar from "@/components/console/ConsoleTabBar";
import { useAuth } from "@/lib/hooks/useAuth";

export default function CampusTabBar() {
  const { me } = useAuth();
  return <ConsoleTabBar isAdmin={me?.is_admin} />;
}
