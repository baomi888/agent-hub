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
import { useMemo, useState } from "react";
import { createPortal } from "react-dom";
import QRCode from "qrcode";
import BrandIcon from "./settings/BrandIcon";

interface Props {
  /** 授权链接；非空时显示引导层 */
  url: string | null;
  onClose: () => void;
  /** 场景说明：登录 / 绑定 / 解绑 */
  label: string;
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

export default function AlipayLaunchOverlay({ url, onClose, label }: Props) {
  // 记下"已复制"对应的链接；url 变化（下次唤起）自然失效，不需要 effect 里 setState。
  const [copiedUrl, setCopiedUrl] = useState<string | null>(null);
  const copied = Boolean(url) && copiedUrl === url;
  if (!url || typeof document === "undefined") return null;

  async function copyLink() {
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      setCopiedUrl(url);
    } catch {
      setCopiedUrl(null);
    }
  }

  return createPortal(
    <div style={dialogStyle} role="presentation" onClick={onClose}>
      <div style={cardStyle} role="dialog" aria-modal="true" aria-label={`支付宝${label}`} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: "inline-flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
          <BrandIcon brand="alipay" size={24} />
          <strong style={{ fontSize: 18 }}>支付宝{label}</strong>
        </div>
        <p style={{ fontSize: 13, lineHeight: 1.6, color: "var(--text-2, #5A4C33)", margin: "8px 0 16px" }}>
          请在支付宝 App 内完成授权。点下方按钮打开支付宝；若没有自动跳转，可用支付宝扫一扫扫描二维码。
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
