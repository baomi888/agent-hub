"use client";

/* 落地页：未登录时访问 / 看到的那一页。
 *
 * 为什么要它：以前未登录直接就是登录卡片。第一次从搜索结果或别人发的链接
 * 进来的人，看到一张登录框，除了"要注册"什么也不知道 —— 没有账号的人
 * 也不会为了看懂这是什么而先注册。落地页负责在要账号之前把话说清楚。
 *
 * ★ 视觉方向（一句话）：**摊开的一页手账**。
 *   不是"又一个 AI 产品页"：没有暗底、没有紫渐变、没有发光网格。
 *   整页就是一张暖米黄的纸，标题用文楷写在页眉，真实界面像照片一样贴在
 *   右下角，区块之间用装订虚线和页码分隔。
 *   理由：这个产品的品牌语言本来就是「苞米地笔记本」（tokens.css 的三级纸色
 *   + 玉米金 + 手写感衬线）。落地页如果换成通用 AI 风，等于告诉访客
 *   "点进来之后的样子和这里不一样"。
 *
 * ★ 首屏骨架：「画面即界面」。文字块只占左边约 45%，真实界面不是并排的另一半，
 *   而是一张斜贴上去的照片压在右下角 —— 两者是斜向关系，不是左右分栏。
 *   （左右分栏是模型的默认，oil-ui 要求一轮最多用一个并写明理由；这里不用。）
 *
 * ★ 滚动叙事（至少一段滑动驱动）：首屏固定一段滚动距离，文字往上退、
 *   那张贴着的照片被推近放大 14% 并摆正 —— 从"手账的一角"推到"这张照片
 *   本身"，正好接力到下一节的开头：它究竟凭什么这么答。
 *   其余区块是滑动触发（once），出场方式按材质选：便签是带重量落下后稳住，
 *   时间轴是先画线再落点。
 */

import Link from "next/link";
import { useEffect, useRef } from "react";
import MascotLogo from "@/components/chat/MascotLogo";
import TourSteps from "@/components/install/TourSteps";
import ReminderSteps from "@/components/reminder/ReminderSteps";
import { DAY, FACTS, LP_FAQ, NOTES, TOC } from "@/lib/landingContent";

