"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { detectPlatform, subscribePwa, type Platform } from "@/lib/pwa";
import { GUIDE_ORDER, reminderGuide, type RemindFigure } from "@/lib/reminderGuide";
import { REMIND_FIGS } from "./figs";

/* 「设置提醒」的分步教程（/reminder 公开页与 /settings/reminder 应用内页共用）。
 *
 * 和 components/install/TourSteps.tsx 长得像，但**没有合并成一个通用组件**：
 *   · 这边每个步骤的图是可选的（电脑那两步只有文字），那边每步必有图；
 *   · 这边多一个 alt 折叠块（iPhone 才有的「另一条路」），那边没有；
 *   · 合并就要给所有分支加 if，读起来比两份 40 行更难。
 *
 * 三个必须守住的点（同 TourSteps，踩过的坑）：
 *   ① 平台识别只在客户端做：服务端没有 navigator，读会炸。用 useSyncExternalStore，
 *      服务端快照给 "other"，客户端挂载后自己翻。
 *   ② 首帧不渲染步骤列表：否则先按 "other" 渲 2 步、再翻成 iOS 的 5 步，
 *      页面会跳一下。等一个 setTimeout(0)；不能按 typeof document 分流，
 *      那样服务端渲空、客户端首帧有内容，hydration 对不上。
 *   ③ 手动点过 tab 之后不要再被自动识别抢回去（picked 优先）。
 */

const getPlatform = (): Platform => detectPlatform();

export default function ReminderSteps() {
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
    return <div className="tour-steps tour-steps-wait" aria-hidden="true" />;
  }

  const active = picked ?? detected;
  const tabs: Platform[] = active === "other" ? [...GUIDE_ORDER, "other"] : GUIDE_ORDER;
  const guide = reminderGuide(active);
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
              id={`remind-tab-${p}`}
              aria-selected={on}
              aria-controls="remind-panel"
              tabIndex={on ? 0 : -1}
              className="tour-tab"
              onClick={() => setPicked(p)}
            >
              {reminderGuide(p).label}
            </button>
          );
        })}
      </div>

      <section
        className="tour-panel"
        id="remind-panel"
        role="tabpanel"
        aria-labelledby={`remind-tab-${active}`}
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
                {s.fig && <Figure fig={s.fig} caption={s.caption} />}
              </div>
            </li>
          ))}
        </ol>

        <p className="tour-note">{guide.note}</p>

        {guide.alt && (
          <details className="tour-alt">
            <summary>{guide.alt.title}</summary>
            <p>{guide.alt.body}</p>
          </details>
        )}
      </section>
    </>
  );
}

function Figure({ fig, caption }: { fig: RemindFigure; caption?: string }) {
  // 宽扁的图（设置页卡片、日历时间轴）放开到满宽；竖长的图（整屏截图）限 320px，
  // 否则一张手机整屏图在桌面上会被拉到 600px 宽，一屏全是它。
  // 上限再夹一次原图宽度：放大只会变糊。
  const wide = fig.w / fig.h > 1.15;
  const maxW = wide ? Math.min(fig.w, 680) : 320;
  const Draw = "draw" in fig ? REMIND_FIGS[fig.draw] : null;

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
      {caption ? <figcaption className="tour-cap">{caption}</figcaption> : null}
    </figure>
  );
}
