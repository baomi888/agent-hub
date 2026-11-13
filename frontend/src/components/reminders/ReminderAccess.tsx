"use client";

// 「提醒权限」—— 待办页「设置」标签里唯一的一张提醒卡。
//
// ## 为什么只剩一个开关
//
// 提醒时刻已经跟着每条待办走了（待办自己的「提醒我」时间），所以这里不再需要
// 「每天几点统一提醒」这一档：两处都能设时间时，用户改了 A 发现 B 没变，
// 只会认为其中一个坏了。
//
// 这张卡只回答一个问题：**这台设备响不响**。下面列出账号里已经开了提醒的设备，
// 每台都能单独关掉——手机上开了、电脑上忘了关，是这一屏最常见的需求。
//
// ## 三个不能含糊的地方
//
// ① 开关管的是**本机**，不是账号。
//    `/api/push/status` 的 `subscribed` 是账号级的：手机开了就是 true。
//    直接拿它渲染「已开启」，在从没开过的电脑上就是假话——于是必须先用
//    `findMyDevice()` 认出本机那一行，把状态分成「这台开 / 别的设备开 / 都没开」。
//
// ② 别的设备只能关，不能开。
//    我们碰不到那台设备的浏览器，没法替它申请通知权限。所以列表里别的设备
//    给的是「关掉」而不是开关——一个点不动的开关比没有开关更让人困惑。
//
// ③ 关掉 ≠ 删除账号数据。
//    这里删掉的只是「这台设备的推送订阅」。待办、提醒都还在，重新打开就恢复。
//    文案必须这么说，否则用户会以为关掉提醒会连带删掉待办。
//
// ## 没有日历兜底了（2026-11-11）
//
// 原来的「日历提醒」卡已删除。代价要说清楚：安卓的网页推送底层是 Google FCM，
// 国内连不通，日历曾是安卓唯一的系统级通道。现在安卓这台设备就是收不到到点提醒，
// 界面必须直说，而不是给一个开了不响的开关。
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
import { Switch } from "@/components/console/ui";

type View =
  | "loading"
  | "error"
  | "insecure"
  | "unsupported"
  | "ios"
  | "denied"
  | "off"
  | "other"
  | "on";

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

