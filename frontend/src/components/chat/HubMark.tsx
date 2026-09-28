// 品牌 icon：漫画风苞米吉祥物（消息头像等小尺寸场景）
import MascotLogo from "./MascotLogo";
export default function HubMark({ className = "h-4 w-4" }: { className?: string }) {
  return <MascotLogo className={className} title="苞米" />;
}
