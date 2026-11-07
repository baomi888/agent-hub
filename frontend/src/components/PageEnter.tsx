// 切页进场动画的统一入口。
//
// 用它的地方都是各路由段的 template.tsx —— Next 里 **template 会在每次导航时
// 重新挂载**（layout 不会），所以把"淡入"放在这一层，正好得到
// 「外壳不动、内容轻轻换掉」的观感；挂在 layout 上则只会在组第一次加载时播一次。
//
// 参数在 app/styles/console.css 的 .page-enter：180ms / 上移 2px。
// 这个幅度是刻意压小的 —— 再大就成了"过场动画"，翻页面会变慢。
// 位移用 animation-fill-mode: backwards，结束后不留 transform 残值
// （残值会持续创建 containing block，坑的是 fixed 定位的浮层）。
import type { ReactNode } from "react";

export default function PageEnter({ children }: { children: ReactNode }) {
  return <div className="page-enter">{children}</div>;
}
