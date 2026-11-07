"use client";

// 首页「今天」条下面那一行：要不要每天提醒一次。
//
// 为什么排在今天条下面而不是做成浮层：
//   · 浮层已经被「装到桌面」占着（InstallBanner，同一个位置、同一批时机），
//     两个浮层抢同一块屏幕，一定会有一个被挤掉（之前 GuideModal 与
//     InstallBanner 抢过，靠 ARM_DELAY/CONFIRM_DELAY 才压住）。
//   · 更重要的是**人群对齐**：今天条只在「有事要做」时出现，而「每日提醒」
//     也只对这批人有意义——没有任务、没有连续天数的人，提醒内容是空的。
//     两个东西天然同框，比把它放到首页顶部去打扰所有人合理。
//
// 为什么不是一个自动弹出的对话框：通知权限**必须由用户手势触发**。
// 自动弹框再让用户点确认，等于问两次；而且 Chrome 对"非手势"的
// requestPermission 会直接拒绝，那一次拒绝是不可逆的（浏览器级永久）。
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { pushSupport, notificationPermission, enablePush, disablePush, findMyDevice } from "@/lib/push";

// 关掉后多久不再提。比安装提示的 60 天短：提醒开关是可逆的
// （用户随时能在设置里打开），不该因为划走一次就一年不提。
const HIDE_KEY = "baomi.pushNudgeAt";
const HIDE_DAYS = 30;

type View = "loading" | "hidden" | "nudge" | "on" | "denied" | "failed";

function readHidden(): boolean {
  try {
    const at = Number(window.localStorage.getItem(HIDE_KEY) || 0);
    return !!at && Date.now() - at < HIDE_DAYS * 86400_000;
  } catch {
    return false;
  }
}

export default function PushNudge({ enabled }: { enabled: boolean }) {
  const [view, setView] = useState<View>("loading");
  const [detail, setDetail] = useState("");
  const [busy, setBusy] = useState(false);
  /** 提醒时刻，来自 /api/push/status —— 用户可以在设置页改，这里不能写死 */
  const [time, setTime] = useState("");

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    // 浏览器侧先拦：微信内置浏览器 / 未装到主屏的 iOS 都不用问接口。
    // 后者尤其重要——问接口会拿到 supported=true，然后在 requestPermission 处失败，
    // 用户只会看到"打开提醒"点了没反应。
    if (pushSupport() !== "ready") {
      setView("hidden");
      return;
    }
    if (notificationPermission() === "denied") {
      setView("denied");
      return;
    }
    api.push
      .status()
      .then(async (s) => {
        if (!alive) return;
        if (!s || !s.supported) {
          setView("hidden"); // 服务端没配密钥：静默隐藏，别让人点一个必然失败的按钮
          return;
        }
        setTime(s.time || "");
        // ★★ 必须认本机。`s.subscribed` 是账号级的：手机上开了推送，电脑上
        //   这条就会误以为"这台也开了"而收起提示，用户永远等不到那句
        //   "要不要每天提醒一次"——而这正是这台电脑唯一该看到的话。
        const my = await findMyDevice(s.device_list || []);
        if (!alive) return;
        if (my) {
          setView("on");
          return;
        }
        setView(readHidden() ? "hidden" : "nudge");
      })
      .catch(() => {
        if (alive) setView("hidden");
      });
    return () => {
      alive = false;
    };
  }, [enabled]);

  const on = useCallback(async () => {
    setBusy(true);
    const res = await enablePush();
    setBusy(false);
    if (res.ok) {
      setView("on");
      return;
    }
    if (res.reason === "denied") {
      setView("denied");
      return;
    }
    setDetail(res.detail || "");
    setView("failed");
  }, []);

  const off = useCallback(async () => {
    setBusy(true);
    await disablePush();
    setBusy(false);
    setView("nudge");
  }, []);

  const close = useCallback(() => {
    try {
      window.localStorage.setItem(HIDE_KEY, String(Date.now()));
    } catch {
      /* 存不了就这次关掉、下次还会问，可接受 */
    }
    setView("hidden");
  }, []);

  if (!enabled || view === "loading" || view === "hidden") return null;

  if (view === "on") {
    return (
      <div className="push-nudge push-nudge--on">
        <span className="pn-ic" aria-hidden="true">
          <BellIcon />
        </span>
        <span className="pn-txt">
          <span className="pn-main">已开启每日提醒</span>
          <span className="pn-sub">每天 {time || "到点"}，只在你还有事没做完时发</span>
        </span>
        <button type="button" className="pn-link" onClick={off} disabled={busy}>
          关掉
        </button>
      </div>
    );
  }

  if (view === "denied") {
    return (
      <div className="push-nudge">
        <span className="pn-ic pn-ic--mute" aria-hidden="true">
          <BellIcon />
        </span>
        <span className="pn-txt">
          <span className="pn-main">这个浏览器关掉了通知权限</span>
          <span className="pn-sub">点地址栏左边的锁 → 通知 → 允许，再回来打开</span>
        </span>
        <button type="button" className="pn-link" onClick={close}>
          知道了
        </button>
      </div>
    );
  }

  if (view === "failed") {
    return (
      // detail 是浏览器的原始报错（多半是 "AbortError: Registration failed - push service error"），
      // 不给人看（看不懂），挂 title 上：用户截图反馈时能带出来，排查省一问。
      <div className="push-nudge" title={detail}>
        <span className="pn-ic pn-ic--mute" aria-hidden="true">
          <BellIcon />
        </span>
        <span className="pn-txt">
          <span className="pn-main">这台手机收不到网页通知</span>
          {/* ★ 这里原来写的是「换 Edge 浏览器，或把知穗装到桌面后再试」，对安卓是**错的**：
              安卓上所有浏览器（含 Edge 安卓版）的推送底层都是 Google FCM，
              国内连不通，换浏览器不会好。唯一能走通的是系统日历，所以直接把人往那边送。 */}
          <span className="pn-sub">日历也能提醒，去设置里开</span>
        </span>
        <Link href="/settings#reminder" className="pn-btn">
          用日历
        </Link>
      </div>
    );
  }

  return (
    <div className="push-nudge">
      <span className="pn-ic" aria-hidden="true">
        <BellIcon />
      </span>
      <span className="pn-txt">
        {/* 文案长度受实际可用宽度约束：390 宽下 pn-txt 只有约 150px（按钮与 × 各占一段），
            超过 9 个字就会被截成「…」。这句是 8 字 + 9 字，实测刚好放得下。 */}
        <span className="pn-main">每天 {time || "到点"} 提醒我</span>
        <span className="pn-sub">只在你还有事没做完时</span>
      </span>
      <button type="button" className="pn-btn" onClick={on} disabled={busy}>
        {busy ? "开启中…" : "打开提醒"}
      </button>
      <button type="button" className="pn-x" onClick={close} aria-label="不再提示">
        ×
      </button>
    </div>
  );
}

function BellIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M18 8.5a6 6 0 1 0-12 0c0 5-1.6 6.5-1.6 6.5h15.2S18 13.5 18 8.5Z" />
      <path d="M10.3 19a2 2 0 0 0 3.4 0" />
    </svg>
  );
}
