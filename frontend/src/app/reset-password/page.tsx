"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import PasswordInput from "@/components/PasswordInput";

export default function ResetPasswordPage() {
  const router=useRouter();
  const [email,setEmail]=useState("");
  const [code,setCode]=useState("");
  const [requestId,setRequestId]=useState("");
  const [grant,setGrant]=useState("");
  const [password,setPassword]=useState("");
  const [confirm,setConfirm]=useState("");
  const [busy,setBusy]=useState(false);
  const [remaining,setRemaining]=useState(0);
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  useEffect(()=>{
    const timer=window.setInterval(()=>setRemaining(value=>Math.max(0,value-1)),1000);
    return ()=>window.clearInterval(timer);
  },[]);
  async function send(){
    if(busy)return;
    setBusy(true);setError("");
    try{
      const result=await api.passwordReset.send(email.trim());
      setRequestId(result.request_id);setRemaining(result.resend_after);setNotice(result.message);
      setCode("");setGrant("");setPassword("");setConfirm("");
    }catch(error){setError(error instanceof Error ? error.message : "暂时无法发送，请稍后重试");}
    finally{setBusy(false);}
  }
  async function verify(){
    if(busy)return;
    setBusy(true);setError("");
    try{
      const result=await api.passwordReset.verify(requestId,code);
      setGrant(result.grant);setCode("");setNotice("邮箱验证通过，请在 5 分钟内设置新密码。");
    }catch(error){setError(error instanceof Error ? error.message : "验证失败，请重试");}
    finally{setBusy(false);}
  }
  async function reset(){
    if(busy)return;
    if(password!==confirm){setError("两次输入的新密码不一致");return;}
    setBusy(true);setError("");
    try{
      await api.passwordReset.finish(grant,password,confirm);
      setGrant("");setCode("");setPassword("");setConfirm("");setEmail("");setNotice("");
      router.replace("/login");
    }catch(error){setError(error instanceof Error ? error.message : "重置失败，请重新验证");}
    finally{setBusy(false);}
  }
  return <div className="auth-stage"><div className="auth-card">
    <h1 className="auth-name" style={{marginBottom:24}}>找回密码</h1>
    <form className="auth-form" onSubmit={event=>{
      event.preventDefault();
      if(grant)void reset();else if(requestId)void verify();else void send();
    }}>
      <label className="auth-label"><span>已绑定的安全邮箱</span>
        <input className="auth-input" type="email" autoComplete="email" value={email} required maxLength={254} disabled={busy}
          onChange={event=>{
            setEmail(event.target.value);setRequestId("");setGrant("");setCode("");
            setPassword("");setConfirm("");setNotice("");setError("");
          }} />
      </label>
      {!grant && <button type="button" className="btn btn-ghost btn-sm" disabled={busy || remaining>0 || !email.trim()}
        onClick={()=>void send()}>{remaining>0 ? remaining+" 秒后可重发" : "发送验证码"}</button>}
      {requestId && !grant && <>
        <label className="auth-label"><span>邮箱验证码</span>
          <input className="auth-input" inputMode="numeric" autoComplete="one-time-code" value={code} maxLength={6} disabled={busy}
            onChange={event=>setCode(event.target.value.replace(/[^0-9]/g,""))} />
        </label>
        <button type="button" className="btn btn-primary btn-sm" disabled={busy || code.length!==6} onClick={()=>void verify()}>验证邮箱</button>
      </>}
      {grant && <>
        <PasswordInput label="新密码" value={password} onChange={event=>{setPassword(event.target.value);setError("");}}
          autoComplete="new-password" minLength={8} maxLength={128} required disabled={busy} />
        <PasswordInput label="再次输入新密码" value={confirm} onChange={event=>{setConfirm(event.target.value);setError("");}}
          autoComplete="new-password" minLength={8} maxLength={128} required disabled={busy} />
        <button type="submit" className="btn-new auth-submit" disabled={busy}>重置密码</button>
      </>}
      {notice && <p className="hint" role="status">{notice}</p>}
      {error && <p className="acct-err" role="alert">{error}</p>}
      <Link href="/login" className="auth-back">返回登录</Link>
    </form>
  </div></div>;
}
