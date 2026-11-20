"use client";

// 登录页的邮箱验证码方式：发码 → 输码 → 验证码换会话。
// 交互与 RegistrationEmailFields 同款（发码按钮 60s 倒计时、验证码只收数字），
// 但这里验证即登录 —— 不需要"先验出 grant 再提交"的两段式。
// 自带提交按钮：AuthGate 的 submit 收集的是登录名/密码，邮箱路径的状态
// 全在这组件里，拆出去反而要为它把 AuthGate 的表单改成判别联合。
// 直接走 authStore：不想让 AuthGate 的 onLogin 回调形状波及三个调用页。
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { loginByEmail } from "@/lib/authStore";

export default function EmailCodeLoginFields() {
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [requestId, setRequestId] = useState("");
  const [ready, setReady] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [remaining, setRemaining] = useState(0);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    api.authConfig().then(config => { if (active) setReady(config.email_ready); })
      .catch(() => { if (active) setReady(false); });
    const timer = window.setInterval(() => setRemaining(value => Math.max(0, value - 1)), 1000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  async function send() {
    if (busy) return;
    setBusy(true); setError("");
    try {
      const result = await api.loginEmail.send(email.trim());
      setRequestId(result.request_id); setCode("");
      setRemaining(result.resend_after);
    } catch (e) { setError(e instanceof Error ? e.message : "发送失败，请稍后重试"); }
    finally { setBusy(false); }
  }

  async function submit() {
    if (busy) return;
    setBusy(true); setError("");
    try {
      await loginByEmail(requestId, code);
      // 成功：authStore 已就位，探活翻绿后登录门自动放行
    } catch (e) { setError(e instanceof Error ? e.message : "登录失败，请重试"); }
    finally { setBusy(false); }
  }

  return <fieldset disabled={busy} style={{ border: 0, padding: 0, margin: 0, minWidth: 0, display: "grid", gap: 12 }}>
    <label className="auth-label"><span>安全邮箱</span>
      <input className="auth-input" type="email" autoComplete="email" required maxLength={254} value={email}
        onChange={event => {
          setEmail(event.target.value); setCode(""); setRequestId(""); setError("");
        }} />
    </label>
    <button type="button" className="btn btn-ghost btn-sm" disabled={!ready || busy || remaining > 0 || !email.trim()}
      onClick={() => void send()}>{remaining > 0 ? remaining + " 秒后可重发" : "发送登录验证码"}</button>
    {ready === false && <p role="status" className="hint">邮箱验证码登录暂不可用，请改用密码登录。</p>}
    {requestId && <>
      <label className="auth-label"><span>登录验证码</span>
        <input className="auth-input" inputMode="numeric" autoComplete="one-time-code" maxLength={6}
          value={code} onChange={event => setCode(event.target.value.replace(/[^0-9]/g, ""))} />
      </label>
      <p role="status" className="hint">验证码 10 分钟内有效；发送后请留意收件箱。</p>
    </>}
    {error && <p role="alert" className="text-fs-sm text-red-ink">{error}</p>}
    <button type="button" disabled={busy || !requestId || code.length !== 6}
      className="btn-new auth-submit disabled:opacity-60"
      onClick={() => void submit()}>{busy ? "请稍候..." : "登录"}</button>
  </fieldset>;
}
