"use client";

/**
 * 「这条提示用户已经关掉了」的共享状态，按 key 落在 localStorage。
 *
 * 为什么用 useSyncExternalStore 而不是 useEffect + setState：
 *  ① 服务端没有 localStorage —— getServerSnapshot 固定返回 true（当作已关闭），
 *     水合时不会出现「服务端渲染了、客户端没渲染」的 mismatch；
 *  ② 本项目 eslint 开了 react-hooks/set-state-in-effect，effect 里同步 setState
 *     直接报 error（10-24 在 QueryEnhance、10-28 在单词卡上都绕过一次）。
 *     外部 store 是这条规则认可的正路。
 *  ③ 同一 key 有多个实例时（比如页头按钮 + 正文卡片要同时变化），listeners
 *     让它们一起重渲染，不用把状态提到共同的父组件里。
 *
 * 用法：
 *   const [dismissed, dismiss, restore] = useDismissed(`baomi.path.intro.${uid}`);
 *   if (dismissed) return null;
 *   // dismiss() 关掉并记住；restore() 重新打开（给"再看一次"的入口用）
 *
 * ⚠️ key 里要带 user id：同一台电脑换个人登录，新用户应该重新看到提示，
 *    而不是继承上一个人的「已读」。
 */

import { useCallback, useSyncExternalStore } from "react";

const listeners = new Set<() => void>();

function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

const SERVER_VALUE = true;

export function useDismissed(key: string): [boolean, () => void, () => void] {
  const getSnapshot = useCallback(() => {
    try {
      return localStorage.getItem(key) === "1";
    } catch {
      // 隐私模式 / 存储被禁用时 localStorage 会抛。读不到就当"已关闭"：
      // 这些提示都是锦上添花，不值得为它挡住主流程。
      return true;
    }
  }, [key]);

  const dismissed = useSyncExternalStore(subscribe, getSnapshot, () => SERVER_VALUE);

  const dismiss = useCallback(() => {
    try {
      localStorage.setItem(key, "1");
    } catch {
      /* 写不进去也不影响当前页：下面的 notify 仍然让它立刻消失 */
    }
    listeners.forEach((cb) => cb());
  }, [key]);

  /** 重新打开：清掉标记（给"再看一次说明"这类入口用，不需要额外的本地 state） */
  const restore = useCallback(() => {
    try {
      localStorage.removeItem(key);
    } catch {
      /* 同上 */
    }
    listeners.forEach((cb) => cb());
  }, [key]);

  return [dismissed, dismiss, restore];
}

export default useDismissed;
