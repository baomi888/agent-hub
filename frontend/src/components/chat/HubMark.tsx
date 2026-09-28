// 品牌 icon：玉米穗 logo（消息头像等小尺寸场景）
export default function HubMark({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <img
      src="/corn-logo.webp"
      alt=""
      width={28}
      height={28}
      decoding="async"
      className={`${className} corn-logo object-contain`}
    />
  );
}
