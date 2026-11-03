"use client";

// 统一头像组件。
//
// 为什么要有它：头像现在在三处出现（首页侧栏、设置页账号卡、选择器预览），
// 三处各写一遍"是图标？是图片？还是首字母？"必然会漂移 —— 常见表现是
// 换了头像侧栏变了、账号卡没变，或者其中一处忘了处理空值闪出一个方块。
// 这里只判断一次，谁用谁传 `avatar` + `name`。
import CornIcon from "@/components/CornIcon";

const ICON_PREFIX = "icon:";
const IMG_PREFIX = "img:";

/** 从 `icon:star` 里取出 `star`；不是内置图标时返回 null */
export function avatarIconName(avatar?: string | null): string | null {
  if (!avatar || !avatar.startsWith(ICON_PREFIX)) return null;
  const name = avatar.slice(ICON_PREFIX.length).trim();
  return name || null;
}

/**
 * 上传头像的图片地址（第 2 批用）。
 *
 * 走 /api 代理而不是直连后端：和后端所有其它请求同源，Cookie 与
 * nginx 的 location 规则都不用额外处理。文件名是内容 hash，所以可以永久缓存。
 */
export function avatarImageUrl(avatar?: string | null): string | null {
  if (!avatar || !avatar.startsWith(IMG_PREFIX)) return null;
  const hash = avatar.slice(IMG_PREFIX.length).trim();
  return hash ? `/api/auth/avatar/${encodeURIComponent(hash)}` : null;
}

/** 首字母：中文取第一个字，英文取首字母大写，其它（emoji 等）取首个码点 */
export function avatarLetter(name?: string | null): string {
  const s = (name || "").trim();
  if (!s) return "?";
  return Array.from(s)[0].toUpperCase();
}

interface Props {
  /** `""` / `icon:<name>` / `img:<hash>`，见 lib/types.ts 的 UserOut.avatar */
  avatar?: string | null;
  /** 昵称优先，其次登录名。只在没有头像时用来取首字母 */
  name?: string | null;
  /** 容器尺寸，默认 36px（账号卡用）。侧栏传 h-6 w-6 */
  className?: string;
}

/**
 * 头像。三种形态的优先级：图片 > 内置图标 > 首字母。
 *
 * 首字母是**兜底而不是默认**：新用户注册后 avatar 是空串，此时他还没挑过头像，
 * 一个带自己名字首字的圆比一个通用的"人形"图标更能让人认出"这是我"。
 */
export default function UserAvatar({ avatar, name, className = "h-9 w-9" }: Props) {
  const img = avatarImageUrl(avatar);
  const icon = avatarIconName(avatar);
  // 底色用 accent-tint（浅玉米金）而不是 fill-1（中性灰）：
  // 灰圆压在暖米黄卡片上像贴了一张灰贴纸，和整站的纸感不是一套；
  // 浅金底 + 深棕图标/字才是"这个站自己的头像"
  const shell =
    "inline-flex shrink-0 items-center justify-center overflow-hidden rounded-full " +
    "bg-accent-tint text-accent-ink ring-1 ring-line " +
    className;

  return (
    <span className={shell} aria-hidden="true">
      {img ? (
        // 头像不是内容主体，alt 留空让读屏跳过；身份由旁边的名字承担。
        // 不用 next/image：图片来自本站 /api（同源、尺寸固定 36px），
        // 走优化器只会多一层 loader 与一次磁盘读，没有收益
        // eslint-disable-next-line @next/next/no-img-element
        <img src={img} alt="" className="h-full w-full object-cover" />
      ) : icon ? (
        // 图标留 12% 内边距：CornIcon 的描边会溢出 viewBox，贴边会被裁掉笔锋
        <CornIcon name={icon} className="h-full w-full p-[12%]" />
      ) : (
        <span className="select-none text-[0.95em] font-semibold leading-none">
          {avatarLetter(name)}
        </span>
      )}
    </span>
  );
}
