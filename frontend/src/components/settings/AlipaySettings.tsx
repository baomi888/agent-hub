"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/hooks/useAuth";
import AlipayLaunchOverlay, { isMobile } from "@/components/AlipayLaunchOverlay";
import BrandIcon from "./BrandIcon";

export default function AlipaySettings() {
  const auth = useAuth();
  const [state, setState] = useState<{ enabled: boolean; bound: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [launchUrl, setLaunchUrl] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    api.alipay.status().then(result => {
      if (!alive) return;
      setState(result);
      const outcome = new URLSearchParams(window.location.search).get("alipay_result");
      if (outcome === "failed") setError("支付宝操作未完成。请确认还有其他登录方式，并使用正确的支付宝重新操作。");
      if (outcome === "bound") setNotice("支付宝已绑定");
      if (outcome === "unlinked") setNotice("支付宝已解绑，本站账号和数据仍保留。");
    }).catch(error => { if (alive) setError(error instanceof Error ? error.message : "读取失败"); });
    return () => { alive = false; };
  }, []);

  async function start(action: "bind" | "unlink") {
    if (action === "unlink" && auth.me?.user.has_password === false) {
      setError("请先通过安全邮箱验证设置本站密码，再解绑支付宝。");
      return;
    }
    if (action === "unlink" && !window.confirm("仅解除支付宝关联，不删除本站账号或数据。请用已绑定支付宝扫码确认解绑。")) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const mobile = isMobile();
      // 移动端走 alipays 唤起：state 自携带 browser+uid，回调无本站 cookie 也能完成。
      const result = await api.alipay.start(action, mobile);
      if (mobile) {
        setLaunchUrl(result.authorize_url);
        setBusy(false);
      } else {
        window.location.assign(result.authorize_url);
      }
    } catch (error) {
      setError(error instanceof Error ? error.message : "操作失败，请重试"); setBusy(false);
    }
  }

  return <>
    <section>
    <p className="sec-label">支付宝登录</p>
    <div className="card card-pad">
      <div className="card-head"><span className="card-title" style={{ display: "inline-flex", alignItems: "center", gap: 8 }}><BrandIcon brand="alipay" />支付宝账号</span>
        <span className="meta">{state ? state.bound ? "已绑定" : "未绑定" : "读取中…"}</span></div>
      <p className="hint" style={{ marginTop: 8 }}>解绑保留本站账号和数据，必须先设置本站密码。更换支付宝时，先用原支付宝确认解绑，再绑定新支付宝。</p>
      {auth.me?.user.has_password === false && <p className="hint" style={{ marginTop: 8 }}>
        尚未设置本站密码。先绑定上方安全邮箱，再<Link className="auth-tlink" href="/reset-password">通过邮箱验证设置密码</Link>。
      </p>}
      {state?.enabled && <button type="button" className="btn btn-ghost btn-sm" style={{ marginTop: 12 }}
        disabled={busy || (state.bound && auth.me?.user.has_password === false)} onClick={() => void start(state.bound ? "unlink" : "bind")}>
        {busy ? "正在打开支付宝授权…" : state.bound ? "解绑支付宝" : "绑定支付宝"}
      </button>}
      {state && !state.enabled && <p className="hint">支付宝登录暂未开放。</p>}
      {error && <p className="acct-err" role="alert">{error}</p>}
      {notice && <p className="hint" role="status">{notice}</p>}
    </div>
    </section>
    <AlipayLaunchOverlay
      url={launchUrl}
      onClose={() => setLaunchUrl(null)}
      label={state?.bound ? "解绑" : "绑定"}
      onDone={(action) => {
        // 结果已领回、会话已就地在原应用重签：刷新绑定状态与身份。
        api.alipay.status().then(result => setState(result)).catch(() => {});
        void auth.refresh();
        const outcome = action === "unlink" ? "unlinked" : "bound";
        setNotice(outcome === "unlinked" ? "支付宝已解绑，本站账号和数据仍保留。" : "支付宝已绑定");
      }}
    />
  </>;
}
