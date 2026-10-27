"use client";

/**
 * 新手引导条（L2）：首次进入时在欢迎页给一条「三步走」，可关闭。
 *
 * 为什么放在欢迎页内部而不是三栏之外：它只在**还没开始问**的时候才有意义。
 * 已经有对话的老用户看到它只是挡地方；所以它随空态一起出现、一起消失，
 * 而不是像公告条一样常驻在每个页面上。
 *
 * 为什么用 useSyncExternalStore 而不是 useEffect + setState：
 *  ① 服务端没有 localStorage，getServerSnapshot 固定返回 true（隐藏），
 *     水合时不会因为「服务端渲染了、客户端没渲染」报 mismatch；
 *  ② 本项目 eslint 启了 react-hooks/set-state-in-effect，effect 里同步
 *     setState 会直接报 error（10-24 在 QueryEnhance 上踩过）。
 *
 * 关闭状态按用户分开存（`baomi.onboard.v1.<uid>`）：同一台电脑换个人登录，
 * 新用户仍然应该看到引导，而不是继承上一个人的「已读」。
 */

import Link from "next/link";
import { useCallback, useSyncExternalStore } from "react";

const KEY = "baomi.onboard.v1";

/** 关闭状态的外部订阅源：同一个 key 的改动要能让所有实例立刻重渲染 */
const listeners = new Set<() => void>();

function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

interface Props {
  /** 当前登录用户 id；未确认身份时传 null（此时不落盘，避免写到一个"公共"key 上） */
  userId?: string | null;
  /** 已拥有的知识库数量：>0 时第 2 步显示"已有 N 个"而不是"去建一个" */
  kbCount?: number;
  /** 打开右侧资料库面板（窄屏是抽屉） */
  onOpenKb?: () => void;
  /** 把焦点送进输入框：第 3 步点一下就能直接打字 */
  onAsk?: () => void;
}

export default function OnboardBar({ userId, kbCount = 0, onOpenKb, onAsk }: Props) {
  const storageKey = userId ? `${KEY}.${userId}` : KEY;

  // 只能在客户端读：localStorage 在 SSR 期不存在
  const getSnapshot = useCallback(() => {
    try {
      return localStorage.getItem(storageKey) === "1";
    } catch {
      // 隐私模式下 localStorage 会抛异常。读不到就当"已关闭"——
      // 引导只是锦上添花，不值得为它挡住主流程。
      return true;
    }
  }, [storageKey]);

  const dismissed = useSyncExternalStore(subscribe, getSnapshot, () => true);

  const close = useCallback(() => {
    try {
      localStorage.setItem(storageKey, "1");
    } catch {
      /* 写不进去也不影响本次会话：下面的 notify 仍然让它在当前页消失 */
    }
    listeners.forEach((cb) => cb());
  }, [storageKey]);

  if (dismissed) return null;

  return (
    <div className="onboard" role="note" aria-label="新手引导">
      <div className="onboard-head">
        <span className="onboard-title">第一次来？三步就能用起来</span>
        <button type="button" className="onboard-close" onClick={close}>
          知道了
        </button>
      </div>

      <ol className="onboard-steps">
        <li>
          <span className="ob-n" aria-hidden="true">1</span>
          <Link href="/templates" className="ob-act">
            选个学习方向
          </Link>
          <span className="ob-hint">在模板广场勾几个标签，推荐会跟着变</span>
        </li>
        <li>
          <span className="ob-n" aria-hidden="true">2</span>
          {kbCount > 0 ? (
            <span className="ob-done">已有 {kbCount} 个资料库</span>
          ) : onOpenKb ? (
            <button type="button" className="ob-act" onClick={onOpenKb}>
              建一个知识库
            </button>
          ) : (
            <span className="ob-done">建一个知识库</span>
          )}
          <span className="ob-hint">把课件、PDF 传进去，回答就能翻回原文</span>
        </li>
        <li>
          <span className="ob-n" aria-hidden="true">3</span>
          {onAsk ? (
            <button type="button" className="ob-act" onClick={onAsk}>
              在下面问一句
            </button>
          ) : (
            <span className="ob-done">在下面问一句</span>
          )}
          <span className="ob-hint">不用会写提示词，想解决什么就说什么</span>
        </li>
      </ol>
    </div>
  );
}
