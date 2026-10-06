"use client";

// 登录态：首屏确认"我是谁"，以及登录失效后把界面收回登录门。
//
// 两件事都在这里闭环：
//   1) 刷新页面靠 /api/auth/me/ 找回身份（HttpOnly Cookie，JS 读不到 token）
//   2) 任何接口返回 401 → api.ts 广播 UNAUTHORIZED_EVENT → 这里把 user 置空。
//      不这么做的话，登录过期后用户点什么都是"没反应"：请求其实失败了，
//      只是失败被埋在某个 hook 的 catch 里。
import { useCallback, useEffect, useRef, useState } from "react";
import { UNAUTHORIZED_EVENT, api } from "@/lib/api";
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
  const [me, setMe] = useState<MeOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [allowSignup, setAllowSignup] = useState(true);
  // 防止"先发的请求后回"覆盖后发的结论（StrictMode 下首屏 effect 会跑两次）
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    try {
      const data = await api.me();
      if (aliveRef.current) setMe(data);
    } catch {
      if (aliveRef.current) setMe(null);
    } finally {
      if (aliveRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    // allow_signup 单独拉：它是公开端点，未登录也能读，
    // 登录页要先知道这件事才能决定渲染不渲染"注册"入口
    api
      .authConfig()
      .then((c) => {
        if (aliveRef.current) setAllowSignup(c.allow_signup);
      })
      .catch(() => {
        /* 读不到就按"允许"兜底，注册失败时后端还会再挡一层 */
      });
    // 探活放到下一个宏任务：直接在 effect 里 setState 会被 React 编译器
    // 判成"级联渲染"（eslint react-hooks/immutability）
    const t = window.setTimeout(() => void refresh(), 0);
    return () => window.clearTimeout(t);
  }, [refresh]);

  useEffect(() => {
    const onExpired = () => setMe(null);
    window.addEventListener(UNAUTHORIZED_EVENT, onExpired);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onExpired);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    await api.login(username, password);
    await refresh();
  }, [refresh]);

  const register = useCallback(async (username: string, password: string) => {
    await api.register(username, password);
    await refresh();
  }, [refresh]);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      /* 退出失败也要清本地：Cookie 可能已经过期了 */
    }
    setMe(null);
  }, []);

  return { me, loading, allowSignup, refresh, login, register, logout };
}
