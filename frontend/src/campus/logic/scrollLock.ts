/**
 * 弹窗打开时锁住页面滚动。
 *
 * 为什么不是 `document.body.style.overflow = "hidden"`：
 * 滚动发生在控制台外壳那一层 `.console-scroll` 上，而 html/body 本来就是
 * `overflow: hidden`（主站工作台是固定视口外壳），对 body 做手脚根本拦不住
 * `.console-scroll` 继续滚。所以改成给 body 打一个类，由 CSS 收口：
 *
 *   body.campus-modal-open .console-scroll { overflow: hidden }
 *
 * ★ 目标是 `.console-scroll` 而不是 `.campus-scroll`（2026-11-13 第三轮改）：
 *   工具集并进 (console) 路由组之后，这一页不再自带滚动容器。
 *   这里只负责打类，具体打到哪一层由 campus.css 那一条选择器决定 ——
 *   换外壳时只改 CSS，不用动这个文件。
 *
 * 计数是为了嵌套：弹窗里再叠一层时，先关的那个不能把锁提前解开。
 * 目前只有「本轮完成」提醒、闪卡全屏用得到（最近记录 / 专注统计已改成页面内展开）。
 */

let locks = 0;

export function lockPageScroll(): () => void {
  if (typeof document === "undefined") return () => {};

  locks += 1;
  document.body.classList.add("campus-modal-open");

  let released = false;
  return () => {
    // 同一个 release 被调两次（React 严格模式会这么干）不能把计数减穿
    if (released) return;
    released = true;
    locks = Math.max(0, locks - 1);
    if (locks === 0) document.body.classList.remove("campus-modal-open");
  };
}
