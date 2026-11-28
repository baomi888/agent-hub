"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import DialogShell from "./DialogShell";
import BrandIcon from "./BrandIcon";

type State = Awaited<ReturnType<typeof api.securityEmail.state>>;
type Purpose = Parameters<typeof api.securityEmail.send>[0];
const message = (error: unknown) => error instanceof Error ? error.message : "操作失败，请稍后重试";

function VerifyEmail({ label, purpose, flow, fixed, onVerified }: {
  label: string; purpose: Purpose; flow: string; fixed?: string;
  onVerified: (grant: string) => void;
}) {
  const [address, setAddress] = useState("");
  const [code, setCode] = useState("");
  const [request, setRequest] = useState("");
  const [verified, setVerified] = useState(false);
  const [busy, setBusy] = useState(false);
  const [remaining, setRemaining] = useState(0);
  const [error, setError] = useState("");
  useEffect(() => {
    const timer = window.setInterval(() => setRemaining(value => Math.max(0, value - 1)), 1000);
    return () => window.clearInterval(timer);
  }, []);
  async function send() {
    if (busy) return;
    setBusy(true); setError("");
    try {
      const sent = await api.securityEmail.send(purpose, flow, fixed ? undefined : address.trim());
      setRequest(sent.request_id); setRemaining(sent.resend_after);
      setCode(""); setVerified(false); onVerified("");
    } catch (error) { setError(message(error)); }
    finally { setBusy(false); }
  }
  async function verify() {
    if (busy) return;
    setBusy(true); setError("");
    try {
      const result = await api.securityEmail.verify(request, code);
      setVerified(true); setCode(""); onVerified(result.grant);
    } catch (error) { setError(message(error)); }
    finally { setBusy(false); }
  }
  return <fieldset className="acct-form" disabled={busy} style={{ border: 0, padding: 0, margin: "12px 0", minWidth: 0 }}>
    <legend className="acct-label">{label}</legend>
    {fixed ? <p className="hint">验证码将发送到 {fixed}</p> :
      <label className="acct-field"><span className="acct-label">新邮箱</span>
        <input className="auth-input" type="email" autoComplete="email" maxLength={254} value={address}
          onChange={event => {
            setAddress(event.target.value); setRequest(""); setCode("");
            setVerified(false); onVerified(""); setError("");
          }} />
      </label>}
    <button type="button" className="btn btn-ghost btn-sm" onClick={() => void send()}
      disabled={busy || remaining > 0 || (!fixed && !address.trim())}>
      {remaining > 0 ? remaining + " 秒后可重发" : "发送验证码"}
    </button>
    {request && !verified && <><label className="acct-field"><span className="acct-label">{label}验证码</span>
      <input className="auth-input" inputMode="numeric" autoComplete="one-time-code" maxLength={6}
        value={code} onChange={event => setCode(event.target.value.replace(/[^0-9]/g, ""))} />
    </label>
      <button type="button" className="btn btn-ghost btn-sm" disabled={busy || code.length !== 6}
        onClick={() => void verify()}>验证{label}</button>
    </>}
    {verified && <p className="hint" role="status">{label}已验证，请在 5 分钟内确认完成。</p>}
    {error && <p className="acct-err" role="alert">{error}</p>}
  </fieldset>;
}

function EmailDialog({ state, onClose, onSaved }: { state: State; onClose: () => void; onSaved: (state: State) => void }) {
  const [flow] = useState(() => crypto.randomUUID());
  const [oldGrant, setOldGrant] = useState("");
  const [newGrant, setNewGrant] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function save() {
    if (busy || !newGrant || (state.bound && !oldGrant)) return;
    setBusy(true); setError("");
    try {
      const result = state.bound ? await api.securityEmail.change(oldGrant, newGrant) : await api.securityEmail.bind(newGrant);
      onSaved(result);
    } catch (error) { setError(message(error)); }
    finally { setBusy(false); }
  }
  return <DialogShell open title={state.bound ? "换绑安全邮箱" : "绑定安全邮箱"}
    desc="至少保留一个已验证的安全邮箱。换绑需要分别验证旧邮箱和新邮箱。" busy={busy}
    onClose={onClose} footer={<>
      <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={onClose}>取消</button>
      <button type="button" className="btn btn-primary btn-sm" disabled={busy || !newGrant || (state.bound && !oldGrant)}
        onClick={() => void save()}>{busy ? "正在保存…" : "确认完成"}</button>
    </>}>
    <fieldset disabled={busy} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
      {state.bound && <VerifyEmail label="原邮箱" fixed={state.masked_email ?? ""} purpose="change_email_old"
        flow={flow} onVerified={setOldGrant} />}
      <VerifyEmail label="新邮箱" purpose={state.bound ? "change_email_new" : "bind_email"}
        flow={flow} onVerified={setNewGrant} />
    </fieldset>
    {error && <p className="acct-err" role="alert">{error}</p>}
  </DialogShell>;
}

export default function SecurityEmailSettings() {
  const [state, setState] = useState<State | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const [notice, setNotice] = useState("");
  useEffect(() => {
    let alive = true;
    api.securityEmail.state().then(value => { if (alive) setState(value); })
      .catch(error => { if (alive) setError(message(error)); });
    return () => { alive = false; };
  }, []);
  return <section id="account-security">
    <p className="sec-label">账号安全</p>
    <div className="card card-pad">
      <div className="card-head"><span className="card-title" style={{ display: "inline-flex", alignItems: "center", gap: 8 }}><BrandIcon brand="qqmail" />安全邮箱</span>
        <span className="meta">{state?.bound ? state.masked_email : state ? "未绑定" : error ? "暂不可用" : "读取中…"}</span></div>
      <p className="hint" style={{ marginTop: 8 }}>
        {state?.bound ? "换绑需要验证旧邮箱和新邮箱，不能直接解绑唯一的安全邮箱。" : "绑定已验证的邮箱，为后续密码找回做好准备。"}
      </p>
      {error && <p className="acct-err" role="alert">{error}</p>}
      {state && !state.ready && <p className="hint">邮件服务暂不可用，请稍后再试。</p>}
      <button type="button" className="btn btn-ghost btn-sm" style={{ marginTop: 12 }}
        disabled={!state?.ready} onClick={() => { setNotice(""); setOpen(true); }}>
        {state?.bound ? "换绑邮箱" : "绑定邮箱"}
      </button>
      {notice && <p className="hint" role="status">{notice}</p>}
    </div>
    {open && state && <EmailDialog state={state} onClose={() => setOpen(false)}
      onSaved={value => { setState(value); setOpen(false); setNotice("安全邮箱已更新"); }} />}
  </section>;
}
