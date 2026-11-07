"use client";

import { useState, useSyncExternalStore } from "react";
import { useRouter } from "next/navigation";
import {
  detectPlatform,
  isInstalled,
  promptInstall,
  subscribePwa,
  type Platform,
} from "@/lib/pwa";

/* 设置页里的固定入口。
 *
 * 和 InstallBanner 的分工：Banner 只在手机首次进来时提一次，关了就不再烦；
 * 这里是「随时能找得到」的正式入口，桌面端也显示（桌面同样能装成独立窗口）。
 *
 * 三个状态都住在浏览器里，一律走 useSyncExternalStore 读 —— 既避免
 * 「服务端没有 window」的 hydration 打架，也避免在 effect 里 setState。
 */
const getPlatform = (): Platform => detectPlatform();
const getInstalled = (): boolean => isInstalled();
const getCanPrompt = (): boolean => !!window.__baomiInstallPrompt;

export default function InstallAppRow() {
  const platform = useSyncExternalStore(subscribePwa, getPlatform, () => "other" as Platform);
  const installed = useSyncExternalStore(subscribePwa, getInstalled, () => false);
  const canPrompt = useSyncExternalStore(subscribePwa, getCanPrompt, () => false);

  // 分步教程在独立页 /install（不要求登录，可以单独发给别人）
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  const onAction = async () => {
    if (!canPrompt) {
      router.push("/install");
      return;
    }
    setBusy(true);
    await promptInstall();
    setBusy(false);
    // 不管用户点没点「安装」，都别继续下一步 —— 没装成功就把他送到分步教程
    if (!isInstalled()) router.push("/install");
  };

  return (
    <div className="card card-pad">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontSize: "var(--fs-lg)" }}>安装到桌面</span>
        {installed ? (
          <span className="meta">已安装</span>
        ) : (
          <button type="button" className="btn btn-ghost btn-sm" onClick={onAction} disabled={busy}>
            {busy ? "安装中…" : canPrompt ? "立即安装" : "怎么装"}
          </button>
        )}
      </div>

      <p className="hint">
        {installed ? (
          <>
            你现在就是从桌面图标打开的。想重新装一次的话，先在桌面删掉旧图标，再用浏览器重新添加。
          </>
        ) : (
          <>
            装好后从桌面图标打开，全屏、没有地址栏，和装了个 App 一样；断网时会显示一个提示页，不会白屏。
            {platform === "desktop" && " 电脑上装出来是独立窗口，开始菜单也能搜到。"}
          </>
        )}
      </p>
    </div>
  );
}
