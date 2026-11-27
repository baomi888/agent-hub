"use client";

// 支付宝移动端授权：点一下就进支付宝，不再让用户点第二次。
//
// 为什么必须唤起：支付宝规定授权链接要在支付宝 App 内打开，外部浏览器 /
// WebView 打开只会提示「请在支付宝客户端打开链接」，授权走不下去。官方给的
// 办法是 alipays:// 协议（appId=20000067 = 支付宝内打开网页）。
//
// 为什么还要这一层（而不是直接 location.assign 完事）：
//   1) 唤起可能失败（没装支付宝、被浏览器拦、iOS 在手势之外触发会被静默丢弃），
//      需要二维码兜底，否则用户点了按钮就再无下文；
//   2) 授权在支付宝内置 WebView 里完成，登录态若种在那里，切回原应用仍是未登录。
//      所以要在这里轮询 /poll，把结果领回原应用就地种会话 —— 在哪点就在哪用。
//
// 交互约定：正常路径不出任何弹窗，只有一句「正在打开支付宝…」的轻提示；
// 只有 1.7 秒后页面还没被切走（说明唤起没成功）才升级成带二维码的兜底卡片。
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import QRCode from "qrcode";
import { api } from "@/lib/api";
import BrandIcon from "./settings/BrandIcon";

interface Props {
  /** 授权链接；非空时接管唤起与轮询 */
  url: string | null;
  onClose: () => void;
  /** 场景说明：登录 / 绑定 / 解绑 */
  label: string;
  /** 领回登录态后通知调用方刷新页面自己的状态（绑定/解绑要用） */
  onDone?: (action: string) => void;
}

/** 移动端（PWA / 安卓 App / 手机浏览器）走唤起；PC 直接跳授权页 */
export function isMobile(): boolean {
  if (typeof navigator === "undefined") return false;
  return /Android|iPhone|iPad|iPod|Mobile|Windows Phone/i.test(navigator.userAgent);
}

/**
 * 发起端形态，随 start 一起交给后端，决定授权回调那张落地页怎么把用户送回来：
 *   apk —— 原生装机包。壳在 WebView 里注入了 `__zhisuiApp`，且 AndroidManifest
 *          注册了 `zhisui` scheme + MainActivity singleTask，所以回调页跳
 *          `zhisui://` 能把 App 拉回前台、复用原来的 Activity（页面不重建）。
 *   pwa —— 桌面快捷方式或手机浏览器，没有自有 scheme，只能 intent + closeWebview。
 * 只影响落地页的出口，不参与任何安全判断。
 */
export function appTargetOf(): "apk" | "pwa" {
  if (typeof window === "undefined") return "pwa";
  return (window as unknown as { __zhisuiApp?: boolean }).__zhisuiApp === true ? "apk" : "pwa";
}

/** 支付宝官方唤起协议：在支付宝 App 内打开指定链接 */
function alipaysUrl(url: string): string {
  return "alipays://platformapi/startapp?appId=20000067&url=" + encodeURIComponent(url);
}

/**
 * 立刻唤起支付宝。要在点击处理器（用户手势栈）里调用，iOS 才肯放行自定义 scheme。
 * 拿授权链接得先请求后端，await 之后手势已失效，所以调用方拿到链接后要马上调它，
 * 剩下的重试与兜底交给 AlipayLaunchOverlay。
 */
export function launchAlipay(url: string) {
  try {
    window.location.href = alipaysUrl(url);
  } catch {
    /* 唤起失败由引导层兜底，这里不打断流程 */
  }
}

