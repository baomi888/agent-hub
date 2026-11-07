"use client";

// 登录态的**单例**。所有 useAuth() 读的是同一份结论。
//
// 为什么必须抽出来（2026-11-07）：
//   原来 useAuth 是"每个实例各查一次 /api/auth/me"，loading 从 true 起。
//   控制台页面组有自己的 layout，首页 `/`、模板广场、/login 各自是独立的页面 ——
//   于是每次跨过 layout 边界（首页 → 设置、设置 → 首页、首页 → 模板广场），
//   新挂载的那份 useAuth 又从 loading=true 开始跑，整屏先闪一下
//   「正在确认登录状态...」再出内容。用户的原话是"老是在刷新"。
//   结论其实在上一页就有了，只是没人把它带过来。
//
// 做法：模块级快照 + useSyncExternalStore。第一次真的去探活；之后任何一次
//   客户端跳转都直接读到现成的身份，一帧都不空。
//   ★ 整页刷新（F5 / 从桌面图标冷启动）时模块会重新初始化，仍然会探活一次 ——
//     这是对的，Cookie 的有效性只有服务端说了算，不能靠页面内缓存推断。
//
// 三条写入路径，都必须经过这里，否则各页面的结论会分叉：
//   · 登录 / 注册成功（api.login 会广播 AUTHORIZED_EVENT）→ 重新探活
//   · 任何接口 401（api 层广播 UNAUTHORIZED_EVENT）→ 就地置空，不跳页
//   · 主动退出 → 置空并作废在飞的探活（否则慢回来的那次会把身份写回去）
import { AUTHORIZED_EVENT, UNAUTHORIZED_EVENT, api } from "@/lib/api";
import type { MeOut } from "@/lib/types";

export interface AuthSnapshot {
  me: MeOut | null;
  /** 首次探活未完成。跨页跳转时它恒为 false（结论已经有了） */
  loading: boolean;
  allowSignup: boolean;
}

/** 还没问过服务端时的样子。也是 SSR / 水合期必须返回的那一份 */
const UNKNOWN: AuthSnapshot = { me: null, loading: true, allowSignup: true };

let snap: AuthSnapshot = UNKNOWN;
/** 在飞的探活。只用来避免"同一次挂载里重复发请求"，不用来共享结果 */
let probing: Promise<void> | null = null;
/** 探活代数：退出登录 / 连续调用时，让后发的结果覆盖先发的 */
let gen = 0;
let wired = false;
const subs = new Set<() => void>();

function emit() {
  // 复制一份再遍历：回调里可能同步取消订阅（组件卸载）
  for (const fn of Array.from(subs)) fn();
}

function set(next: Partial<AuthSnapshot>) {
  const merged = { ...snap, ...next };
  if (
    merged.me === snap.me &&
    merged.loading === snap.loading &&
    merged.allowSignup === snap.allowSignup
  ) {
    return;
  }
  snap = merged;
  emit();
}

export function getAuthSnapshot(): AuthSnapshot {
  return snap;
}

export function getServerAuthSnapshot(): AuthSnapshot {
  return UNKNOWN;
}

function wire() {
  if (wired || typeof window === "undefined") return;
  wired = true;
  // 401：收回身份。不在这里跳登录页 —— 页面对"没有身份"的反应各不相同
  // （首页给落地页、/login 给卡片、控制台给登录门），跳转是它们的事。
  window.addEventListener(UNAUTHORIZED_EVENT, () => {
    if (snap.me === null && !snap.loading) return;
    gen++; // 作废在飞的探活：它带回来的是过期结论
    set({ me: null, loading: false });
  });
  // 登录 / 注册成功：重新探活，一次拿全（昵称、头像、配额都在 me 里）
  window.addEventListener(AUTHORIZED_EVENT, () => void refreshAuth());
}

export function subscribeAuth(fn: () => void) {
  subs.add(fn);
  wire();
  return () => {
    subs.delete(fn);
  };
}

/**
 * 重新问一次服务端。登录后、改完资料后、以及"我不确定还算不算登录"时用。
 * 每次调用都会真发请求（不复用在飞的），因为调用方要的是"此刻的结论"。
 */
export function refreshAuth(): Promise<void> {
  if (typeof window === "undefined") return Promise.resolve();
  const my = ++gen;
  const run = async () => {
    try {
      // allow_signup 是公开端点，未登录也该能读：登录卡片要靠它决定渲染不渲染注册入口
      const cfg = await api.authConfig().catch(() => null);
      if (my === gen && cfg) set({ allowSignup: cfg.allow_signup });
      const me = await api.me();
      if (my !== gen) return;
      set({ me, loading: false });
    } catch {
      if (my !== gen) return;
      set({ me: null, loading: false });
    }
  };
  const p = run();
  probing = p;
  void p.finally(() => {
    if (probing === p) probing = null;
  });
  return p;
}

/** 页面挂载时调一次：只有"还没结论"才真发请求，有结论就什么都不做 */
export function ensureAuth(): void {
  if (typeof window === "undefined") return;
  if (!snap.loading) return;
  if (probing) return;
  void refreshAuth();
}

export async function login(username: string, password: string): Promise<void> {
  await api.login(username, password);
  // api.login 已经广播过 AUTHORIZED_EVENT（另一个探活会随之起跑）；
  // 这里再等一次，是为了让 await login() 返回时身份就已经就位。
  await refreshAuth();
}

export async function register(username: string, password: string): Promise<void> {
  await api.register(username, password);
  await refreshAuth();
}

export async function logout(): Promise<void> {
  try {
    await api.logout();
  } catch {
    /* 退出失败也要清本地：Cookie 可能已经过期了 */
  }
  gen++; // 在飞的探活带回来的会是"还登录着"，作废掉
  set({ me: null, loading: false });
}
