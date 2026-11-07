"use client";

// 设置页里的「每日提醒」——和 InstallAppRow 一样的定位：首页那条是一次性的
// 提醒（关了 30 天内不再出现），这里才是随时能找到的正式开关。
//
// ★★ 这里最容易搞错的一件事：「账号有没有设备订阅」≠「这台设备开没开」。
//   服务端的 `subscribed` 是账号级的（手机开了就是 true）。早先直接拿它渲染
//   "已开启"，结果在从没开过推送的电脑上：卡片显示"已开启"、给出"发一条测试"
//   和"关掉"，点测试通知发去了手机（用户以为发错了设备），点关掉又因为本机
//   没有订阅而什么都没删。所以现在必须先用 findMyDevice() 认出本机，
//   把状态分成三种：
//     on     这台设备已开
//     other  这台没开，但账号里别的设备开着（给出"在这台设备上打开"）
//     off    一台都没有
//
// 三种"开不了"的原因分别给不同的话，因为用户能做的事完全不同：
//   浏览器不支持      → 换浏览器（微信里打开的最常见）
//   iOS 没装到主屏    → 去装（推送是装完才有的，不是把开关藏起来）
//   权限被拒绝        → 去地址栏左边的锁里改（页面内改不了）
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import {
  pushSupport,
  notificationPermission,
  enablePush,
  disablePush,
  findMyDevice,
  deviceLabel,
} from "@/lib/push";
import type { PushDevice } from "@/lib/types";
import { REMIND_CHANGED } from "@/components/settings/ReminderTimeCard";

type View = "loading" | "insecure" | "unsupported" | "ios" | "denied" | "off" | "other" | "on";

/** 时间戳 → 「今天 09:47」/「11/6 20:00」。空值给空串，由调用方决定说什么。 */
function fmtTs(ts?: number | null): string {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const now = new Date();
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  if (d.toDateString() === now.toDateString()) return `今天 ${hh}:${mm}`;
  return `${d.getMonth() + 1}/${d.getDate()} ${hh}:${mm}`;
}

export default function PushRow() {
  const [view, setView] = useState<View>("loading");
  const [list, setList] = useState<PushDevice[]>([]);
  /** 本机在列表里那一行（认不出来就是 null，view 会落到 off / other） */
  const [mine, setMine] = useState<PushDevice | null>(null);
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
    const devices = s.device_list || [];
    const my = await findMyDevice(devices);
    setList(devices);
    setMine(my);
    setTime(s.time || "");
    if (my) setView("on");
    else if (devices.some((d) => !d.disabled)) setView("other");
    else setView("off");
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
      await refresh(); // 重新认本机：要把"这台"标出来、把设备列表刷新
      return;
    }
    if (res.reason === "denied") setView("denied");
    // ★ 不能说"换 Edge"：安卓上所有浏览器（含 Edge 安卓版）的推送底层都是
    //   Google FCM，国内连不通，换浏览器不会好。指到下面那张日历卡片上去。
    else setNote("这台设备连不上推送服务，用下面的「日历提醒」一样能每天提醒");
  }, [refresh]);

  const off = useCallback(async () => {
    setBusy(true);
    setNote("");
    await disablePush();
    setBusy(false);
    await refresh();
  }, [refresh]);

  const remove = useCallback(
    async (id: string) => {
      setBusy(true);
      setNote("");
      try {
        await api.push.remove(id);
        await refresh();
      } catch {
        setNote("移除失败，稍后再试");
      }
      setBusy(false);
    },
    [refresh],
  );

  const test = useCallback(async () => {
    setBusy(true);
    setNote("");
    try {
      const r = await api.push.test();
      // sent 是**真发出去的台数**。本机的订阅可能刚好失效（410）而别的设备
      // 收到了，这时说"已发出"会让人以为这台也行，所以按台数分三档说。
      if (r.sent <= 0) setNote("没发出去：这台设备的订阅可能已失效，点「关掉」再重新打开");
      else if (r.sent === 1) setNote("已发出，看系统通知栏");
      else setNote(`已发到 ${r.sent} 台设备，这台也会响一次`);
    } catch {
      setNote("发送失败，稍后再试");
    }
    setBusy(false);
  }, []);

  const aliveCount = list.filter((d) => !d.disabled).length;
  const others = list.filter((d) => !d.disabled && d.id !== mine?.id);

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
        // ★ 只说"这台已开"，别写"当前这台设备已登记（共 N 台）"——
        //   那是在账号级状态上补一句话，本机没订阅时就是假话。
        return `每天 ${time || "到点"} 会提醒你一次，只在你还有任务没做完时才发。这台设备已开启。`;
      case "other":
        return `这台设备还没开。你的提醒目前只发往 ${aliveCount} 台已开的设备（下面列着）。想让这台也收，点「在这台设备上打开」。`;
      default:
        return `每天 ${time || "到点"} 提醒你一次，只在还有任务没做完时才发。不做别的打扰，随时可以关。`;
    }
  })();

  const showList = (view === "on" || view === "other") && list.length > 0;

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
              关掉这台
            </button>
          </div>
        ) : view === "other" ? (
          <button type="button" className="btn btn-ghost btn-sm" onClick={on} disabled={busy}>
            {busy ? "开启中…" : "在这台设备上打开"}
          </button>
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

      {showList ? (
        <div style={{ marginTop: 10 }}>
          {list.map((d) => {
            const isMine = d.id === mine?.id;
            return (
              <div
                key={d.id}
                className="row"
                style={{
                  justifyContent: "space-between",
                  alignItems: "center",
                  gap: 10,
                  padding: "7px 0",
                  borderTop: "1px dashed var(--line)",
                }}
              >
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: "var(--fs-sm)" }}>
                    {deviceLabel(d.platform, d.ua)}
                    {isMine ? (
                      <span className="hint" style={{ marginLeft: 6 }}>
                        · 这台
                      </span>
                    ) : null}
                  </div>
                  <p className="hint" style={{ margin: "2px 0 0" }}>
                    {d.disabled
                      ? "已失效——那台设备清过浏览器数据，需要重新开启"
                      : d.last_ok_at
                        ? `最近成功 ${fmtTs(d.last_ok_at)}`
                        : "已登记，还没发过"}
                  </p>
                </div>
                {isMine ? null : (
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    onClick={() => remove(d.id)}
                    disabled={busy}
                  >
                    移除
                  </button>
                )}
              </div>
            );
          })}
          {view === "on" && others.length > 0 ? (
            <p className="hint" style={{ marginTop: 6 }}>
              提醒会同时发到上面每一台开着的设备。
            </p>
          ) : null}
        </div>
      ) : null}

      {note ? <p className="hint" style={{ marginTop: 4 }}>{note}</p> : null}
    </div>
  );
}
