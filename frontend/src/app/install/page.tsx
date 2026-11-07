import type { Metadata } from "next";
import Link from "next/link";
import TourSteps from "@/components/install/TourSteps";
import { FAQ } from "@/lib/installGuide";

/* 「装到桌面」的独立说明页。
 *
 * 为什么要有这一页（而不是只有设置页里那个弹窗）：
 *   - 用户第一次搜到 myzhisui.cn 时还没注册，登录门后面什么也看不到，
 *     装到桌面这件事必须**在注册之前**就讲清楚，否则他下次还得再搜一遍网址；
 *   - 一张图一步的教程塞进弹窗看不清，独立页能把图放到全宽；
 *   - 这一页不挂在 (console) 里、也不要求登录，可以直接当链接发出去。
 *
 * 正文全是静态内容，只有平台识别和 tab 切换是客户端行为（TourSteps）。
 */

export const metadata: Metadata = {
  title: "装到桌面 · 知穗Agent",
  description:
    "把知穗Agent装到桌面：iPhone、安卓、电脑三端的分步图文教程。装完从图标打开是全屏的，没有地址栏，不用每次再输网址。",
};

export default function InstallPage() {
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
          <p className="tour-eyebrow">装成 App</p>
          {/* 标题必须点出产品名：这一页多数是冷启动进来的（搜索结果 / 别人转的链接），
              光写"装一次就进"读者还得靠下面的 tab 猜这是什么 App */}
          <h1 className="tour-h1">把知穗装到桌面</h1>
          <p className="tour-lede">
            装一次，以后点图标就进，不用再到浏览器里搜网址。打开是全屏的、没有地址栏，和装了个
            App 一样；它还是同一个网站，登录状态和数据都不变。
          </p>

          <TourSteps />
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
          {/* 这一页不要求登录，读者可能还没有账号，所以不写"回来接着用" */}
          <p>装好了，从桌面图标点开就是工作台。</p>
          <Link href="/" className="btn btn-primary btn-sm">
            去工作台
          </Link>
        </div>

        <p className="tour-foot">
          教程里的截图来自 iPhone（Safari）与 Windows（Edge）实机操作，安卓暂时用的是按系统控件位置绘制的示意图。界面版本不同时，按钮位置可能略有出入，名字是一样的。
        </p>
      </div>
    </main>
  );
}
