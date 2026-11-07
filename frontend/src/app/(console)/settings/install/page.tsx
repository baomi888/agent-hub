import type { Metadata } from "next";
import Link from "next/link";
import PageShell from "@/components/console/PageShell";
import TourSteps from "@/components/install/TourSteps";
import { FAQ } from "@/lib/installGuide";

/* 应用内的「装到桌面」教程页（设置 → 装成 App → 看分步教程）。
 *
 * 为什么不直接跳到公开页 /install：
 *   /install 是**独立页**（app/install/page.tsx，套 .tour 整页纸、自带"回到首页"），
 *   它存在的理由是"注册前 / 发给别人"——不登录也能看。但用户从设置里点进去时，
 *   左导航会整个消失，感觉像退出了应用，想回去还得靠浏览器返回键。
 *   所以这里做成 (console) 组里的子路由：外壳不动，点左导航随时能切走。
 *
 * 两页的内容是同一套（TourSteps + FAQ 都来自 lib/installGuide.ts），
 * 改教程只改数据层，两边一起变；不要在这一页另写一份步骤文案。
 *
 * 这一页是 Server Component：正文没有交互状态，只有 TourSteps 自己是客户端组件
 * （平台识别与 tab 切换在里面）。页头和卡片沿用控制台现有的 .pg-wrap / .card。
 */

export const metadata: Metadata = {
  title: "装到桌面 · 知穗Agent",
};

export default function SettingsInstallPage() {
  return (
    <PageShell
      eyebrow="装成 App"
      title="装到桌面"
      sub="一步一图，电脑、安卓、苹果分开写。装好后从桌面图标点开就是全屏工作台，没有地址栏。"
      actions={
        <Link href="/settings" className="btn btn-ghost btn-sm">
          回到设置
        </Link>
      }
    >
      <div className="card card-pad">
        <TourSteps />
      </div>

      <section>
        <p className="sec-label">常见问题</p>
        {/* is-embedded：整页版里 .tour-faq 自带 0 20px 的内边距（那是纸张自己的边距），
            嵌进卡片后再套一层就成了双份缩进 */}
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
        <Link href="/install" style={{ color: "var(--accent-ink)", textDecoration: "underline", textDecorationColor: "var(--accent-line)", textUnderlineOffset: 3 }}>
          /install
        </Link>{" "}
        —— 那一页不需要登录，没注册的人也能看。
      </p>
    </PageShell>
  );
}
