"use client";

/**
 * 「上课提醒」—— 按课表在课前提醒，具体怎么提醒由**设备自己决定**。
 *
 * ## 为什么不是一个开关走到底
 *
 * 同样是「课前 20 分钟提醒」，两台设备的可行路径完全不同：
 *
 * | | iOS / Mac / Windows | 安卓 |
 * |---|---|---|
 * | 系统通知 | 装到主屏后走苹果 APNs，能到 | 底层一律走 Google FCM，国内连不通 |
 * | 系统日历 | 能，但不是用户想要的「弹窗」 | **唯一可靠**：本机定时，断网也响 |
 *
 * 所以对 iOS 主推这里的定时提醒（`/api/course-reminder`），安卓主推日历
 * （`/api/calendar/*.ics`）。两条路后端都保留，前端按平台自动选，
 * 用户不需要知道这些 —— 他打开这一页看到的就是「适合这台手机的那个」。
 *
 * ## 平台判断为什么放在这里而不是后端
 *
 * 后端从 UA 猜「iPhone 还是 iPad」会误伤：iPadOS 13+ 的 UA 写着 Macintosh。
 * 而且「这台设备该看哪个按钮」本来就是浏览器才知道的事，`detectPlatform()`
 * 已经处理了触点数那个特例。
 *
 * ## 提前量存服务端
 *
 * 安卓的提前量只是 URL 上的 `?before=N`，本来可以不落库。但一个人可能白天用
 * 手机、晚上用电脑，客户端各存一份就会出现「在手机选 20、在电脑看到 10」。
 * 存一处、两边读同一个值，代价只有一次 PUT。
 */

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { CalendarLink, CourseReminderState } from "@/lib/types";
import { Switch } from "@/components/console/ui";
import { detectPlatform, type Platform } from "@/lib/pwa";
import {
  enablePush,
  notificationPermission,
  pushSupport,
  type PushSupport,
} from "@/lib/push";

function leadLabel(n: number): string {
  return n === 0 ? "上课那一刻" : `提前 ${n} 分钟`;
}