/** 二维码矩阵 → SVG rect 列表（白底深码，兼容支付宝扫一扫） */
function QrCode({ url, size = 176 }: { url: string; size?: number }) {
  const rects = useMemo(() => {
    const qr = QRCode.create(url, { errorCorrectionLevel: "M" });
    const n = qr.modules.size;
    const cell = size / n;
    const out: React.ReactNode[] = [];
    for (let row = 0; row < n; row++) {
      for (let col = 0; col < n; col++) {
        if (!qr.modules.get(row, col)) continue;
        out.push(
          <rect
            key={`${row}-${col}`}
            x={(col * cell).toFixed(2)}
            y={(row * cell).toFixed(2)}
            width={cell.toFixed(2)}
            height={cell.toFixed(2)}
          />,
        );
      }
    }
    return out;
  }, [url, size]);
  return (
    <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size} role="img" aria-label="支付宝授权二维码">
      <rect x="0" y="0" width={size} height={size} fill="#fff" />
      <g fill="#1a1a1a">{rects}</g>
    </svg>
  );
}

/** 唤起等待中：一句轻提示，不拦截点击、不遮挡页面 */
const hintStyle: React.CSSProperties = {
  position: "fixed",
  left: "50%",
  bottom: "calc(72px + env(safe-area-inset-bottom, 0px))",
  transform: "translateX(-50%)",
  zIndex: 1000,
  display: "inline-flex",
  alignItems: "center",
  gap: 10,
  padding: "10px 18px",
  borderRadius: 999,
  background: "rgba(28,24,16,0.86)",
  color: "#F6F0DC",
  fontSize: 13,
  lineHeight: 1.4,
  whiteSpace: "nowrap",
  pointerEvents: "none",
  boxShadow: "0 8px 24px rgba(0,0,0,0.22)",
};
const spinStyle: React.CSSProperties = {
  width: 14,
  height: 14,
  borderRadius: "50%",
  border: "2px solid rgba(246,240,220,0.32)",
  borderTopColor: "#F6F0DC",
  animation: "zs-oauth-spin 0.9s linear infinite",
};
/** 提示条上的出口：能取消，但只有这么一小块可点 */
const hintCancelStyle: React.CSSProperties = {
  pointerEvents: "auto",
  margin: 0,
  padding: "2px 0 2px 6px",
  border: 0,
  background: "transparent",
  color: "inherit",
  font: "inherit",
  fontSize: 12,
  opacity: 0.72,
  textDecoration: "underline",
  cursor: "pointer",
};

const dialogStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  zIndex: 1000,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  padding: 20,
  background: "rgba(0,0,0,0.45)",
};
const cardStyle: React.CSSProperties = {
  width: "100%",
  maxWidth: 360,
  maxHeight: "calc(100vh - 40px)",
  overflowY: "auto",
  borderRadius: 16,
  padding: 22,
  background: "var(--paper, #FFFDF6)",
  color: "var(--ink, #3D2F1A)",
  textAlign: "center",
  boxShadow: "0 16px 48px rgba(0,0,0,0.28)",
};

const FAIL_TEXT: Record<string, string> = {
  provider_failed: "授权未完成或被取消，请重新发起",
  flow_invalid: "授权流程已失效，请重新发起",
  account_unavailable: "账号不可用，请联系管理员",
  rate_limited: "操作过于频繁，请稍后再试",
  service_unavailable: "服务暂时不可用，请稍后重试",
  not_configured: "支付宝登录尚未配置完成",
};

type Phase = "waiting" | "ok" | "failed";

interface CardProps {
  url: string;
  onClose: () => void;
  label: string;
  onDone?: (action: string) => void;
}

