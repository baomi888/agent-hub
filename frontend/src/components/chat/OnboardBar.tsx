"use client";

/**
 * 新手引导条（L2）：首次进入时在欢迎页给一条「三步走」，可关闭。
 *
 * 为什么放在欢迎页内部而不是三栏之外：它只在**还没开始问**的时候才有意义。
 * 已经有对话的老用户看到它只是挡地方；所以它随空态一起出现、一起消失，
 * 而不是像公告条一样常驻在每个页面上。
 *
 * 关闭状态按用户分开存（`baomi.onboard.v1.<uid>`）：同一台电脑换个人登录，
 * 新用户仍然应该看到引导，而不是继承上一个人的「已读」。
 * 存取逻辑抽在 lib/hooks/useDismissed（/path 的「这条路是什么」用的是同一个）。
 */

import Link from "next/link";
import { useDismissed } from "@/lib/hooks/useDismissed";

const KEY = "baomi.onboard.v1";

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
  const [dismissed, dismiss] = useDismissed(userId ? `${KEY}.${userId}` : KEY);

  if (dismissed) return null;

  return (
    <div className="onboard" role="note" aria-label="新手引导">
      <div className="onboard-head">
        <span className="onboard-title">第一次来？三步就能用起来</span>
        <button type="button" className="onboard-close" onClick={dismiss}>
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
