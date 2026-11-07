import type { Metadata } from "next";
import Link from "next/link";
import ReminderSteps from "@/components/reminder/ReminderSteps";
import { FAQ } from "@/lib/reminderGuide";

/* 「设置提醒」的独立说明页。
 *
 * 和 /install 同样的理由：这一页不挂在 (console) 里、也不要求登录，
 * 可以直接当链接发给别人（室友问「你怎么每天到点被提醒」时最省事）。
 *
 * 正文全是静态内容，只有平台识别和 tab 切换是客户端行为（ReminderSteps）。
 */

export const metadata: Metadata = {
  title: "设置提醒 · 知穗Agent",
  description:
    "让知穗每天到点提醒你：iPhone、安卓、电脑三端的分步图文教程。手机上加一条日历订阅，靠系统日历响，断网也提醒。",
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
          <p className="tour-eyebrow">每天提醒</p>
          <h1 className="tour-h1">让知穗到点提醒你</h1>
          <p className="tour-lede">
            每天到你设的那个时刻，知穗会提醒你一次——只在你还有事没做完的时候。手机上是加一条
            日历订阅，由系统日历响，不依赖浏览器、关掉网页也照响；电脑上打开浏览器通知就行。
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
          <p>看完就去设置里把提醒打开，顺便把时间改成你习惯的那个点。</p>
          <Link href="/settings#reminder" className="btn btn-primary btn-sm">
            去设置提醒
          </Link>
        </div>

        <p className="tour-foot">
          iPhone 的截图来自用户真机操作，安卓暂时用的是按系统控件位置绘制的示意图。各家日历的菜单叫法不一样，认「订阅」两个字就行；界面版本不同时，按钮位置可能略有出入。
        </p>
      </div>
    </main>
  );
}
