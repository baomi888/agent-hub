"use client";

// 原生壳（APK）深链回跳的全局接收端。
//
// 为什么需要它，而不是全交给授权引导卡（AlipayLaunchOverlay）：
// 用户在支付宝里停留久了，Android 会回收后台 WebView。此时壳由
// `zhisui://oauth/alipay?state=…` 冷启动，WebView 从首页重新加载 ——
// 引导卡根本没挂载过，没人监听事件、没人去 /poll，结果就是「授权成功了
// 但打开还是未登录」。这个桥挂在根布局，页面一加载就在，补上这一条。
//
// 与引导卡的分工：两者都会 /poll，而交接结果是一次性领取的，抢着领会让
// 另一方永远拿到 pending。所以用 cardsOpen 计数让路 —— 卡片在场就由卡片领
// （它还要处理「已绑定」提示与关闭弹窗），卡片不在才由这里领。
import { useEffect } from "react";
import { api } from "@/lib/api";

let cardsOpen = 0;

/** 引导卡挂载/卸载时调用，告诉全局桥「现在有人负责领结果」。 */
export function markOAuthCard(open: boolean) {
  cardsOpen = Math.max(0, cardsOpen + (open ? 1 : -1));
}

function stateOf(url: string): string {
  try {
    return new URL(url).searchParams.get("state") || "";
  } catch {
    return "";
  }
}

export default function OAuthDeepLinkBridge() {
  useEffect(() => {
    const onReturn = (event: Event) => {
      if (cardsOpen > 0) return; // 卡片自己在领，别抢一次性交接
      const detail = (event as CustomEvent).detail;
      const state = stateOf(typeof detail === "string" ? detail : "");
      if (!state) return;
      // ok 时 api.alipay.poll 内部会 notifyAuthorized()，各处数据自动重拉；
      // 失败/待定无需打扰用户 —— 他此刻看到的就是原来的界面。
      void api.alipay.poll(state).catch(() => {});
    };
    window.addEventListener("zhisui:oauth-return", onReturn);
    return () => window.removeEventListener("zhisui:oauth-return", onReturn);
  }, []);
  return null;
}
