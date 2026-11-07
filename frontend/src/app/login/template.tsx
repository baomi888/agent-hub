// 登录页（/login）的切页进场。
// 从落地页点「开始使用」进来时是一整屏切换，加一层 180ms 淡入，
// 免得登录卡片凭空"啪"地出现。
import PageEnter from "@/components/PageEnter";

export default function Template({ children }: { children: React.ReactNode }) {
  return <PageEnter>{children}</PageEnter>;
}
