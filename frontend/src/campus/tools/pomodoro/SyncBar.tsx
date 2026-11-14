"use client";

/**
 * 同步状态行。
 *
 * **只在「自动同步办不到」的时候出现。** 进页面、网络恢复、页面重新可见
 * （PomodoroTool 里三处 effect）都会自动跑一次同步，所以「已同步 · 刚刚同步 ·
 * 云端共 12 条记录」这行字只是在复述一件本来就该成立的事；旁边那个「立即同步」
 * 按钮更没有用武之地 —— 用户真需要它的时候是同步失败，那时按钮该叫「重试」。
 *
 * 留下的是两种会让人做错决定的状态：
 *  - 未登录：数据只在这台设备上，换台电脑或清了浏览器就没了；
 *  - 同步失败：这次没传上去，联网后会补。
 *
 * 这两句都不是「状态」，是后果，所以按后果说：能不能换设备、记录会不会丢。
 */

import Link from "next/link";

import type { SyncStatus } from "../../logic/focusSync";

interface SyncBarProps {
  status: SyncStatus;
  message: string;
  onSync: () => void;
}

export default function SyncBar({ status, message, onSync }: SyncBarProps) {
  // 正常态不说话。checking / syncing 是几百毫秒内的瞬态，为它闪一次反而像出错。
  if (status === "synced" || status === "checking" || status === "syncing") return null;

  const guest = status === "guest";

  return (
    <div className={`campus-sync is-${status}`} aria-live="polite">
      <span className="campus-sync-dot" aria-hidden="true" />

      <div className="campus-sync-text">
        <b>{guest ? "仅本机" : "未同步"}</b>
        <span>{guest ? message : `${message}。计时不受影响，记录不会丢。`}</span>
      </div>

      {guest ? (
        <Link className="campus-btn is-ghost campus-sync-btn" href="/">
          去登录
        </Link>
      ) : (
        <button
          type="button"
          className="campus-btn is-ghost campus-sync-btn"
          onClick={onSync}
        >
          重试
        </button>
      )}
    </div>
  );
}
