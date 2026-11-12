"use client";

/**
 * 「上课提醒」—— 把课表加进手机自带的日历。
 *
 * ## 为什么是日历，而不是再做一条推送
 *
 * 安卓的网页通知底层一律走 Google FCM，国内连不通，换浏览器也不解决
 * （`lib/push.ts` 顶部有完整说明）。**系统日历是唯一不依赖任何推送通道的路**：
 * 日历 App 用本机 AlarmManager 定时，断网、关浏览器、后台被冻结都照响。
 * 后端 `courses.ics` 每条 VEVENT 都带 VALARM（2026-11-12 补的），
 * 所以订阅之后「课前几分钟响」是手机自己算的，跟我们有没有发出去无关。
 *
 * ## 和 2026-11-11 删掉的那个「日历提醒」不是一回事
 *
 * 当时删的是**每天一条固定时刻的摘要**（20:00 那条），它和「每条待办各自设时间」
 * 天生对不上：用户把提醒改到 14:30，日历里 20:00 的摘要照旧 —— 说了假话。
 * 课表没有这个问题：每门课的真实上课时间本来就在数据里，日历里那条就是事实。
 * 后端 `/api/calendar/*` 从那时起就一直留着，注释写着「课表订阅共用同一套 token」，
 * 只是前端入口被一起删掉了 —— 这张卡把它接回来。
 *
 * ## 两个必须说出来的限制
 *
 * 1. **安卓多数日历不认 `webcal://`**，只能下载 .ics 导入一次，改课表后要重新导入。
 *    不告诉他这件事，他会以为"订了就一直自动更新"。
 * 2. **课表没上云就是空日历**。订阅不等于有数据，先去「校园 → 课程表」确认。
 */
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { CalendarLink } from "@/lib/types";

/** 提前量只给这几档：0 是"正点响"，再大就没人在课前 45 分钟想被叫起来 */
const LEAD_OPTIONS = [0, 5, 10, 15, 30];

function leadLabel(n: number): string {
  return n === 0 ? "上课时" : `提前 ${n} 分钟`;
}

export default function CourseCalendar() {
  /** null = 还没读到 / 端点不存在 / 请求失败。三种都表现为"整块不出现" */
  const [link, setLink] = useState<CalendarLink | null>(null);
  const [read, setRead] = useState(false);
  const [lead, setLead] = useState(10);

  useEffect(() => {
    let alive = true;
    void api.calendar
      .link()
      .then((v) => {
        if (!alive) return;
        setLink(v);
        setRead(true);
      })
      .catch(() => {
        // 401/网络错误：整块不出现。这里不该弹提示 —— 用户在设置页
        // 看到的应该只有"能用的东西"，而不是一条他没法处理的报错。
        if (alive) setRead(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  if (!read || !link) return null;

  // 提前量落在 URL 上：同一份课表不同的人要不同的提前量，而订阅是「一个 URL 一份日历」。
  // 改完之后要重新订阅（日历客户端只在刷新时读 URL，不会自己变）。
  const withLead = (base: string): string => `${base}?before=${lead}`;
  const download = `${link.courses_url}?before=${lead}&dl=1`;

  return (
    <section className="card card-pad rem-calcard" style={{ marginTop: 12 }}>
      <h3 className="card-title">上课提醒</h3>
      <p className="mt-1 text-[var(--fs-xs)] text-[var(--text-3)]">
        把课表加进手机自带的日历，课前由系统闹钟提醒。不用开着网页，
        锁屏、断网也照样响。
      </p>

      <div
        className="row gap-2 mt-3"
        style={{ alignItems: "center", flexWrap: "wrap" }}
      >
        <label
          className="text-[var(--fs-sm)] text-[var(--text-2)]"
          htmlFor="rem-lead"
        >
          什么时候响
        </label>
        <select
          id="rem-lead"
          className="input !w-auto"
          value={lead}
          onChange={(e) => setLead(Number(e.target.value))}
        >
          {LEAD_OPTIONS.map((n) => (
            <option key={n} value={n}>
              {leadLabel(n)}
            </option>
          ))}
        </select>
      </div>

      <div className="row gap-2 mt-3" style={{ flexWrap: "wrap" }}>
        {/* href 用 webcal://：iOS / macOS 点一下弹「订阅日历」，之后自动更新。
            安卓多数日历不认这个 scheme，所以旁边必须留下载这条路。 */}
        <a className="btn btn-primary btn-sm" href={withLead(link.courses_webcal_url)}>
          加进手机日历
        </a>
        <a className="btn btn-ghost btn-sm" href={download}>
          下载日历文件
        </a>
      </div>

      <ul className="rem-calnotes mt-3">
        <li>
          iPhone / Mac 用「加进手机日历」会直接订阅，以后在课程表里改课，
          一天内自动同步。
        </li>
        <li>
          安卓多数日历不认订阅链接，只能用「下载日历文件」导入一次；
          之后再改课表，要重新下载一份。
        </li>
        <li>
          导进去是空日历，说明课表还没同步到云端 —— 先去「校园 → 课程表」里看一眼。
        </li>
      </ul>
    </section>
  );
}
