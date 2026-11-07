// 设置提醒教程（/reminder）的切页进场。
// 从落地页、设置页两处点进来都是站内跳转，加一层淡入让"换页"有个交代。
import PageEnter from "@/components/PageEnter";

export default function Template({ children }: { children: React.ReactNode }) {
  return <PageEnter>{children}</PageEnter>;
}
