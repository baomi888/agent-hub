"use client";

/**
 * 同步状态条（B6）。
 *
 * 一行字 + 一个按钮，放在番茄钟最上面 —— 位置刻意高于所有数据：
 * 「你的数据在哪」这件事应该在看到数字之前就交代清楚，而不是藏在设置里。
 *
 * 四种状态对应四句不同的话，不合并成「同步状态：正常/异常」：
 * 用户需要知道的是「现在该怎么办」（去登录 / 重试 / 等联网），不是状态码。
 */

import Link from "next/link";

import type { SyncStatus } from "../../logic/focusSync";

interface SyncBarProps {
  status: SyncStatus;
  message: string;
  /** 上次成功同步的时刻（毫秒），null 表示还没成功过 */
  lastSyncedAt: number | null;
  onSync: () => void;
}

function agoText(at: number): string {
  const diff = Date.now() - at;
  if (diff < 60_000) return "刚刚";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`;
  return `${Math.floor(diff / 86_400_000)} 天前`;
}

export default function SyncBar({ status, message, lastSyncedAt, onSync }: SyncBarProps) {
  const busy = status === "syncing" || status === "checking";

  return (
    <div className={`campus-sync is-${status}`} aria-live="polite">
      <span className="campus-sync-dot" aria-hidden="true" />

      <div className="campus-sync-text">
        {status === "guest" ? (
          <>
            <b>仅本机</b>
            <span>
              {message}
              <Link className="campus-sync-link" href="/">
                去登录
              </Link>
            </span>
          </>
        ) : status === "synced" ? (
          <>
            <b>已同步</b>
            <span>
              {lastSyncedAt ? `${agoText(lastSyncedAt)}同步` : "已同步"} · {message}
            </span>
          </>
        ) : status === "error" ? (
          <>
            <b>未同步</b>
            <span>{message}计时不受影响，记录不会丢。</span>
          </>
        ) : (
          <>
            <b>{status === "checking" ? "检查中" : "同步中"}</b>
            <span>正在和云端核对记录…</span>
          </>
        )}
      </div>

      <button
        type="button"
        className="campus-btn is-ghost campus-sync-btn"
        onClick={onSync}
        disabled={busy}
      >
        {busy ? "稍等" : status === "guest" ? "刷新" : "立即同步"}
      </button>
    </div>
  );
}
