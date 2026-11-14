import type { Metadata } from "next";
import Link from "next/link";
import PageShell from "@/components/console/PageShell";
import ReminderSteps from "@/components/reminder/ReminderSteps";
import { FAQ } from "@/lib/reminderGuide";

/* 应用内的「打开提醒」教程页（待办 → 设置 → 看分步教程）。
 *
 * 为什么不直接跳公开页 /reminder：那一页是独立页（套 .tour 整页纸、自带"回到首页"），
 * 从应用里点进去左导航会整个消失，感觉像退出了应用。所以做成 (console) 组里的子路由，
 * 外壳不动，点左导航随时能切走。
 *
 * 两页内容同源（ReminderSteps + FAQ 都来自 lib/reminderGuide.ts），
 * 改教程只改数据层，两边一起变——不要在这一页另写一份步骤。
 */

export const metadata: Metadata = {
  title: "打开提醒 · 知穗Agent",
};

export default function SettingsReminderPage() {
  return (
    <PageShell
      title="打开提醒"
      sub="苹果要先装到主屏才收得到；安卓多数机型在国内收不到推送。"
      actions={
        <Link href="/reminders?tab=settings" className="btn btn-ghost btn-sm">
          去打开提醒
        </Link>
      }
    >
      <div className="card card-pad">
        <ReminderSteps />
      </div>

      <section>
        <p className="sec-label">常见问题</p>
        {/* is-embedded：整页版里 .tour-faq 自带 20px 内边距（那是纸张的边距），
            嵌进卡片后再套一层就是双份缩进 */}
        <div className="tour-faq is-embedded">
          {FAQ.map((item) => (
            <details key={item.q}>
              <summary>{item.q}</summary>
              <p>{item.a}</p>
            </details>
          ))}
        </div>
      </section>

      <p className="hint">
        想把教程发给别人？公开版在{" "}
        <Link
          href="/reminder"
          style={{
            color: "var(--accent-ink)",
            textDecoration: "underline",
            textDecorationColor: "var(--accent-line)",
            textUnderlineOffset: 3,
          }}
        >
          /reminder
        </Link>{" "}
        —— 那一页不需要登录，没注册的人也能看。
      </p>
    </PageShell>
  );
}
