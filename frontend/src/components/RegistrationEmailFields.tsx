"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { RegistrationEmail } from "@/lib/types";

export default function RegistrationEmailFields({ onProof }: { onProof: (value: RegistrationEmail | null) => void }) {
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [requestId, setRequestId] = useState("");
  const [ready, setReady] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [remaining, setRemaining] = useState(0);
  const [verified, setVerified] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api.authConfig().then(config => { if (active) setReady(config.email_ready); })
      .catch(() => { if (active) setReady(false); });
    const timer = window.setInterval(() => setRemaining(value => Math.max(0,value-1)),1000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);
  async function send() {
    if (busy) return;
    setBusy(true); setError("");
    try {
      const result = await api.registrationEmail.send(email.trim());
      setRequestId(result.request_id); setCode(""); setVerified(false);
      onProof(null); setRemaining(result.resend_after);
    } catch (error) { setError(error instanceof Error ? error.message : "发送失败，请稍后重试"); }
    finally { setBusy(false); }
  }
  async function verify() {
    if (busy) return;
    setBusy(true); setError("");
    try {
      const result = await api.registrationEmail.verify(requestId,code);
      onProof({email:email.trim(),email_grant:result.grant});
      setVerified(true); setCode("");
    } catch (error) { setError(error instanceof Error ? error.message : "验证失败，请重试"); }
    finally { setBusy(false); }
  }
  return <fieldset disabled={busy} style={{border:0,padding:0,margin:0,minWidth:0,display:"grid",gap:12}}>
    <legend className="auth-label">注册邮箱（必填）</legend>
    <label className="auth-label"><span>邮箱</span>
      <input className="auth-input" type="email" autoComplete="email" required maxLength={254} value={email}
        onChange={event => {
          setEmail(event.target.value); setCode(""); setRequestId(""); setVerified(false);
          onProof(null); setError("");
        }} />
    </label>
    <button type="button" className="btn btn-ghost btn-sm" disabled={!ready || busy || remaining>0 || !email.trim()}
      onClick={() => void send()}>{remaining>0 ? remaining+" 秒后可重发" : "发送邮箱验证码"}</button>
    {ready === false && <p role="status" className="hint">邮箱验证暂不可用，请稍后再注册。已有账号可正常登录。</p>}
    {requestId && !verified && <>
      <label className="auth-label"><span>邮箱验证码</span>
        <input className="auth-input" inputMode="numeric" autoComplete="one-time-code" maxLength={6}
          value={code} onChange={event=>setCode(event.target.value.replace(/[^0-9]/g,""))} />
      </label>
      <button type="button" className="btn btn-ghost btn-sm" disabled={busy || code.length!==6}
        onClick={() => void verify()}>验证邮箱</button>
    </>}
    {verified && <p role="status" className="hint">邮箱已验证，请在 5 分钟内完成注册。</p>}
    {error && <p role="alert" className="text-fs-sm text-red-ink">{error}</p>}
  </fieldset>;
}
