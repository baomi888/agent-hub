// 切页过渡：template.tsx 在**每次导航**时重新挂载（layout 不会），
// 所以把"内容淡入"放在这里，正好得到 DeepSeek 那种「侧栏不动、内容轻轻换掉」的观感。
//
// 为什么不用 Next 的 experimental.viewTransition：它需要改 next.config 且对
// 老浏览器是整页跳变，收益不稳定；这里只是一段 180ms 的动画，
// 用户能感知到"换了一个页面"，但不会看到白屏。
//
// 注意：动画作用在包裹页面的 div 上，不写 transform 的终态残值
// （animation-fill-mode 用 backwards 而不是 both），结束后不残留 containing block。
//
// ★ 2026-11-16 P2：方向判断搬进 `components/PageTransition.tsx`
//   （切一级 tab 只淡入、进二级页从右 8px 推入）。这一层只做转引，
//   逻辑与"不留 transform 残值"的约束都写在那边的注释里。
export { default } from "@/components/PageTransition";
