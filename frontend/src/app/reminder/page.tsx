import type { Metadata } from "next";
import Link from "next/link";
import ReminderSteps from "@/components/reminder/ReminderSteps";
import { FAQ } from "@/lib/reminderGuide";

/* 「打开提醒」的独立说明页。
 *
 * 和 /install 同样的理由：这一页不挂在 (console) 里、也不要求登录，
 * 可以直接当链接发给别人（室友问「你怎么到点被提醒」时最省事）。
 *
 * 正文全是静态内容，只有平台识别和 tab 切换是客户端行为（ReminderSteps）。
 */

export const metadata: Metadata = {
  title: "打开提醒 · 知穗Agent",
  description:
    "让知穗在你要的时间提醒你：iPhone、安卓、电脑三端的分步教程。打开通知权限之后，每条待办到点都会响。",
};

export default function ReminderPage() {
  return (
    <main className="tour">
      <div className="tour-sheet">
        <header className="tour-bar">
          <Link href="/" className="tour-back">
            <span aria-hidden="true">←</span> 回到首页
          </Link>
          <span className="tour-brand">知穗 AGENT</span>
        </header>

        <div className="tour-body">
          <p className="tour-eyebrow">到点提醒</p>
          <h1 className="tour-h1">让知穗到点提醒你</h1>
          <p className="tour-lede">
            每条待办可以单独设提醒时间（「明天 14:30 提醒我」），到点由浏览器通知你。
            打开一次权限就行，之后不用管。
          </p>

          <ReminderSteps />
        </div>

        <section className="tour-faq">
          <h2>常见问题</h2>
          {FAQ.map((item) => (
            <details key={item.q}>
              <summary>{item.q}</summary>
              <p>{item.a}</p>
            </details>
          ))}
        </section>

        <div className="tour-cta">
          <p>权限在「待办 → 设置」里，打开之后点一次「发一条试试」就知道通没通。</p>
          <Link href="/reminders?tab=settings" className="btn btn-primary btn-sm">
            去打开提醒
          </Link>
        </div>

        <p className="tour-foot">
          手机上的通知能力由系统和浏览器决定：iPhone 需要先「添加到主屏」，安卓多数机型在国内收不到——
          这不是本页能绕过的限制，安卓那一页写了怎么确认。
        </p>
      </div>
    </main>
  );
}
