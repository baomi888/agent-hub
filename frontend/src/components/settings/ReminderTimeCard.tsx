"use client";

// 设置页里的「提醒时间」。
//
// ## 为什么单独一张卡，不塞进「每日提醒」或「日历提醒」
//
// 这个时刻同时管两个通道：
//   · worker 到点发的 Web Push（走通知栏）
//   · 日历里那条 RRULE 每日事件的起点（安卓唯一能真的响的路）
// 塞进 PushRow，则**收不到推送的人**（安卓 FCM 不通，很常见）改不了时间，
// 而他们恰恰最需要调日历时间；塞进 CalendarRow 则没订阅日历的人改不了推送时间。
// 所以它必须是独立的一行，两个通道都读它。
//
// ## 改完之后什么会变、什么不会变（必须写在界面上）
//
// · 推送：下一跳就按新时刻判断，今天已发过的不会重发（不打扰）。
// · 订阅式日历（复制 URL）：服务端下次被拉取时给新 DTSTART，但**客户端刷新周期
//   不可控**（Google 8–24 小时，国产日历更随意），可能明天才生效。
// · 下载导入的 .ics：一次性快照，**永远不会变**，要删掉重新导入。
// 这三条不说清楚，「我改了 7 点怎么还 20 点响」就会当成 bug 报回来。

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { RemindPref } from "@/lib/types";

/** 保存成功后广播给同一页的其他卡片，让它们显示的「每天 XX:XX」跟着变 */
export const REMIND_CHANGED = "baomi:remind-changed";

const pad = (n: number) => String(n).padStart(2, "0");

// ★ 字号 16px：iOS 上小于 16px 的表单控件聚焦时会把整页放大，且不还原。
//   桌面看着略大是有意的代价。
const selectStyle: React.CSSProperties = {
  border: "1px solid var(--input-border)",
  borderRadius: 8,
  background: "var(--surface)",
  color: "var(--text-1)",
  padding: "6px 8px",
  fontSize: 16,
  outline: "none",
  fontFamily: "inherit",
};

const HOURS = Array.from({ length: 24 }, (_, i) => i);

export default function ReminderTimeCard() {
  const [pref, setPref] = useState<RemindPref | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");

  useEffect(() => {
    let alive = true;
    // 拿不到就整张不渲染：宁可没有，也不要一个改了没反应的控件
    api.remind.get().then((v) => {
      if (alive && v) setPref(v);
    });
    return () => {
      alive = false;
    };
  }, []);

  const save = useCallback(
    async (hour: number, minute: number) => {
      if (!pref || busy) return;
      if (hour === pref.hour && minute === pref.minute) return;
      setBusy(true);
      setNote("");
      const before = pref;
      // 先乐观更新：网络往返期间下拉不该弹回去
      setPref({ ...before, hour, minute, time: `${pad(hour)}:${pad(minute)}` });
      const v = await api.remind.set(hour, minute);
      setBusy(false);
      if (!v) {
        setPref(before);
        setNote("没保存成功，稍后再试");
        return;
      }
      setPref(v);
      setNote(`已改成每天 ${v.time}`);
      window.dispatchEvent(
        new CustomEvent(REMIND_CHANGED, {
          detail: { time: v.time, hour: v.hour, minute: v.minute },
        }),
      );
    },
    [pref, busy],
  );

  if (!pref) return null;

  return (
    <div className="card card-pad">
      <div
        className="row"
        style={{ justifyContent: "space-between", alignItems: "center", gap: 10 }}
      >
        <span style={{ fontSize: "var(--fs-lg)" }}>提醒时间</span>
        <div className="row" style={{ gap: 4, alignItems: "center" }}>
          <select
            aria-label="提醒小时"
            value={pref.hour}
            disabled={busy}
            onChange={(e) => save(Number(e.target.value), pref.minute)}
            style={selectStyle}
          >
            {HOURS.map((h) => (
              <option key={h} value={h}>
                {pad(h)} 点
              </option>
            ))}
          </select>
          <select
            aria-label="提醒分钟"
            value={pref.minute}
            disabled={busy}
            onChange={(e) => save(pref.hour, Number(e.target.value))}
            style={selectStyle}
          >
            {/* 档位由服务端给（steps）：调度是每 15 分钟一跳，
                前端自己写死四档会和服务端不同步 */}
            {pref.steps.map((m) => (
              <option key={m} value={m}>
                {pad(m)} 分
              </option>
            ))}
          </select>
        </div>
      </div>

      <p className="hint">
        {pref.push_enabled
          ? "浏览器推送和手机日历都按这个时间响。"
          : "这个时间只影响手机日历（这台服务器没开浏览器推送）。"}
        改了之后，订阅的日历会自动跟上（日历自己多久拉一次不确定），
        下载导入的那份不会变，得重新导入。
      </p>

      {note ? (
        <p className="hint" style={{ marginTop: 4 }}>
          {note}
        </p>
      ) : null}
    </div>
  );
}
