"use client";

// 登录态：首屏确认"我是谁"，以及登录失效后把界面收回登录门。
//
// 2026-11-07 起这里只是 lib/authStore 的一层薄壳 —— 状态和请求都搬进单例了，
// 原因见那个文件的头注释（跨 layout 跳转时新挂载的实例会重新 loading，
// 整屏闪一下「正在确认登录状态」，观感就是"每次点导航都在刷新"）。
//
// 对调用方完全兼容：形状还是 { me, loading, allowSignup, refresh, login, register, logout }，
// 老的二十来个调用点一行都不用改。唯一的行为差别是好的那一面：
//   · refresh() 现在会让**所有**页面一起变（改完昵称，侧栏、设置页、工作台同帧更新）；
//   · 退出登录后其它页面立刻读到 null，不会再各自探活一遍。
import { useEffect, useSyncExternalStore } from "react";
import {
  ensureAuth,
  getAuthSnapshot,
  getServerAuthSnapshot,
  login as storeLogin,
  logout as storeLogout,
  refreshAuth,
  register as storeRegister,
  subscribeAuth,
} from "@/lib/authStore";
import type { MeOut } from "@/lib/types";

export interface AuthState {
  /** null = 未登录；undefined 的语义不存在，未登录就是 null */
  me: MeOut | null;
  /** 首屏探活中：不先确认就渲染主界面，会闪一下空会话列表再跳登录 */
  loading: boolean;
  allowSignup: boolean;
  refresh: () => Promise<void>;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

export function useAuth(): AuthState {
  const snap = useSyncExternalStore(subscribeAuth, getAuthSnapshot, getServerAuthSnapshot);

  // 只有"整页刚打开、还没有结论"时才真发请求；页面内跳转时这里是空转。
  // 放在宏任务里发，避免 effect 体里同步触发的 setState 被判成级联渲染。
  useEffect(() => {
    const t = window.setTimeout(() => ensureAuth(), 0);
    return () => window.clearTimeout(t);
  }, []);

  return {
    me: snap.me,
    loading: snap.loading,
    allowSignup: snap.allowSignup,
    refresh: refreshAuth,
    login: storeLogin,
    register: storeRegister,
    logout: storeLogout,
  };
}
