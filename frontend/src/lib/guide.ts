/**
 * 上手说明弹窗的"再看一次"通道。
 *
 * 为什么用自定义事件而不是把状态提上去：弹窗挂在两处（首页 ChatArea、
 * 控制台 ConsoleShell），而「再看一次」的按钮在设置页 —— 三个位置不在同一条
 * 组件链上。提状态要一路 prop 穿过好几层，或者再加一个 context，
 * 只为了传"打开一个一次性浮层"这件事。
 *
 * 事件方式还有个好处：弹窗不在场（比如某页没挂）时，dispatch 出去什么也不会发生，
 * 不会报错。
 */

export const GUIDE_OPEN_EVENT = "baomi:guide-open";

export function openGuide() {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(GUIDE_OPEN_EVENT));
}
