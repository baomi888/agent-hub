// 装到桌面教程（/install）的切页进场。
// 从落地页、登录卡片、设置页三处点进来都是站内跳转，加一层淡入让"换页"有个交代。
import PageEnter from "@/components/PageEnter";

export default function Template({ children }: { children: React.ReactNode }) {
  return <PageEnter>{children}</PageEnter>;
}
