/**
 * 弹窗打开时锁住页面滚动。
 *
 * 为什么不是 `document.body.style.overflow = "hidden"`：
 * 板块页的滚动发生在外层 `.campus-scroll` 上，而 html/body 本来就是 `overflow: hidden`
 * （主站工作台是固定视口外壳），对 body 做手脚根本拦不住 `.campus-scroll` 继续滚。
 * 所以改成给 body 打一个类，由 CSS 收口：
 *
 *   body.campus-modal-open .campus-scroll { overflow: hidden }
 *
 * 计数是为了嵌套：弹窗里再叠一层时，先关的那个不能把锁提前解开。
 * 目前只有「本轮完成」提醒用得到（最近记录 / 专注统计已改成页面内展开，不锁滚动）。
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
