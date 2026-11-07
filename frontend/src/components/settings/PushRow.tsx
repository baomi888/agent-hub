"use client";

// 设置页里的「每日提醒」——和 InstallAppRow 一样的定位：首页那条是一次性的
// 提醒（关了 30 天内不再出现），这里才是随时能找到的正式开关。
//
// 三种"开不了"的原因分别给不同的话，因为用户能做的事完全不同：
//   浏览器不支持      → 换浏览器（微信里打开的最常见）
//   iOS 没装到主屏    → 去装（推送是装完才有的，不是把开关藏起来）
//   权限被拒绝        → 去地址栏左边的锁里改（页面内改不了）
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { pushSupport, notificationPermission, enablePush, disablePush } from "@/lib/push";
import { REMIND_CHANGED } from "@/components/settings/ReminderTimeCard";

type View = "loading" | "insecure" | "unsupported" | "ios" | "denied" | "off" | "on";

export default function PushRow() {
  const [view, setView] = useState<View>("loading");
  const [devices, setDevices] = useState(0);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  /** 提醒时刻。★ 来自服务端的 /api/push/status，不是写死的 20:00 —— */
  const [time, setTime] = useState("");

  const refresh = useCallback(async () => {
    const sup = pushSupport();
    if (sup === "insecure") {
      // 本地用 http 局域网地址调试时一定会走到这里。文案绝不能说
      // 「加到主屏就有」——加了也没有，那是把排查方向带偏。
      setView("insecure");
      return;
    }
    if (sup === "ios-needs-install") {
      setView("ios");
      return;
    }
    if (sup !== "ready") {
      setView("unsupported");
      return;
    }
    if (notificationPermission() === "denied") {
      setView("denied");
      return;
    }
    const s = await api.push.status();
    if (!s || !s.supported) {
      setView("unsupported");
      return;
    }
    setDevices(s.devices);
    setTime(s.time || "");
    setView(s.subscribed ? "on" : "off");
  }, []);

  useEffect(() => {
    // refresh 里全是异步读浏览器/接口，不改状态机之外的东西；放 effect 里调一次即可
    void refresh();
  }, [refresh]);

  // 上面那张「提醒时间」卡改完会广播，这里跟着改文案。
  // 不重新拉 status：多一次请求，而且回来的还是同一批字段。
  useEffect(() => {
    const onChanged = (e: Event) => {
      const detail = (e as CustomEvent<{ time?: string }>).detail;
      if (detail?.time) setTime(detail.time);
    };
    window.addEventListener(REMIND_CHANGED, onChanged);
    return () => window.removeEventListener(REMIND_CHANGED, onChanged);
  }, []);

  const on = useCallback(async () => {
    setBusy(true);
    setNote("");
    const res = await enablePush();
    setBusy(false);
    if (res.ok) {
      setDevices(res.devices);
      setView("on");
      return;
    }
    if (res.reason === "denied") setView("denied");
    // ★ 不能说"换 Edge"：安卓上所有浏览器（含 Edge 安卓版）的推送底层都是
    //   Google FCM，国内连不通，换浏览器不会好。指到下面那张日历卡片上去。
    else setNote("这台设备连不上推送服务，用下面的「日历提醒」一样能每天提醒");
  }, []);

  const off = useCallback(async () => {
    setBusy(true);
    await disablePush();
    setBusy(false);
    setView("off");
    setDevices(0);
  }, []);

  const test = useCallback(async () => {
    setBusy(true);
    setNote("");
    try {
      const r = await api.push.test();
      setNote(r.sent > 0 ? "已发出，看系统通知栏" : "没发出去：这台设备的订阅可能已失效");
    } catch {
      setNote("发送失败，稍后再试");
    }
    setBusy(false);
  }, []);

  const hint = (() => {
    switch (view) {
      case "insecure":
        // 只有本地调试会看到这一句（线上是 https）。说清楚是地址的问题，
        // 并把人指到「日历提醒」——它走的是系统日历的本机定时器，不吃安全上下文。
        return "当前地址是 http，浏览器不会给通知权限（手机上尤其严格，添加到主屏也不行）。这条要等站点挂上 https 才能用；下面的「日历提醒」不受影响，现在就能用。";
      case "ios":
        return "iPhone 要先把这个站「添加到主屏」，从主屏图标打开之后才有推送。教程在上面「安装到桌面」里。";
      case "unsupported":
        // 安卓上的国产浏览器（微信、QQ、UC、夸克、各家自带浏览器）基本都没有
        // Push API；而装了 Chrome 的那批在国内容易连不上 FCM。所以这句不能只说
        // "换浏览器"，必须把人指到下面那张日历卡片——那是安卓唯一能走通的路。
        return "这个浏览器不支持推送提醒。手机上看不到这条的话，用下面的「日历提醒」一样每天响。";
      case "denied":
        return "通知权限被拒绝过，且浏览器不允许网页再问一次。点地址栏左边的锁 → 通知 → 允许，刷新后再打开。";
      case "on":
        return `每天 ${time || "到点"} 会提醒你一次，只在你还有任务没做完时才发。当前这台设备已登记${
          devices > 1 ? `（共 ${devices} 台）` : ""
        }。`;
      default:
        return `每天 ${time || "到点"} 提醒你一次，只在还有任务没做完时才发。不做别的打扰，随时可以关。`;
    }
  })();

  return (
    <div className="card card-pad">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontSize: "var(--fs-lg)" }}>每日提醒</span>
        {view === "loading" ? null : view === "on" ? (
          <div className="row" style={{ gap: 8, alignItems: "center" }}>
            <button type="button" className="btn btn-ghost btn-sm" onClick={test} disabled={busy}>
              发一条测试
            </button>
            <button type="button" className="btn btn-ghost btn-sm" onClick={off} disabled={busy}>
              关掉
            </button>
          </div>
        ) : (
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={on}
            disabled={
              busy ||
              view === "insecure" ||
              view === "unsupported" ||
              view === "ios" ||
              view === "denied"
            }
          >
            {busy ? "开启中…" : "打开提醒"}
          </button>
        )}
      </div>

      <p className="hint">{hint}</p>
      {note ? <p className="hint" style={{ marginTop: 4 }}>{note}</p> : null}
    </div>
  );
}
