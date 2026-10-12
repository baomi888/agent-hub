// 品牌 icon：漫画风知穗吉祥物（消息头像等小尺寸场景）
import MascotLogo from "./MascotLogo";

export default function HubMark({
  className = "h-4 w-4",
  thinking = false,
}: {
  className?: string;
  /** AI 正在生成时传入，头像进入思考态（嘴变○） */
  thinking?: boolean;
}) {
  return <MascotLogo className={className} title="知穗" mood={thinking ? "thinking" : "idle"} />;
}
