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
 *   2. 延后一点注册，不跟首屏抢带宽
 *
 * ★ 不要等 window 的 load 事件。线上实测首页的 document.readyState 会长时间停在
 *   "interactive"（存在长连接类资源），load 迟迟不触发 —— 等在 load 上等于永不注册，
 *   而现象是"SW 安装明明没问题，但线上就是没有注册"。改成定时器，1.2 秒后直接注册。
 */
export default function PwaRegister() {
  useEffect(() => {
    if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return;
    if (process.env.NODE_ENV !== "production") return;

    const timer = window.setTimeout(() => {
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {
        /* 静默失败：http 环境、浏览器策略、隐私模式都会走到这里 */
      });
    }, 1200);

    return () => window.clearTimeout(timer);
  }, []);

  return null;
}