export default function ReminderAccess() {
  const [view, setView] = useState<View>("loading");
  const [list, setList] = useState<PushDevice[]>([]);
  /** 本机在列表里那一行。认不出来就是 null（view 落到 off / other） */
  const [mine, setMine] = useState<PushDevice | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");

  const refresh = useCallback(async () => {
    const sup = pushSupport();
    if (sup === "insecure") {
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
    if (!s) {
      // `status` 返回 null 是**请求失败**，不是"浏览器不支持"。
      // 混成 unsupported 会对着一个其实能收通知的浏览器说"你收不到" ——
      // 用户照着这句话去换设备，方向从一开始就错了。
      setView("error");
      return;
    }
    if (!s.supported) {
      setView("unsupported");
      return;
    }
    const devices = s.device_list || [];
    const my = await findMyDevice(devices);
    setList(devices);
    setMine(my);
    if (my) setView("on");
    else if (devices.some((d) => !d.disabled)) setView("other");
    else setView("off");
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const toggle = useCallback(
    async (next: boolean) => {
      if (busy) return;
      setBusy(true);
      setNote("");
      if (next) {
        const res = await enablePush();
        if (res.ok) {
          await refresh();
        } else if (res.reason === "denied") {
          setView("denied");
        } else {
          // 浏览器原始错误对用户是天书，但排查时是唯一线索，别静默吞掉
          if (res.detail) console.warn("[push] 订阅失败：", res.detail);
          setNote("这台设备连不上通知服务，暂时收不到提醒");
        }
      } else {
        // 本机退订：浏览器那边也要撤（否则服务端删了行、浏览器还留着悬空订阅）
        await disablePush();
        await refresh();
        setNote("已关掉这台。待办和提醒都在，想收的时候再打开。");
      }
      setBusy(false);
    },
    [busy, refresh],
  );

  const removeOther = useCallback(
    async (d: PushDevice) => {
      if (busy) return;
      setBusy(true);
      setNote("");
      try {
        await api.push.remove(d.id);
        await refresh();
        setNote(`已关掉「${deviceLabel(d.platform, d.ua)}」。要再开，得在那台设备上重新打开一次。`);
      } catch {
        setNote("没关掉，稍后再试");
      }
      setBusy(false);
    },
    [busy, refresh],
  );

  const test = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    setNote("");
    try {
      const r = await api.push.test();
      setNote(r.sent > 0 ? "发出去了，看通知栏应该有一条" : "没发出去，先关掉再重新打开试试");
    } catch (e) {
      const msg = (e as Error)?.message || "";
      setNote(msg && !/^HTTP \d+/.test(msg) ? msg : "发送失败，稍后再试");
    }
    setBusy(false);
  }, [busy]);

  const alive = list.filter((d) => !d.disabled);

  // 开关只对「能自己决定」的状态可操作
  const toggleable = view === "on" || view === "off" || view === "other";

  const hint = (() => {
    switch (view) {
      case "loading":
        return "正在看这台设备的通知权限…";
      case "error":
        return "没读到这台设备的通知状态，刷新页面试试。";
      case "insecure":
        // 本地 http 调试会走到这里。绝不能说「加到主屏就有」——加了也没有。
        return "当前地址是 http，浏览器不给通知权限。等站点挂上 https 才能打开。";
      case "ios":
        return "iPhone 要先把这个站「添加到主屏」，从主屏图标打开之后才有通知。";
      case "unsupported":
        // 安卓的网页推送底层是 Google 的服务，国内连不通，换浏览器也不会好。
        // 日历提醒已经删掉，所以这里没有第二条路可指——直说，别给假的出路。
        return "这个浏览器不支持通知。安卓上的网页通知走 Google 的服务，国内连不通，换浏览器也一样——这台设备现在收不到到点提醒，别的设备照常。";
      case "denied":
        return "通知权限被拒绝过，浏览器不再让网页问第二次。点地址栏左边的锁 → 通知 → 允许，刷新后再打开。";
      case "on":
        return "开着。到点会通知你这台设备；关掉之后就不再通知你，待办和提醒都还在。";
      case "other":
        return `这台设备还没开。提醒目前发往下面 ${alive.length} 台已开的设备。`;
      default:
        // 不写「只在你还有事没做完时才发」：待办完成会撤掉它自己的提醒，
        // 但课表、打卡这类联动提醒到点就会发，跟"做没做完"无关。
        return "打开后，到点会通知你这台设备。关掉只是不再通知，待办和提醒都还在。";
    }
  })();

  return (
    <section className="card card-pad">
      <div
        className="row"
        style={{ justifyContent: "space-between", alignItems: "center", gap: 10 }}
      >
        <h3 className="card-title">提醒权限</h3>
        <Switch
          checked={view === "on"}
          label="这台设备的提醒"
          disabled={busy || !toggleable}
          onChange={(next) => void toggle(next)}
        />
      </div>

      <p className="mt-1 text-[var(--fs-sm)] text-[var(--text-3)]">{hint}</p>

      {alive.length > 0 ? (
        <div className="mt-3">
          <p className="text-[var(--fs-sm)] text-[var(--text-3)]">
            已开启提醒 {alive.length} 台
          </p>
          {alive.map((d) => {
            const isMine = d.id === mine?.id;
            return (
              <div
                key={d.id}
                className="row"
                style={{
                  justifyContent: "space-between",
                  alignItems: "center",
                  gap: 10,
                  padding: "8px 0",
                  borderTop: "1px dashed var(--line)",
                }}
              >
                <div style={{ minWidth: 0 }}>
                  <div className="text-[var(--fs-md)] text-[var(--text-1)]">
                    {deviceLabel(d.platform, d.ua)}
                    {isMine ? (
                      <span className="text-[var(--fs-xs)] text-[var(--text-3)]"> · 这台</span>
                    ) : null}
                  </div>
                  <p className="mt-0.5 text-[var(--fs-sm)] text-[var(--text-3)]">
                    {d.last_ok_at ? `最近成功 ${fmtTs(d.last_ok_at)}` : "已登记，还没发过"}
                  </p>
                </div>
                {/* 本机不在这里给按钮：上面的开关就是它的开关，两个控件管一件事
                    只会让人怀疑自己点的是哪一个 */}
                {isMine ? (
                  <span className="text-[var(--fs-sm)] text-[var(--text-3)]">用上面的开关</span>
                ) : (
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    disabled={busy}
                    onClick={() => void removeOther(d)}
                  >
                    关掉
                  </button>
                )}
              </div>
            );
          })}
        </div>
      ) : null}

      {view === "on" ? (
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          style={{ marginTop: 10 }}
          disabled={busy}
          onClick={() => void test()}
        >
          发一条试试
        </button>
      ) : null}

      {note ? (
        <p className="mt-2 text-[var(--fs-sm)] text-[var(--text-3)]">{note}</p>
      ) : null}
    </section>
  );
}
