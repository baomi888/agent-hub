"use client";

import { useEffect } from "react";

/**
 * 注册 Service Worker。
 *
 * 不做任何 UI —— 注册失败也不该打断用户，最坏结果只是没有离线能力、
 * 安卓 Chrome 不给「安装应用」入口。
 *
 * 交给 /sw.js 自己去决定缓存什么；这里的职责仅有两件：
 *   1. 生产环境才注册（开发环境下 SW 缓存 /_next/static 会干扰热更新，排查半天）
 *   2. 等 load 之后再注册，不跟首屏抢带宽
 */
export default function PwaRegister() {
  useEffect(() => {
    if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return;
    if (process.env.NODE_ENV !== "production") return;

    const register = () => {
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {
        /* 静默失败：http 环境、浏览器策略、隐私模式都会走到这里 */
      });
    };

    if (document.readyState === "complete") {
      register();
      return;
    }
    window.addEventListener("load", register, { once: true });
    return () => window.removeEventListener("load", register);
  }, []);

  return null;
}
