// 「这个接口可能还没上线」的加载状态。
//
// 四屏里的 P4/P5/P7 依赖队友分支 `feat/ai-module-20261013` 新增的端点，
// 当前后端跑起来是 404。api.ts 的 tryRequest 把这种情况归一成 `null`，
// 这里再把 `null` 收敛成 `absent` —— 页面据此渲染「接口待上线」而不是报错。
//
// 区分三种失败是刻意的：
//   absent = 端点不存在（后端的事，页面显示一块说明就行）
//   error  = 真的出错了（该给用户重试入口）
// 混在一起就会出现「后端没做」和「后端挂了」长一个样子的经典问题。
export type ProbeState<T> =
  | { state: "loading" }
  | { state: "ready"; data: T }
  | { state: "absent" }
  | { state: "error"; message: string };

export function toProbe<T>(data: T | null): ProbeState<T> {
  return data === null ? { state: "absent" } : { state: "ready", data };
}

export function errProbe(e: unknown): ProbeState<never> {
  return { state: "error", message: e instanceof Error ? e.message : "加载失败" };
}

/** 把一串 ProbeState 合成一个：任何一个还在 loading 就是 loading */
export function anyLoading(...list: ProbeState<unknown>[]): boolean {
  return list.some((p) => p.state === "loading");
}