export default function Landing() {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const heroRef = useRef<HTMLElement | null>(null);
  const copyRef = useRef<HTMLDivElement | null>(null);
  const shotRef = useRef<HTMLElement | null>(null);
  const tocRef = useRef<HTMLElement | null>(null);
  const mascotRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const hero = heroRef.current;
    const copy = copyRef.current;
    const shot = shotRef.current;
    let alive = true;
    let mm: { add: (q: string, fn: () => void) => void; revert: () => void } | null = null;

    // 兜底闸门：GSAP 迟迟没到（弱网、被拦、脚本报错）就把 .lp-reveal 直接放出来。
    // 没有这一层，首屏以下的区块会永远停在 opacity:0 —— 页面看着是半空的。
    const bail = window.setTimeout(() => root.classList.add("lp-static"), 1800);

    (async () => {
      try {
        const [g, st] = await Promise.all([import("gsap"), import("gsap/ScrollTrigger")]);
        if (!alive) return;
        window.clearTimeout(bail);
        const { gsap } = g;
        const ScrollTrigger = st.ScrollTrigger ?? st.default;
        gsap.registerPlugin(ScrollTrigger);

        // ★ scroller 必须是 .lp 自己：全站 html/body 是 overflow:hidden，
        //   页面级滚动发生在内部容器里，ScrollTrigger 默认盯 window，会全程不动。
        const scroller = root;
        mm = gsap.matchMedia();

        mm.add("(prefers-reduced-motion: reduce)", () => {
          root.classList.add("lp-static");
        });

        mm.add("(prefers-reduced-motion: no-preference)", () => {
          // ---------- 首屏景深（滑动驱动，可倒放） ----------
          if (hero && copy && shot) {
            const tl = gsap.timeline({
              scrollTrigger: {
                trigger: hero,
                scroller,
                start: "top top",
                end: "bottom top",
                scrub: 0.6,
              },
            });
            // 目录和封面上的吉祥物跟着文字一起退：它们都属于"封面上的字"，
            // 不该比那张照片活得久
            const words = [copy, tocRef.current, mascotRef.current].filter(
              Boolean
            ) as Element[];
            tl.to(words, { y: -84, opacity: 0.06, ease: "none" }, 0).to(
              shot,
              { scale: 1.14, rotation: 0, y: -26, ease: "none" },
              0
            );
          }

          // ---------- 普通区块：克制的统一出场 ----------
          gsap.utils.toArray<HTMLElement>(".lp-reveal", scroller).forEach((node) => {
            gsap.fromTo(
              node,
              { y: 26, opacity: 0 },
              {
                y: 0,
                opacity: 1,
                duration: 0.62,
                ease: "power2.out",
                scrollTrigger: { trigger: node, scroller, start: "top 90%", once: true },
              }
            );
          });

          // ---------- 便签：带重量落下后稳住 ----------
          const notes = gsap.utils.toArray<HTMLElement>(".lp-note", scroller);
          if (notes.length) {
            gsap.fromTo(
              notes,
              { y: 34, opacity: 0, rotation: -1.1 },
              {
                y: 0,
                opacity: 1,
                rotation: 0,
                duration: 0.68,
                ease: "back.out(1.3)",
                stagger: 0.09,
                scrollTrigger: {
                  trigger: notes[0].parentElement,
                  scroller,
                  start: "top 84%",
                  once: true,
                },
              }
            );
          }

          // ---------- 时间轴：连接线先画出来，节点随后落位 ----------
          const rail = scroller.querySelector<HTMLElement>(".lp-rail");
          const stops = gsap.utils.toArray<HTMLElement>(".lp-stop", scroller);
          if (rail && stops.length) {
            const trigger = { trigger: rail.parentElement, scroller, start: "top 84%", once: true };
            gsap.fromTo(
              rail,
              { scaleY: 0 },
              { scaleY: 1, duration: 0.85, ease: "power2.inOut", transformOrigin: "top center", scrollTrigger: trigger }
            );
            gsap.fromTo(
              stops,
              { opacity: 0, x: -10 },
              { opacity: 1, x: 0, duration: 0.42, ease: "power2.out", stagger: 0.13, delay: 0.18, scrollTrigger: trigger }
            );
          }
        });
      } catch {
        window.clearTimeout(bail);
        root.classList.add("lp-static");
      }
    })();

    return () => {
      alive = false;
      window.clearTimeout(bail);
      mm?.revert();
    };
  }, []);

  return (
    <div className="lp" ref={rootRef}>
      {/* 桌面上的点阵 + 两侧页边的竖排字号（见 landing.css 的 .lp-margin）。
          纯装饰，屏幕小时整段不渲染出可见像素，所以 aria-hidden 且不进 DOM 语义 */}
      <div className="lp-margin" aria-hidden="true">
        <span className="lp-margin-l">知穗 Agent · 苞米地里的学习工作台</span>
        <span className="lp-margin-r">www.myzhisui.cn</span>
      </div>

      {/* ---------- 页眉 ---------- */}
      <header className="lp-nav">
        <div className="lp-nav-inner">
          <Link href="/" className="lp-brand" aria-label="知穗 Agent 首页">
            <MascotLogo className="lp-brand-mark" />
            <span>知穗 Agent</span>
          </Link>

          <nav className="lp-links" aria-label="页面内容">
            <a href="#cite">它怎么回答</a>
            <a href="#day">一天的样子</a>
            <a href="#install">装到桌面</a>
            <a href="#reminder">到点提醒</a>
            <a href="#faq">常见问题</a>
          </nav>

          <div className="lp-nav-act">
            <Link href="/login" className="lp-signin">
              登录
            </Link>
            <Link href="/login" className="btn btn-primary lp-start">
              开始使用
            </Link>
          </div>
        </div>
      </header>

      {/* ---------- 封面：摊开的一页 ---------- */}
      <section className="lp-sheet lp-cover" ref={heroRef}>
        <div className="lp-hero-copy" ref={copyRef}>
          <p className="lp-eyebrow">给大学生的 AI 学习工作台</p>
          <h1 className="lp-h1">
            资料它读过，
            <br />
            今天它排好
          </h1>
          <p className="lp-lede">
            课件、笔记、真题传进来，它先翻原文再回答，每句后面都挂着出处。一学期的目标拆成每天做得完的几件——
            晚上它还会问一句：今天学了什么。
          </p>
          <div className="lp-actions">
            <Link href="/login" className="btn btn-primary lp-btn-lg">
              开始使用
            </Link>
            <a href="#install" className="btn btn-ghost lp-btn-lg">
              先看看怎么装
            </a>
          </div>
          <p className="lp-hero-note">一个登录名、一个密码就能开始，不用邮箱验证。</p>
        </div>

        {/* 吉祥物站在文字和目录之间那块空白里 —— 用户说"这片白有点空"，
            补一株苞米比补一段文案更像这一页该有的东西。
            ★ 它是这一页唯一会动的东西，动的是"呼吸 + 眨眼 + 一点漂浮"，
              不是弹跳招手的动：那种动会让人以为吉祥物本身是个按钮。
              hover 才弹一下（组件自带），所以它确实"可以点"，但不主动讨点击。 */}
        <div className="lp-mascot" ref={mascotRef} aria-hidden="true">
          <span className="lp-mascot-shadow" />
          <MascotLogo className="lp-mascot-fig" />
          <span className="lp-spark lp-spark-1" />
          <span className="lp-spark lp-spark-2" />
        </div>

        {/* 贴在右下角的实拍：真机截图，不是画的。
            斜着放是因为它像"夹在本子里的一张"，摆正了就变成产品截图陈列。 */}
        <figure className="lp-paste" ref={shotRef}>
          <img
            src="/landing/checkin.webp"
            alt="知穗Agent 的今日打卡卡片：数据结构·树与二叉树，遍历与平衡标为已掌握，线索化标为待加强"
            width={430}
            height={443}
            loading="eager"
            decoding="async"
          />
          <figcaption>今天的打卡卡片 · 只讲知识结构，不带姓名学号</figcaption>
        </figure>

        {/* 封面左下角原本是一片空白：文字到按钮就结束，右侧照片却一直压到页脚，
            整个重心塌向右边（用户看到的就是那块白）。
            这里补一份目录 —— 手账本来就该有目录，它既填掉那片白，
            也让第一次进来的人一眼看到这页有几节、能直接跳。
            ★ 序号用「一二三四」而不是 01/02：页脚那两位是页码，
              两套数字挨在一起会被读成"第几页"。 */}
        <nav className="lp-toc" aria-label="本页目录" ref={tocRef}>
          <p className="lp-toc-head">这页里面</p>
          <ol className="lp-toc-list">
            {TOC.map((t) => (
              <li key={t.href}>
                <a href={t.href}>
                  <span className="lp-toc-no">{t.no}</span>
                  <span className="lp-toc-t">{t.t}</span>
                </a>
              </li>
            ))}
          </ol>
        </nav>

        <div className="lp-page-mark" aria-hidden="true">
          <span>01</span>
          <span className="lp-page-hint">往下翻</span>
        </div>
      </section>

      {/* ---------- 正文：内页 ---------- */}
      <div className="lp-sheet lp-body">
        {/* 三条事实 */}
        <ul className="lp-facts lp-reveal">
          {FACTS.map((f) => (
            <li key={f.k}>
              <b>{f.k}</b>
              <span>{f.v}</span>
            </li>
          ))}
        </ul>

        {/* 核心能力：先翻资料再开口 */}
        <section className="lp-sec" id="cite">
          <p className="lp-eyebrow lp-reveal">它怎么回答</p>
          <h2 className="lp-h2 lp-reveal">先翻你的资料，再开口</h2>
          <p className="lp-body-txt lp-reveal">
            把 PDF、Markdown、txt 拖进资料库，或者直接粘一个网址。它把这些东西切段、整理成可检索的库；
            你提问时先检索，把最接近的几段连同出处一起交给你。
          </p>

          <figure className="lp-shot lp-reveal">
            <div className="lp-shot-frame">
              <img
                src="/landing/cite.webp"
                alt="回答下方的参考资料卡片：sample.txt，相似度 0.766，强度标为「强」，可展开原文"
                width={1243}
                height={586}
                loading="lazy"
                decoding="async"
              />
            </div>
            <figcaption>
              参考资料里每一条都带相似度和强度，点「查看全文」能核对整段原文。
            </figcaption>
          </figure>

          <ul className="lp-points lp-reveal">
            <li>切段大小和重叠可以调，导完不满意还能「重新整理」一遍。</li>
            <li>检索到的材料撑不住问题时，它会直接标「依据较弱」，不硬答。</li>
            <li>问过的每个结论都能回到原文件的那一段，不用你再去翻 PDF。</li>
          </ul>
        </section>

        {/* 四张便签 */}
        <section className="lp-sec">
          <p className="lp-eyebrow lp-reveal">对话之外</p>
          <h2 className="lp-h2 lp-reveal">它还替你管着这几件事</h2>
          <div className="lp-notes">
            {NOTES.map((n) => (
              <article className="lp-note" key={n.tag}>
                <span className="lp-note-tag">{n.tag}</span>
                <h3>{n.title}</h3>
                <p>{n.desc}</p>
              </article>
            ))}
          </div>
        </section>

        {/* 一天的样子 */}
        <section className="lp-sec" id="day">
          <p className="lp-eyebrow lp-reveal">一天的样子</p>
          <h2 className="lp-h2 lp-reveal">不是多了一个聊天框，是多了一个节奏</h2>
          <div className="lp-day">
            <div className="lp-rail" aria-hidden="true" />
            <ol className="lp-day-list">
              {DAY.map((d) => (
                <li className="lp-stop" key={d.time}>
                  <span className="lp-stop-time">{d.time}</span>
                  <span className="lp-stop-dot" aria-hidden="true" />
                  <div className="lp-stop-body">
                    <b>{d.when}</b>
                    <p>{d.what}</p>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </section>

        {/* 装到桌面：教程本身就在这一页，不用再点走 */}
        <section className="lp-sec lp-install" id="install">
          <p className="lp-eyebrow lp-reveal">装到桌面</p>
          <h2 className="lp-h2 lp-reveal">下次不用再搜网址</h2>
          <p className="lp-body-txt lp-reveal">
            苹果、安卓、电脑三套装法不一样，下面按你的设备自动选好了。照着一图一步做，两三分钟。
          </p>
          <div className="lp-install-body">
            <TourSteps />
          </div>
          <p className="lp-install-more">
            想发给别人或者收藏起来？<Link href="/install">这一页有单独的教程链接 ›</Link>
          </p>
        </section>

        {/* 到点提醒：和「装到桌面」同一个做法——教程内嵌，不让人再点走。
            为什么放在装到桌面之后：iPhone 上想收系统通知，前提就是先装到主屏，
            读的顺序正好是"先装、再开提醒"。 */}
        <section className="lp-sec lp-remind" id="reminder">
          <p className="lp-eyebrow lp-reveal">到点提醒</p>
          <h2 className="lp-h2 lp-reveal">到点它叫你一声</h2>
          <p className="lp-body-txt lp-reveal">
            每条待办可以单独设提醒时间：「明天 14:30 交高数作业」。到点由浏览器通知你，
            打开一次权限就行。iPhone 装到主屏、电脑用 Edge 就能收；安卓多数机型在国内收不到，教程里写了怎么确认。
          </p>
          <div className="lp-install-body">
            <ReminderSteps />
          </div>
          <p className="lp-install-more">
            想发给别人或者收藏起来？<Link href="/reminder">这一页有单独的教程链接 ›</Link>
          </p>
        </section>

        {/* 常见问题 */}
        <section className="lp-sec" id="faq">
          <p className="lp-eyebrow lp-reveal">常见问题</p>
          <h2 className="lp-h2 lp-reveal">你可能想问的</h2>
          <div className="lp-faq lp-reveal">
            {LP_FAQ.map((item) => (
              <details key={item.q}>
                <summary>{item.q}</summary>
                <p>{item.a}</p>
              </details>
            ))}
          </div>
        </section>

        {/* 收尾 */}
        <section className="lp-cta">
          <h2>先传一份课件试试</h2>
          <p>把它这学期的专业课笔记放进去，问一个你一直没搞懂的地方。</p>
          <Link href="/login" className="btn btn-primary lp-btn-lg">
            开始使用
          </Link>
        </section>

        <footer className="lp-foot">
          <span>知穗 Agent · 苞米地里的学习工作台</span>
          <nav aria-label="页脚">
            <Link href="/install">装到桌面</Link>
            <Link href="/reminder">设置提醒</Link>
            <Link href="/login">登录</Link>
          </nav>
          {/* 联系方式贴在最底下、独占一行：这是外部访客唯一能直接找到人的
              地方（反馈 bug、想合作、想投稿都从这儿来）。用 mailto，
              点了直接起邮件客户端，不用先复制再切 App */}
          <a className="lp-mail" href="mailto:3059591171@qq.com">
            <span>联系我</span>
            <strong>3059591171@qq.com</strong>
          </a>
        </footer>
      </div>
    </div>
  );
}
