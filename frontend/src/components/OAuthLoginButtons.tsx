"use client";

// 第三方登录区：支付宝 / 微信扫码。
//
// 为什么要一个统一组件而不是各写一个按钮：分隔线只能有一条，且只在实际有
// 平台启用时才出现。微信个人主体未认证时 enabled=false，这时支付宝按钮独占
// 整行；将来微信开通后自动变两列，不用回来改布局。
// 首次扫码会自动建站账号（后端 external_accounts.complete），已有账号请
// 先登录后到设置页手动绑定 —— 这两句话直接写在按钮下方，省得用户猜。
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import AlipayLaunchOverlay, { isMobile, launchAlipay } from "./AlipayLaunchOverlay";

type Provider = "alipay" | "wechat";

function AlipayIcon() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
      <rect x="1" y="1" width="22" height="22" rx="5" fill="#1677FF" />
      <text x="12" y="16.6" textAnchor="middle" fontSize="13" fontWeight="600" fill="#fff">支</text>
    </svg>
  );
}

function WeChatIcon() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
      <rect x="1" y="1" width="22" height="22" rx="5" fill="#07C160" />
      <path
        d="M9.4 7.3c-2.9 0-5.2 1.7-5.2 3.9 0 1.3.8 2.4 2 3.2l-.5 1.6 1.9-.9c.5.1 1.1.2 1.7.2h.4a3.6 3.6 0 0 1-.2-1.1c0-2.3 2.2-4.1 4.9-4.1h.3c-.6-1.7-2.5-2.8-5.3-2.8z"
        fill="#fff"
      />
      <path
        d="M19.8 13c0-1.8-1.8-3.2-4-3.2s-4 1.4-4 3.2 1.8 3.2 4 3.2c.5 0 1-.1 1.4-.2l1.5.8-.4-1.3c.9-.6 1.5-1.4 1.5-2.5z"
        fill="#fff"
      />
    </svg>
  );
}

export default function OAuthLoginButtons() {
  const [enabled, setEnabled] = useState<Provider[]>([]);
  const [busy, setBusy] = useState<Provider | null>(null);
  const [error, setError] = useState("");
  const [launchUrl, setLaunchUrl] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api.authConfig()
      .then((config) => {
        if (!alive) return;
        const list: Provider[] = [];
        if (config.alipay_enabled) list.push("alipay");
        if (config.wechat_enabled) list.push("wechat");
        setEnabled(list);
        const params = new URLSearchParams(window.location.search);
        if (params.get("alipay_result") === "failed" || params.get("wechat_result") === "failed") {
          setError("第三方登录未完成，请重试。已有账号可先登录，再在设置中绑定。");
        }
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  async function start(provider: Provider) {
    setBusy(provider);
    setError("");
    try {
      const mobile = isMobile();
      const result = provider === "alipay" ? await api.alipay.start("login", mobile) : await api.wechat.start("login", mobile);
      // 移动端支付宝：授权链接必须在支付宝 App 内打开，点一下就直接唤起，
      // 只有唤起失败才由引导层给二维码兜底。
      if (provider === "alipay" && mobile) {
        setLaunchUrl(result.authorize_url);
        launchAlipay(result.authorize_url);
        setBusy(null);
      } else {
        window.location.assign(result.authorize_url);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "暂时无法发起授权登录");
      setBusy(null);
    }
  }

  if (enabled.length === 0) return null;

  const label = (p: Provider) => (p === "alipay" ? "支付宝登录" : "微信登录");

  return (
    <div className="auth-oauth">
      <div className="auth-oauth-divider">
        <span>或使用以下方式</span>
      </div>
      <div className="auth-oauth-row">
        {enabled.map((p) => (
          <button
            key={p}
            type="button"
            className="auth-oauth-btn"
            disabled={busy !== null}
            onClick={() => void start(p)}
          >
            {p === "alipay" ? <AlipayIcon /> : <WeChatIcon />}
            {busy === p ? "正在打开授权…" : label(p)}
          </button>
        ))}
      </div>
      {error && (
        <p className="acct-err" role="alert" style={{ textAlign: "center" }}>
          {error}
        </p>
      )}
      <AlipayLaunchOverlay url={launchUrl} onClose={() => setLaunchUrl(null)} label="登录" />
    </div>
  );
}
