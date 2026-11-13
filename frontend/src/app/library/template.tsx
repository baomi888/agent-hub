// 公开知识库广场（/library）的切页进场：从工作台点进来、或点「工作台」回去，
// 都靠这一层淡入，不至于硬切。动画定义见 components/PageEnter.tsx。
import PageEnter from "@/components/PageEnter";

export default function Template({ children }: { children: React.ReactNode }) {
  return <PageEnter>{children}</PageEnter>;
}
