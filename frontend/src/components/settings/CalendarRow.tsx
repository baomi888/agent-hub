"use client";

// 设置页里的「日历提醒」。
//
// 为什么需要这一行：Web Push 在安卓上走 Google FCM，国内连不通（换浏览器也没用，
// Edge 安卓版同样是 FCM）。系统日历用本机定时器，不吃网络，是目前安卓上唯一
// 能做到「到点手机响」的通道，且不需要装 APK。
//
// 三个操作各自对应一种真实场景，缺一个就会有人卡住：
//   复制订阅链接 —— iPhone / 桌面日历「订阅」用，内容能跟着服务端更新
//   下载日历文件 —— 安卓最快的一条路：点开、选"用日历打开"、导入完事
//                  （国产日历把"从网址订阅"藏在很深的菜单里，多数人找不到）
//   换一个链接   —— 链接不小心发到群里了，换个新的让旧的立刻失效
//
// ★ clipboard 在非 HTTPS 下不可用（本地 http 就是），写失败时不能只说"复制失败"，
//   必须把链接摊出来让人手动选——每条限制后都要跟一条出路。

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { CalendarLink } from "@/lib/types";
import { REMIND_CHANGED } from "@/components/settings/ReminderTimeCard";

export default function CalendarRow() {
  const [link, setLink] = useState<CalendarLink | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  /** clipboard 用不了时降级成"自己选中复制" */
  const [manual, setManual] = useState(false);
  /** 换链接要二次确认：旧链接会立刻失效，误触等于把用户已经导入的日历弄坏 */
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    let alive = true;
    api.calendar.link().then((v) => {
      if (alive) setLink(v);
    });
    return () => {
      alive = false;
    };
  }, []);

  // 上面「提醒时间」卡改了时刻，这里显示的「每天 XX:XX」要立刻跟上。
  // ★ 不去重新拉 /api/calendar/link：token 没变，只是 time 变了，
  //   而重新拉会让下面的订阅链接闪一下（用户可能正在选中复制）。
  useEffect(() => {
    const onChanged = (e: Event) => {
      const detail = (e as CustomEvent<{ time?: string; hour?: number; minute?: number }>).detail;
      if (!detail?.time) return;
      setLink((prev) =>
        prev
          ? {
              ...prev,
              time: detail.time as string,
              hour: detail.hour ?? prev.hour,
              minute: detail.minute ?? prev.minute,
            }
          : prev,
      );
    };
    window.addEventListener(REMIND_CHANGED, onChanged);
    return () => window.removeEventListener(REMIND_CHANGED, onChanged);
  }, []);

  const copy = useCallback(async () => {
    if (!link) return;
    setNote("");
    try {
      await navigator.clipboard.writeText(link.subscribe_url);
      setNote("链接已复制，去日历里粘贴");
      setManual(false);
    } catch {
      // 非 HTTPS、或浏览器禁了剪贴板权限。摊出输入框让人自己选，别只说失败。
      setManual(true);
      setNote("浏览器不给用剪贴板，请手动选中下面的链接复制");
    }
  }, [link]);

  const reset = useCallback(async () => {
    if (!confirming) {
      setConfirming(true);
      setNote("换掉后旧链接立刻失效，已经导入的日历需要重新添加。再点一次确认。");
      return;
    }
    setBusy(true);
    setConfirming(false);
    const v = await api.calendar.reset();
    setBusy(false);
    if (v) {
      setLink(v);
      setNote("已换成新链接");
    } else {
      setNote("没换成功，稍后再试");
    }
  }, [confirming]);

  // 拿不到链接就整行不渲染：宁可没有，也不要一个点了没反应的按钮
  if (!link) return null;

  return (
    <div className="card card-pad">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontSize: "var(--fs-lg)" }}>日历提醒</span>
        <span className="hint" style={{ margin: 0 }}>
          每天 {link.time}
        </span>
      </div>

      <p className="hint">
        {/* 断句按 390 宽下约 22 字/行来分：写成一句 26 字会让"知。"孤零零掉到第三行 */}
        每天 {link.time} 在你手机的日历里响。不依赖浏览器通知。
      </p>

      <div className="row" style={{ gap: 8, marginTop: 10 }}>
        <button type="button" className="btn btn-ghost btn-sm" onClick={copy} disabled={busy}>
          复制订阅链接
        </button>
        <a className="btn btn-ghost btn-sm" href={link.download_url} download>
          下载日历文件
        </a>
      </div>

      <p className="hint" style={{ marginTop: 8 }}>
        安卓：下载后点开，选「用日历打开」导入。iPhone：复制链接，日历 → 添加日历 → 订阅。
      </p>

      {/* 「换链接」放成最下面的一行小字，不进按钮行：它会作废已经导入的日历，
          是这里唯一有副作用、且不可撤销的操作，不该和两个安全操作并排等宽。
          390 宽下三个按钮排不进一行（会掉一个到第二行），这也是分开的理由之一。 */}
      <button
        type="button"
        onClick={reset}
        disabled={busy}
        style={{
          marginTop: 10,
          padding: 0,
          font: "inherit",
          fontSize: "var(--fs-sm)",
          color: confirming ? "var(--danger, #b3261e)" : "var(--text-3)",
          background: "none",
          border: 0,
          textDecoration: "underline",
          textUnderlineOffset: 3,
          // ★ 下划线要跟随文字色（currentColor）。用 var(--line) 那条分隔线色会淡到
          //   看上去只是一个下划线装饰，用户不会意识到这行能点。
          textDecorationColor: "currentColor",
          cursor: "pointer",
        }}
      >
        {confirming ? "再点一次确认换掉（旧的会失效）" : "链接发出去过？换一个链接"}
      </button>

      {manual ? (
        <input
          className="input"
          readOnly
          value={link.subscribe_url}
          onFocus={(e) => e.currentTarget.select()}
          style={{ marginTop: 8, fontSize: "var(--fs-sm)" }}
        />
      ) : null}

      {note ? (
        <p className="hint" style={{ marginTop: 6 }}>
          {note}
        </p>
      ) : null}
    </div>
  );
}
