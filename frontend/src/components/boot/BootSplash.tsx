"use client";

import { useEffect, useState } from "react";
import MascotLogo from "@/components/chat/MascotLogo";

type Mode = "boot" | "auth";

const SLOGAN = ["越", "来", "越", "懂", "你"];

function Sprout() {
  return (
    <svg viewBox="0 0 40 36" width="100%" height="100%" aria-hidden="true">
      <path
        d="M20 35 C19 28 20 22 23 17"
        fill="none"
        stroke="#7C9A54"
        strokeWidth="3"
        strokeLinecap="round"
      />
      <path
        d="M22 20 C14 19 8 13 7 6 C15 6 21 11 23 18 Z"
        fill="#7C9A54"
        stroke="#5E7A3F"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <path
        d="M23 15 C29 13 33 8 34 2 C26 3 22 8 21 14 Z"
        fill="#8FAF62"
        stroke="#5E7A3F"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function BootScene({ mode }: { mode: Mode }) {
  const auth = mode === "auth";
  const base = auth ? 0.06 : 0.18;

  return (
    <div className={`zs-boot-scene${auth ? " is-auth" : ""}`}>
      <div className="zs-boot-logo-tile">
        <MascotLogo className="zs-boot-mascot" />
      </div>

      <div className="zs-boot-slogan" aria-hidden="true">
        <span className="zs-boot-brand">
          <span
            className="zs-boot-char zs-boot-brand-char"
            style={{ animationDelay: `${base}s` }}
          >
            知
          </span>
          <span
            className="zs-boot-char zs-boot-brand-char"
            style={{ animationDelay: `${base + 0.055}s` }}
          >
            穗
          </span>
        </span>
        <span
          className="zs-boot-char zs-boot-comma"
          style={{ animationDelay: `${base + 0.11}s` }}
        >
          ，
        </span>
        {SLOGAN.map((ch, i) => (
          <span
            key={`${ch}-${i}`}
            className="zs-boot-char"
            style={{ animationDelay: `${base + 0.165 + i * 0.055}s` }}
          >
            {ch}
          </span>
        ))}
      </div>

      <div className="zs-boot-line-wrap">
        <div className="zs-boot-line">
          <span className="zs-boot-line-fill" />
          {auth ? <span className="zs-boot-line-sweep" /> : null}
        </div>
        <span className="zs-boot-sprout">
          <span className="zs-boot-sprout-inner">
            <Sprout />
          </span>
        </span>
      </div>

      <p
        className="zs-boot-status"
        role={auth ? "status" : undefined}
        aria-live={auth ? "polite" : undefined}
      >
        {auth ? "正在确认登录状态" : "正在打开知穗"}
      </p>
    </div>
  );
}

/**
 * 全站冷启动层。首帧由服务端一起渲染，所以不会先闪一下页面内容；
 * 约 1.7s 后淡出。原生壳里由 CSS `.in-app` 直接隐藏，避免和原生启动层
 * 各演一遍。
 */
export default function BootSplash() {
  const [leaving, setLeaving] = useState(false);
  const [gone, setGone] = useState(false);

  useEffect(() => {
    const fade = window.setTimeout(() => setLeaving(true), 1700);
    const remove = window.setTimeout(() => setGone(true), 2080);
    return () => {
      window.clearTimeout(fade);
      window.clearTimeout(remove);
    };
  }, []);

  if (gone) return null;

  return (
    <div
      className={`zs-boot-splash${leaving ? " is-leaving" : ""}`}
      role="presentation"
      aria-hidden="true"
    >
      <BootScene mode="boot" />
    </div>
  );
}

/** 登录信息还没确认完时的全屏等待层，复用同一套品牌场景。 */
export function AuthLoading() {
  return (
    <div className="zs-boot-auth" aria-busy="true">
      <BootScene mode="auth" />
    </div>
  );
}
