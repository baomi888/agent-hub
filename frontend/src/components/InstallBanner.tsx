"use client";

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { useAuth } from "@/lib/hooks/useAuth";
import {
  detectPlatform,
  installSteps,
  isInstalled,
  promptInstall,
  subscribePwa,
  type Platform,
} from "@/lib/pwa";

/* 首次进入时提示一次「装到桌面」。
 *
 * 设计取舍：
 *   - 只做手机（iOS / 安卓）。桌面浏览器地址栏右边本来就有安装入口，再弹一条会烦人。
 *   - 只做已登录之后：登录页上弹「装到桌面」很奇怪，用户还没决定要不要用。
 *   - 每条会话最多提一次（localStorage 60 天冷却）：用户可能划走没看见，
 *     但反复弹更讨厌；没装的人可以去设置页的固定入口。
 *   - 挂在页面顶部而不是底部：手机聊天页底部是输入框，浮层压上去没法打字。
 */

const STORE_KEY = "baomi.installHintAt";
const COOLDOWN = 60 * 24 * 3600 * 1000;

/* 本次会话是否已经决定要显示。
 * 原因：显示之后要往 localStorage 记时间戳（不然下次刷新又弹），
 * 而一旦记进去，getSnapshot 再读就会返回 false —— 界面刚出来就自己消失了。
 * 用这个模块级标记把"本次会话已决定显示"钉住，刷新页面才会重新判断。 */
let decidedThisSession = false;

function readLastShown(): number {
  try {
    return Number(window.localStorage.getItem(STORE_KEY) || 0);
  } catch {
    return 0; // 隐私模式读不到，当作没提示过
  }
}

export default function InstallBanner() {
  const [dismissed, setDismissed] = useState(false);

  const eligible = useSyncExternalStore(
    subscribePwa,
    () => {
      if (dismissed) return false;
      // 本次会话已决定显示 → 保持显示（记账写进 localStorage 后不能让自己消失）
      if (decidedThisSession) return true;
      if (isInstalled()) return false;
      const platform = detectPlatform();
      if (platform !== "ios" && platform !== "android") return false;
      const last = readLastShown();
      return !(last && Date.now() - last < COOLDOWN);
    },
    () => false
  );

  // 真的露出来了才记账。只写存储、不改 state，不会触发级联渲染。
  useEffect(() => {
    if (!eligible) return;
    decidedThisSession = true;
    try {
      window.localStorage.setItem(STORE_KEY, String(Date.now()));
    } catch {
      /* 存不了就只提示这一次 */
    }
  }, [eligible]);

  const close = useCallback(() => setDismissed(true), []);

  if (!eligible || dismissed) return null;
  return <Banner onClose={close} />;
}

/** 参数固定，避免每次渲染新建函数导致 useSyncExternalStore 反复重订阅 */
const getPlatform = (): Platform => detectPlatform();
const getCanPrompt = (): boolean => !!window.__baomiInstallPrompt;

function Banner({ onClose }: { onClose: () => void }) {
  const auth = useAuth();
  const platform = useSyncExternalStore(subscribePwa, getPlatform, () => "other" as Platform);
  const canPrompt = useSyncExternalStore(subscribePwa, getCanPrompt, () => false);
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    window.addEventListener("baomi:installed", onClose);
    return () => window.removeEventListener("baomi:installed", onClose);
  }, [onClose]);

  // 登录页不弹：这时用户还没进系统
  if (auth.loading || !auth.me) return null;

  const info = installSteps(platform);

  const onAction = async () => {
    if (!canPrompt) {
      setExpanded((v) => !v);
      return;
    }
    setBusy(true);
    const ok = await promptInstall();
    setBusy(false);
    if (ok) onClose();
    else setExpanded(true);
  };

  return (
    <div
      role="dialog"
      aria-label="把知穗装到桌面"
      style={{
        position: "fixed",
        top: 0,
        left: 0,
        right: 0,
        zIndex: 90,
        padding: "calc(8px + env(safe-area-inset-top, 0px)) 10px 0",
        pointerEvents: "none",
      }}
    >
      <div
        style={{
          pointerEvents: "auto",
          background: "var(--bg-canvas)",
          border: "1px solid var(--line)",
          borderRadius: "var(--radius-lg)",
          boxShadow: "var(--shadow-2)",
          padding: "10px 12px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/mascot.svg" alt="" width={34} height={34} style={{ flex: "none" }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: "var(--fs-lg)", fontWeight: 600, lineHeight: 1.3 }}>
              把知穗装到桌面
            </div>
            <div
              className="hint"
              style={{ margin: "2px 0 0", fontSize: "var(--fs-sm)", lineHeight: 1.4 }}
            >
              {canPrompt
                ? "装好后像 App 一样打开，不用每次找网址"
                : platform === "ios"
                  ? "像 App 一样全屏使用，不用每次输网址"
                  : "装好后在桌面点一下就能用"}
            </div>
          </div>
          <button
            type="button"
            className="btn btn-sm"
            onClick={onAction}
            disabled={busy}
            style={{ flex: "none" }}
          >
            {busy ? "安装中…" : canPrompt ? "安装" : expanded ? "收起" : "怎么装"}
          </button>
          <button
            type="button"
            onClick={onClose}
            aria-label="不再提示"
            style={{
              flex: "none",
              border: 0,
              background: "transparent",
              color: "var(--ink)",
              opacity: 0.45,
              fontSize: 18,
              lineHeight: 1,
              padding: "4px 2px 4px 6px",
              cursor: "pointer",
            }}
          >
            ×
          </button>
        </div>

        {expanded && (
          <div
            style={{
              marginTop: 10,
              paddingTop: 10,
              borderTop: "1px solid var(--line)",
            }}
          >
            <div style={{ fontSize: "var(--fs-md)", fontWeight: 600, marginBottom: 6 }}>
              {info.title}
            </div>
            <ol
              style={{
                margin: 0,
                paddingLeft: 18,
                fontSize: "var(--fs-sm)",
                lineHeight: 1.7,
                color: "var(--ink)",
              }}
            >
              {info.steps.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ol>
            {info.note && (
              <p className="hint" style={{ margin: "8px 0 0", fontSize: "var(--fs-sm)" }}>
                {info.note}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
