"use client";

// 「提醒」在设置页只剩这一张小卡（2026-11-18）。
//
// ## 来龙去脉
//
//   · 11-11：整组提醒设置（权限、设备清单、邮件兜底、课程日历、免打扰）搬进了
//     「待办 → 设置」，设置页只留一行灰字指路 —— 理由是"两个地方都能开关同一件事，
//     迟早出现'在这关了那边还显示开着'"。
//   · 11-18：用户看不懂那行灰字。原话是「这个用户不知道是什么意思」，指着
//     「提醒权限、已开启提醒的设备，都在「待办 → 设置」里」。
//     这是指路文案的经典失败：句子在描述一个地址，而用户要的是一个开关。
//
// ## 现在的分工
//
//   这里：**这台设备响不响** —— 一个开关 + 一行状态 + 一步进详细设置。
//         这是唯一一件用户会在设置页临时想起来的事（"我这台怎么不响"）。
//   详细设置（/reminders?tab=settings）：设备清单、邮件兜底、课程日历、
//         每日回顾、免打扰 —— 一年改一次，不值得占设置页的版面。
//
// ## 为什么"两个地方能开关"这次可以接受
//
//   两边都不是本地状态：开关读的是 `GET /api/push/status` + `findMyDevice()`，
//   写的是浏览器的 `pushManager.subscribe/unsubscribe`（服务端那份跟着变）。
//   所以不存在"这边关了那边还显示开着" —— 除非页面停留着没刷新，
//   而那是所有远端状态的共同前提，不是这一处的问题。
//
// ## 三个不能含糊的地方（和待办页那张卡同源）
//
//   ① 开关管的是**本机**，不是账号。`subscribed` 是账号级的，直接拿它渲染
//      「已开启」，在从没开过的电脑上就是假话。
//   ② 别的设备只能关不能开（碰不到那台浏览器），所以清单不给这里，只在页脚报个数。
//   ③ 关掉 ≠ 删数据。文案必须这么说，否则用户以为待办没了。
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import {
  pushSupport,
  notificationPermission,
  enablePush,
  disablePush,
  findMyDevice,
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

export default function ReminderQuick() {
  const [view, setView] = useState<View>("loading");
  const [alive, setAlive] = useState<PushDevice[]>([]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");

  const refresh = useCallback(async () => {
    const sup = pushSupport();
    if (sup === "insecure") return setView("insecure");
    if (sup === "ios-needs-install") return setView("ios");
    if (sup !== "ready") return setView("unsupported");
    if (notificationPermission() === "denied") return setView("denied");

    const s = await api.push.status();
    // `status` 返回 null 是**请求失败**，不是"浏览器不支持"。
    // 混成 unsupported 会对着一个其实能收通知的浏览器说"你收不到"。
    if (!s) return setView("error");
    if (!s.supported) return setView("unsupported");

    const devices = s.device_list || [];
    const my = await findMyDevice(devices);
    setAlive(devices.filter((d) => !d.disabled));
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
          if (res.detail) console.warn("[push] 订阅失败：", res.detail);
          setNote("连不上通知服务，这台暂时收不到");
        }
      } else {
        // 本机退订时浏览器那边也要撤，否则服务端删了行、浏览器还留着悬空订阅
        await disablePush();
        await refresh();
        setNote("已关掉这台。待办和提醒都还在。");
      }
      setBusy(false);
    },
    [busy, refresh],
  );

  const toggleable = view === "on" || view === "off" || view === "other";
  const otherCount = alive.length;

  // 一行说完"这台响不响"。比待办页那张卡短一档：这里不是主场地，
  // 长句会把它变成一段正文，而用户只是路过看了一眼。
  const hint = (() => {
    switch (view) {
      case "loading":
        return "正在看这台设备的通知权限…";
      case "error":
        return "没读到通知状态，刷新试试。";
      case "insecure":
        return "当前是 http 地址，浏览器不给通知权限。";
      case "ios":
        return "iPhone 要先「添加到主屏」，从主屏打开才有通知。";
      case "unsupported":
        return "这个浏览器收不到通知（安卓的网页推送走 Google 服务）。";
      case "denied":
        return "通知被拒过，要在地址栏的锁里改成「允许」。";
      case "on":
        return "开着。到点会通知这台设备。";
      case "other":
        return `这台还没开，提醒发往另外 ${otherCount} 台。`;
      default:
        return "打开后，到点会通知这台设备。";
    }
  })();

  return (
    <section className="card card-pad">
      <div className="card-head">
        <span className="card-title">这台设备的提醒</span>
        <Switch
          checked={view === "on"}
          label="这台设备的提醒"
          disabled={busy || !toggleable}
          onChange={(next) => void toggle(next)}
        />
      </div>

      <p className="hint" style={{ marginTop: 6 }}>
        {hint}
      </p>
      {note ? (
        <p className="hint" style={{ marginTop: 4 }}>
          {note}
        </p>
      ) : null}

      {/* 整行可点：左边报个数（"我有几台在收"），右边一步进详细设置。
          以前这一行是一句 12 字的灰字，用户读完了还不知道能不能点。 */}
      <Link href="/reminders?tab=settings" className="rem-quick-foot">
        <span className="meta">
          {otherCount > 0 ? `${otherCount} 台设备已开启提醒` : "还没有设备开启提醒"}
        </span>
        <span className="rem-quick-more">
          详细设置
          <span aria-hidden="true">›</span>
        </span>
      </Link>
    </section>
  );
}