/** 服务端时间戳（秒）→ `11月16日 07:40`。用户在中国，浏览器时区就是 +8。 */
function stamp(ts: number): string {
  const d = new Date(ts * 1000);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${d.getMonth() + 1}月${d.getDate()}日 ${hh}:${mm}`;
}

/** 通知这条路能不能用、差什么。`null` = 还在读，别下结论。 */
type NotifyState = "ready" | "needs-install" | "denied" | "unsupported" | null;

export default function CourseCalendar() {
  const [state, setState] = useState<CourseReminderState | null>(null);
  const [link, setLink] = useState<CalendarLink | null>(null);
  const [platform, setPlatform] = useState<Platform>("other");
  const [support, setSupport] = useState<PushSupport>("unsupported");
  const [notify, setNotify] = useState<NotifyState>(null);
  const [busy, setBusy] = useState(false);
  const [hint, setHint] = useState("");

  useEffect(() => {
    let alive = true;
    // 平台与通知能力只能在浏览器里读（服务端没有 navigator）。
    // 用 setTimeout(0) 推到下一个宏任务再 setState：effect 里同步 setState
    // 会被 react-hooks/set-state-in-effect 判为级联渲染，项目里已有先例。
    const timer = setTimeout(() => {
      if (!alive) return;
      setPlatform(detectPlatform());
      const s = pushSupport();
      setSupport(s);
      if (s === "ios-needs-install") setNotify("needs-install");
      else if (s !== "ready") setNotify("unsupported");
      else if (notificationPermission() === "denied") setNotify("denied");
      else setNotify("ready");
    }, 0);

    void Promise.all([api.courseReminder.get(), api.calendar.link()])
      .then(([cr, cal]) => {
        if (!alive) return;
        setState(cr);
        setLink(cal);
      })
      .catch(() => {
        // 401 / 端点没部署：整块不出现。设置页不该出现用户处理不了的报错。
      });

    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, []);

  if (!state && !link) return null;

  const isAndroid = platform === "android";
  const lead = state?.lead_minutes ?? 20;
  const leads = state?.leads ?? [0, 5, 10, 15, 20, 30, 45, 60];

  async function save(patch: { enabled?: boolean; lead_minutes?: number }) {
    if (!state) return;
    setBusy(true);
    setHint("");
    try {
      const next = await api.courseReminder.set(patch);
      setState(next);
      if (patch.enabled === true && next.next.length === 0) {
        setHint("课表里还没有课，先去「校园 → 课程表」排好课，这里才会有下一次。");
      }
    } catch {
      setHint("保存没成功，网络似乎不太顺，过一会儿再试一次。");
    } finally {
      setBusy(false);
    }
  }

  async function turnOn() {
    // 打开之前先把系统通知这一步走完。没这一步，后端会照常到点发，
    // 但这台设备收不到 —— 用户看到的是「开了但从来不出现」。
    const res = await enablePush();
    if (!res.ok) {
      if (res.reason === "denied") {
        setNotify("denied");
        setHint("系统把通知权限关掉了。去「设置 → 通知 → 苞米」打开，再回来试。");
      } else {
        setHint(
          res.reason === "no-key"
            ? "通知功能还没在这台设备上配置好，换个时间再试。"
            : "这台设备连不上通知通道。iPhone 请先用 Safari 把它加到主屏幕，再从桌面图标打开。"
        );
      }
      return;
    }
    setNotify("ready");
    await save({ enabled: true });
  }

  // 安卓：日历是主路径。提前量拼在 URL 上，改完要重新加一次日历 ——
  // 日历客户端只在刷新时读 URL，它不会自己变。
  const icsBase = link?.courses_url ?? "";
  const icsSub = icsBase ? `${icsBase}?before=${lead}` : "";
  const icsFile = icsBase ? `${icsBase}?before=${lead}&dl=1` : "";

  return (
    <section className="card card-pad rem-calcard" style={{ marginTop: 12 }}>
      <h3 className="card-title">上课提醒</h3>

      {isAndroid ? (
        <>
          <p className="rem-calplat mt-1">
            安卓手机收不到网页通知（系统限制，换浏览器也一样），所以这里用手机自带的日历：
            课前由系统闹钟叫你，不用开着网页，锁屏、断网也照样提醒。
          </p>

          <div className="row gap-2 mt-3" style={{ alignItems: "center", flexWrap: "wrap" }}>
            <label className="text-[var(--fs-sm)] text-[var(--text-2)]" htmlFor="rem-lead">
              什么时候提醒
            </label>
            <select
              id="rem-lead"
              className="input !w-auto"
              value={lead}
              disabled={busy || !state}
              onChange={(e) => void save({ lead_minutes: Number(e.target.value) })}
            >
              {leads.map((n) => (
                <option key={n} value={n}>
                  {leadLabel(n)}
                </option>
              ))}
            </select>
          </div>

          <div className="row gap-2 mt-3" style={{ flexWrap: "wrap" }}>
            {icsFile ? (
              <a className="btn btn-primary btn-sm" href={icsFile}>
                下载日历文件
              </a>
            ) : null}
            {icsSub ? (
              <a className="btn btn-ghost btn-sm" href={`webcal://${icsSub.replace(/^https?:\/\//, "")}`}>
                用订阅链接（部分日历支持）
              </a>
            ) : null}
          </div>

          <ul className="rem-calnotes mt-3">
            <li>下载后用手机关联的日历打开，导入即可；改完提前量要重新下载一次。</li>
            <li>之后在课程表里改了课，也要重新下载，日历不会自己跟着变。</li>
            <li>导进去是空日历，说明课表还没同步到云端 —— 先去「校园 → 课程表」看一眼。</li>
          </ul>
        </>
      ) : (
        <>
          <p className="rem-calplat mt-1">
            按你的课表，在每节课前提醒你。不用自己一节一节建，改了课表这里自动跟着变。
          </p>

          <div className="rem-remindhead mt-3">
            <div className="rem-step">课前提醒我</div>
            <Switch
              checked={Boolean(state?.enabled)}
              disabled={busy || !state || notify === "needs-install" || notify === "unsupported"}
              label="课前提醒我"
              onChange={(next) => {
                if (next) void turnOn();
                else void save({ enabled: false });
              }}
            />
          </div>

          {notify === "needs-install" ? (
            <p className="rem-calwarn">
              这个浏览器还不支持通知。iPhone 上请点底部分享 →「添加到主屏幕」，
              再从桌面上的图标打开这一页 —— 那样才能收到。
            </p>
          ) : null}
          {notify === "denied" ? (
            <p className="rem-calwarn">
              通知权限被拒绝了。去「设置 → 通知 → 苞米」打开，回到这一页再试。
            </p>
          ) : null}
          {notify === "unsupported" ? (
            <p className="rem-calwarn">
              这个浏览器收不到通知。换成 Safari（iPhone）或 Edge（电脑）再打开这一页。
            </p>
          ) : null}

          <div className="row gap-2 mt-3" style={{ alignItems: "center", flexWrap: "wrap" }}>
            <label className="text-[var(--fs-sm)] text-[var(--text-2)]" htmlFor="rem-lead2">
              什么时候提醒
            </label>
            <select
              id="rem-lead2"
              className="input !w-auto"
              value={lead}
              disabled={busy || !state}
              onChange={(e) => void save({ lead_minutes: Number(e.target.value) })}
            >
              {leads.map((n) => (
                <option key={n} value={n}>
                  {leadLabel(n)}
                </option>
              ))}
            </select>
          </div>

          {state?.next?.length ? (
            <ul className="rem-calnext mt-3">
              {state.next.map((it) => (
                <li key={`${it.fire_at}-${it.title}`}>
                  <b>{stamp(it.fire_at)}</b>
                  <span>{it.title}</span>
                  <i>{it.lead === 0 ? "上课那一刻" : `上课前 ${it.lead} 分钟`}</i>
                </li>
              ))}
            </ul>
          ) : state?.enabled ? (
            <p className="rem-calempty mt-3">
              课表里还没有课。先去「校园 → 课程表」排好课，这里就会出现下一次。
            </p>
          ) : null}

          {hint ? <p className="rem-calwarn">{hint}</p> : null}

          {/* 日历留作备选：有人就是想让它在自带日历里也出现一条 */}
          <details className="rem-caldetails mt-3">
            <summary>也想在手机自带的日历里看到课表</summary>
            <p className="mt-2 text-[var(--fs-sm)] text-[var(--text-3)]">
              两件事可以同时开：上面的提醒是弹一条通知，日历是把整学期课表画进日历。
            </p>
            <div className="row gap-2 mt-2" style={{ flexWrap: "wrap" }}>
              {icsSub ? (
                <a
                  className="btn btn-ghost btn-sm"
                  href={`webcal://${icsSub.replace(/^https?:\/\//, "")}`}
                >
                  加进手机日历
                </a>
              ) : null}
              {icsFile ? (
                <a className="btn btn-ghost btn-sm" href={icsFile}>
                  下载日历文件
                </a>
              ) : null}
            </div>
          </details>
        </>
      )}

      {support === "insecure" ? (
        <p className="rem-calwarn">
          当前地址不是安全连接，通知在这台设备上不可用。用 https 打开才能收到。
        </p>
      ) : null}
    </section>
  );
}
