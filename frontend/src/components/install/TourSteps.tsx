"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { detectPlatform, subscribePwa, type Platform } from "@/lib/pwa";
import { GUIDE_ORDER, installGuide, type Figure } from "@/lib/installGuide";
import { INSTALL_FIGS } from "./figs";

/* 装到桌面的分步教程（/install 页面的正文）。
 *
 * 为什么是独立页面而不是弹窗：教程有 3~5 步、每步一张全宽图，塞进弹窗后
 * 手机上只看得见图看不清字。独立页还能当链接发给没注册的人 —— 这正是
 * 「注册前就要看到安装引导」的落点：/install 不要求登录。
 *
 * 三个必须守住的点：
 *  ① 平台自动识别只在客户端做：服务端没有 navigator，直接读会炸；用
 *    useSyncExternalStore 拿，服务端快照给 "other"，客户端挂载后自己翻。
 *  ② 首帧不渲染步骤列表：否则先按 "other" 渲 2 步、再翻成 iOS 的 5 步，
 *    页面会跳一下。等一个 setTimeout(0) 再出，用户看不见这一帧。
 *    不能写 `if (typeof document === "undefined")` 分流 —— 那样服务端渲空、
 *    客户端第一次渲染就有内容，hydration 对不上。
 *  ③ 用户手动点过 tab 之后，就不要再被自动识别抢回去（picked 优先）。
 */

const getPlatform = (): Platform => detectPlatform();

export default function TourSteps() {
  const [ready, setReady] = useState(false);
  const [picked, setPicked] = useState<Platform | null>(null);
  const tabsRef = useRef<(HTMLButtonElement | null)[]>([]);

  useEffect(() => {
    // 异步回调里 setState 不触发 react-hooks/set-state-in-effect
    const t = window.setTimeout(() => setReady(true), 0);
    return () => window.clearTimeout(t);
  }, []);

  const detected = useSyncExternalStore(subscribePwa, getPlatform, () => "other" as Platform);

  if (!ready) {
    // 占位高度按最小的一套（2 步）给，避免加载完把下面内容顶下去太多
    return <div className="tour-steps tour-steps-wait" aria-hidden="true" />;
  }

  const active = picked ?? detected;
  const tabs: Platform[] = active === "other" ? [...GUIDE_ORDER, "other"] : GUIDE_ORDER;
  const guide = installGuide(active);
  const idx = tabs.indexOf(active);

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const next = (idx + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    setPicked(tabs[next]);
    tabsRef.current[next]?.focus();
  };

  return (
    <>
      <div className="tour-tabs" role="tablist" aria-label="选择你的设备" onKeyDown={onKey}>
        {tabs.map((p, i) => {
          const on = p === active;
          return (
            <button
              key={p}
              ref={(el) => {
                tabsRef.current[i] = el;
              }}
              type="button"
              role="tab"
              id={`tour-tab-${p}`}
              aria-selected={on}
              aria-controls="tour-panel"
              tabIndex={on ? 0 : -1}
              className="tour-tab"
              onClick={() => setPicked(p)}
            >
              {installGuide(p).label}
            </button>
          );
        })}
      </div>

      <section
        className="tour-panel"
        id="tour-panel"
        role="tabpanel"
        aria-labelledby={`tour-tab-${active}`}
      >
        <div className="tour-summary">
          <h2>{guide.title}</h2>
          <span className="tour-count">共 {guide.steps.length} 步</span>
        </div>
        {guide.prereq && <p className="tour-prereq">{guide.prereq}</p>}

        <ol className="tour-steps">
          {guide.steps.map((s, i) => (
            <li className="tour-step" key={s.title}>
              <span className="tour-no" aria-hidden="true">
                {i + 1}
              </span>
              <div className="tour-step-body">
                <h3 className="tour-step-title">
                  <span className="tour-sr">第 {i + 1} 步：</span>
                  {s.title}
                </h3>
                <p className="tour-step-desc">{s.desc}</p>
                <Figure fig={s.fig} caption={s.caption} />
              </div>
            </li>
          ))}
        </ol>

        <p className="tour-note">{guide.note}</p>
      </section>
    </>
  );
}

function Figure({ fig, caption }: { fig: Figure; caption: string }) {
  // 宽扁的图（电脑截图）放开到满宽，竖长的图（手机截图）限 320px，
  // 否则一张手机截图在桌面上会被拉到 600px 宽，一屏全是它。
  // 上限再夹一次原图宽度：截图放大只会变糊，宁可小一点。
  const wide = fig.w / fig.h > 1.15;
  const maxW = wide ? Math.min(fig.w, 680) : 320;
  const Draw = "draw" in fig ? INSTALL_FIGS[fig.draw] : null;

  return (
    <figure className="tour-fig">
      <div
        className="tour-fig-frame"
        style={{ aspectRatio: `${fig.w} / ${fig.h}`, maxWidth: maxW }}
      >
        {Draw ? (
          <Draw />
        ) : (
          // eslint-disable-next-line @next/next/no-img-element -- 静态裁剪图，宽高已知，不需要 next/image 的按需转码
          <img
            src={"shot" in fig ? fig.shot : ""}
            alt={fig.alt}
            width={fig.w}
            height={fig.h}
            loading="lazy"
            decoding="async"
          />
        )}
      </div>
      <figcaption className="tour-cap">{caption}</figcaption>
    </figure>
  );
}