function LaunchCard({ url, onClose, label, onDone }: CardProps) {
  const [phase, setPhase] = useState<Phase>("waiting");
  const [fallback, setFallback] = useState(false);
  const [returned, setReturned] = useState(false);
  const [failText, setFailText] = useState("");
  const [copiedUrl, setCopiedUrl] = useState<string | null>(null);
  const doneRef = useRef(onDone);
  const closeRef = useRef(onClose);
  useEffect(() => {
    doneRef.current = onDone;
    closeRef.current = onClose;
  }, [onDone, onClose]);

  const state = useMemo(() => {
    try {
      return new URL(url).searchParams.get("state") || "";
    } catch {
      return "";
    }
  }, [url]);

  // 唤起：调用方在点击时已经试过一次（手势内才行）。这里补一次重试，
  // 并判断「有没有真的切走」——没切走才出兜底，正常路径全程无弹窗。
  useEffect(() => {
    if (typeof document === "undefined") return;
    let left = document.visibilityState === "hidden";
    const onVis = () => {
      if (document.visibilityState === "hidden") left = true;
      // 切回来时说明授权已经走完，提示语改成「正在确认」，别让人以为还在跳转。
      else setReturned(true);
    };
    document.addEventListener("visibilitychange", onVis);
    const retry = window.setTimeout(() => {
      if (!left && document.visibilityState === "visible") launchAlipay(url);
    }, 700);
    const giveUp = window.setTimeout(() => {
      if (!left && document.visibilityState === "visible") setFallback(true);
    }, 1700);
    return () => {
      document.removeEventListener("visibilitychange", onVis);
      window.clearTimeout(retry);
      window.clearTimeout(giveUp);
    };
  }, [url]);

  // 轮询领回结果：切回应用（visibilitychange / focus）时立刻领一次，
  // 不必再等下一个轮询周期。pending 继续，落定即停。
  useEffect(() => {
    if (!state) return;
    let alive = true;
    let attempts = 0;
    let inFlight = false;
    let timer = 0;
    const stop = () => {
      if (timer) {
        window.clearTimeout(timer);
        timer = 0;
      }
    };
    const tick = async () => {
      if (!alive || inFlight) return;
      inFlight = true;
      attempts += 1;
      try {
        const result = await api.alipay.poll(state);
        if (!alive) return;
        if (result.status === "ok") {
          setPhase("ok");
          doneRef.current?.(result.action || "login");
          // 登录态已就地生效，收个尾就把界面还给用户。
          timer = window.setTimeout(() => {
            if (alive) closeRef.current();
          }, 700);
          return;
        }
        if (result.status === "failed") {
          setPhase("failed");
          setFailText(FAIL_TEXT[result.reason || ""] || "授权未完成，请重新发起");
          return;
        }
      } catch {
        // 网络抖动：下一轮继续，不打断用户授权。
      } finally {
        inFlight = false;
      }
      if (alive && attempts < 300) timer = window.setTimeout(tick, 1200);
    };
    const wake = () => {
      if (!alive || inFlight || document.visibilityState !== "visible") return;
      stop();
      void tick();
    };
    // 原生壳（APK）收到 zhisui:// 深链后会 dispatch 这个事件 —— 它比
    // visibilitychange 更早也更可靠：有些机型从支付宝切回来不派发可见性变化，
    // 而深链是确定的「授权走完了」。PWA/浏览器里这个事件永远不会出现。
    // visibilitychange 要等可见性真的翻过来才领；深链是确定信号，不等。
    const onDeepLink = () => {
      if (!alive || inFlight) return;
      stop();
      void tick();
    };
    window.addEventListener("zhisui:oauth-return", onDeepLink);
    document.addEventListener("visibilitychange", wake);
    window.addEventListener("focus", wake);
    timer = window.setTimeout(tick, 700);
    return () => {
      alive = false;
      stop();
      window.removeEventListener("zhisui:oauth-return", onDeepLink);
      document.removeEventListener("visibilitychange", wake);
      window.removeEventListener("focus", wake);
    };
  }, [state]);

  const copied = copiedUrl === url;

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(url);
      setCopiedUrl(url);
    } catch {
      setCopiedUrl(null);
    }
  }

  if (phase === "ok") {
    return createPortal(
      <div style={dialogStyle} role="presentation">
        <div style={cardStyle} role="dialog" aria-modal="true" aria-label={`支付宝${label}结果`}>
          <div style={{ display: "inline-flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
            <BrandIcon brand="alipay" size={24} />
            <strong style={{ fontSize: 18 }}>授权成功</strong>
          </div>
          <p style={{ fontSize: 14, lineHeight: 1.6, color: "var(--text-2, #5A4C33)", margin: "14px 0 0" }}>
            正在进入…
          </p>
        </div>
      </div>,
      document.body,
    );
  }

  if (phase === "failed") {
    return createPortal(
      <div style={dialogStyle} role="presentation">
        <div style={cardStyle} role="dialog" aria-modal="true" aria-label={`支付宝${label}结果`}>
          <div style={{ display: "inline-flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
            <BrandIcon brand="alipay" size={24} />
            <strong style={{ fontSize: 18 }}>授权未完成</strong>
          </div>
          <p style={{ fontSize: 14, lineHeight: 1.6, color: "var(--text-2, #5A4C33)", margin: "14px 0" }}>
            {failText}
          </p>
          <button type="button" className="btn btn-ghost btn-sm" style={{ width: "100%", minHeight: 40 }} onClick={onClose}>
            关闭
          </button>
        </div>
      </div>,
      document.body,
    );
  }

  if (!fallback) {
    // 正常路径：只在角落给一句提示，不出弹窗、不拦点击。
    return createPortal(
      <div style={hintStyle} role="status" aria-live="polite">
        <span style={spinStyle} />
        <span>{returned ? "正在确认授权…" : "正在打开支付宝…"}</span>
        <button type="button" style={hintCancelStyle} onClick={onClose}>
          取消
        </button>
        <style>{"@keyframes zs-oauth-spin{to{transform:rotate(360deg)}}"}</style>
      </div>,
      document.body,
    );
  }

  // 唤起没成功（没装支付宝 / 被浏览器拦）：二维码 + 复制兜底。
  return createPortal(
    <div style={dialogStyle} role="presentation" onClick={onClose}>
      <div style={cardStyle} role="dialog" aria-modal="true" aria-label={`支付宝${label}`} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: "inline-flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
          <BrandIcon brand="alipay" size={24} />
          <strong style={{ fontSize: 18 }}>用支付宝继续{label}</strong>
        </div>
        <p style={{ fontSize: 13, lineHeight: 1.6, color: "var(--text-2, #5A4C33)", margin: "8px 0 16px" }}>
          没能自动打开支付宝。点下方按钮再试一次，或用支付宝扫一扫下面的二维码。完成后会自动回到这里。
        </p>
        <a
          href={alipaysUrl(url)}
          className="btn btn-primary"
          style={{ display: "block", width: "100%", minHeight: 44, lineHeight: "44px", textDecoration: "none" }}
        >
          打开支付宝
        </a>
        <div style={{ margin: "18px 0 10px" }}>
          <div style={{ display: "inline-block", padding: 8, background: "#fff", borderRadius: 10 }}>
            <QrCode url={url} />
          </div>
          <p style={{ fontSize: 12, color: "var(--text-3, #8A7C63)", margin: "8px 0 0" }}>支付宝扫一扫 · 或长按识别</p>
        </div>
        <button type="button" className="btn btn-ghost btn-sm" style={{ width: "100%", minHeight: 40 }} onClick={() => void copyLink()}>
          {copied ? "已复制，请粘贴到支付宝打开" : "复制授权链接"}
        </button>
        <button type="button" className="btn btn-ghost btn-sm" style={{ width: "100%", minHeight: 40, marginTop: 8 }} onClick={onClose}>
          取消
        </button>
      </div>
    </div>,
    document.body,
  );
}

export default function AlipayLaunchOverlay({ url, onClose, label, onDone }: Props) {
  if (!url || typeof document === "undefined") return null;
  // key=url：每次重新发起授权（新链接）都重置轮询与状态。
  return <LaunchCard key={url} url={url} onClose={onClose} label={label} onDone={onDone} />;
}
