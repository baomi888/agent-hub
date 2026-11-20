"use client";

import { useId, useState, type InputHTMLAttributes } from "react";

export default function PasswordInput({ label, id, ...props }:
  { label: string } & Omit<InputHTMLAttributes<HTMLInputElement>, "type">) {
  const generated = useId();
  const inputId = id ?? generated;
  const [visible, setVisible] = useState(false);
  return <div className="auth-label">
    <label htmlFor={inputId}>{label}</label>
    <div style={{position:"relative"}}>
      <input {...props} id={inputId} type={visible ? "text" : "password"}
        className={props.className ?? "auth-input"} style={{...props.style,width:"100%",paddingRight:48}} />
      <button type="button" disabled={props.disabled} aria-label={(visible ? "隐藏" : "显示")+label} aria-pressed={visible}
        onClick={() => setVisible(value=>!value)}
        style={{position:"absolute",right:8,top:"50%",transform:"translateY(-50%)",width:32,height:32,
          display:"grid",placeItems:"center",background:"transparent",border:0,color:"var(--ink)",cursor:"pointer"}}>
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
          <path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7S2 12 2 12Z" />
          <circle cx="12" cy="12" r="3" />
          {!visible && <path d="m3 3 18 18" />}
        </svg>
      </button>
    </div>
  </div>;
}
