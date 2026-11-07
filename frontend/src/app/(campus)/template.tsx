// 工具集（/tools）的切页进场。
//
// 这一页不套控制台外壳，从首页或左导航点进来是"整个换一屏"，
// 没有淡入就是一记生硬的切换。动画本身见 components/PageEnter.tsx。
//
// ★ 外层这一层 div 不设高度：页面自己的滚动容器 .campus-scroll 是 height:100dvh，
//   靠视口单位撑开，不受这一层影响（反过来给它 height:100% 才会把它压成 0）。
import PageEnter from "@/components/PageEnter";

export default function Template({ children }: { children: React.ReactNode }) {
  return <PageEnter>{children}</PageEnter>;
}
