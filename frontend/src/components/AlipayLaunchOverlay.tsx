"use client";

// 支付宝授权跳转引导层。
//
// 为什么需要它：支付宝规定授权链接必须在支付宝 App 内打开，外部浏览器 /
// App 内置 WebView 打开会提示「请在支付宝客户端打开链接」，授权根本走不下去。
// 所以移动端不能用 window.location.assign 直接跳授权页，得先给一道引导：
//   1) 主按钮用 alipays:// 协议唤起支付宝 App（官方 scheme，appId=20000067）
//   2) 唤起失败/没装支付宝时，同一授权链接的二维码 + 复制按钮兜底
// PC 端不需要这层（点开就是扫码页），由调用方直接 window.location.assign。
//
// 这里用二维码而不是纯文字链接：支付宝「扫一扫」本就能打开授权链接，
// 是最可靠的兜底路径 —— 不依赖 scheme 唤起，也不依赖剪贴板权限。
//
// 授权在支付宝 App 内完成，回调也发生在它的内置 WebView；登录态若种在那里，
// 用户切回原应用仍是未登录。所以这层同时在后台轮询 /poll：授权一落定，
// 就用发起时签发的 state 把结果领回原应用并就地种上会话 —— 在哪里发起，
// 就在哪里登录进去。
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import QRCode from "qrcode";
import { api } from "@/lib/api";
import BrandIcon from "./settings/BrandIcon";

interface Props {
  /** 授权链接；非空时显示引导层 */
  url: string | null;
  onClose: () => void;
  /** 场景说明：登录 / 绑定 / 解绑 */
  label: string;
  /** 领回登录态后通知调用方刷新页面自己的状态（绑定/解绑要用） */
  onDone?: (action: string) => void;
}

/** 移动端（PWA / 安卓 App / 手机浏览器）走唤起引导；PC 直接跳授权页 */
export function isMobile(): boolean {
  if (typeof navigator === "undefined") return false;
  return /Android|iPhone|iPad|iPod|Mobile|Windows Phone/i.test(navigator.userAgent);
}

/** 支付宝官方唤起协议：在支付宝 App 内打开指定链接 */
function alipaysUrl(url: string): string {
  return "alipays://platformapi/startapp?appId=20000067&url=" + encodeURIComponent(url);
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
  const [copiedUrl, setCopiedUrl] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>("waiting");
  const [pollError, setPollError] = useState("");
  const doneRef = useRef(onDone);
  const closeRef = useRef(onClose);
  useEffect(() => {
    doneRef.current = onDone;
    closeRef.current = onClose;
  }, [onDone, onClose]);

  // 每 1.5 秒领一次结果，最多约 5 分钟；pending 继续，落定即停。
  // 只依赖 url：换一次授权链接（重新发起）才会重启轮询。
  useEffect(() => {
    let state = "";
    try {
      state = new URL(url).searchParams.get("state") || "";
    } catch {
      /* 授权链接异常，交给后端 poll 报错 */
    }
    if (!state) return;
    let alive = true;
    let timer = 0;
    let attempts = 0;
    const tick = async () => {
      if (!alive) return;
      attempts += 1;
      try {
        const result = await api.alipay.poll(state);
        if (!alive) return;
        if (result.status === "ok") {
          setPhase("ok");
          doneRef.current?.(result.action || "login");
          // 登录态已就地生效，稍候自动收起弹窗回到原界面。
          timer = window.setTimeout(() => {
            if (alive) closeRef.current();
          }, 900);
          return;
        }
        if (result.status === "failed") {
          setPhase("failed");
          setPollError(FAIL_TEXT[result.reason || ""] || "授权未完成，请重新发起");
          return;
        }
      } catch {
        // 网络抖动：下一次继续，不打断用户授权。
      }
      if (alive && attempts < 200) timer = window.setTimeout(tick, 1500);
    };
    timer = window.setTimeout(tick, 1500);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [url]);

  const copied = copiedUrl === url;

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(url);
      setCopiedUrl(url);
    } catch {
      setCopiedUrl(null);
    }
  }

  if (phase !== "waiting") {
    const ok = phase === "ok";
    return createPortal(
      <div style={dialogStyle} role="presentation">
        <div style={cardStyle} role="dialog" aria-modal="true" aria-label={`支付宝${label}结果`}>
          <div style={{ display: "inline-flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
            <BrandIcon brand="alipay" size={24} />
            <strong style={{ fontSize: 18 }}>{ok ? "授权成功" : "授权未完成"}</strong>
          </div>
          <p style={{ fontSize: 14, lineHeight: 1.6, color: "var(--text-2, #5A4C33)", margin: "14px 0" }}>
            {ok ? "正在返回，请稍候…" : pollError}
          </p>
          {!ok && (
            <button type="button" className="btn btn-ghost btn-sm" style={{ width: "100%", minHeight: 40 }} onClick={onClose}>
              关闭
            </button>
          )}
        </div>
      </div>,
      document.body,
    );
  }

  return createPortal(
    <div style={dialogStyle} role="presentation" onClick={onClose}>
      <div style={cardStyle} role="dialog" aria-modal="true" aria-label={`支付宝${label}`} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: "inline-flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
          <BrandIcon brand="alipay" size={24} />
          <strong style={{ fontSize: 18 }}>支付宝{label}</strong>
        </div>
        <p style={{ fontSize: 13, lineHeight: 1.6, color: "var(--text-2, #5A4C33)", margin: "8px 0 16px" }}>
          请在支付宝 App 内完成授权。点下方按钮打开支付宝；若没有自动跳转，可用支付宝扫一扫扫描二维码。授权完成后会自动返回本页面。
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
