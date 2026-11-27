"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function WeChatLoginButton() {
  const [enabled, setEnabled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    api.authConfig().then(config => {
      if (!alive) return;
      setEnabled(Boolean(config.wechat_enabled));
      if (new URLSearchParams(window.location.search).get("wechat_result") === "failed") {
        setError("微信登录未完成，请重试。已有账号可先登录，再在设置中绑定微信。");
      }
    }).catch(() => {});
    return () => { alive = false; };
  }, []);

  async function start() {
    setBusy(true); setError("");
    try {
      const result = await api.wechat.start();
      window.location.assign(result.authorize_url);
    } catch (error) {
      setError(error instanceof Error ? error.message : "暂时无法发起微信登录");
      setBusy(false);
    }
  }

  return <>
    {enabled && <div style={{ marginTop: 16 }}>
      <button type="button" className="btn btn-ghost auth-submit" disabled={busy} onClick={() => void start()}>
        {busy ? "正在打开微信授权…" : "微信扫码登录 / 注册"}
      </button>
      <p className="hint" style={{ marginTop: 8 }}>首次使用会创建本站账号。已有账号请先登录后绑定微信。</p>
    </div>}
    {error && <p className="acct-err" role="alert">{error}</p>}
  </>;
}
