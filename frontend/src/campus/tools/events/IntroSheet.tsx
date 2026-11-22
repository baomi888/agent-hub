"use client";

/**
 * 使用说明：第一次进来弹一次，之后从标题右侧的「使用说明」重开。
 *
 * 写这份说明的原因不是「功能复杂需要教」，而是这个工具的边界容易被误解：
 * 它长得像一份「赛事通知」，学生很容易以为上面的日期就是官方日期、点进去就能报名。
 * 所以说明里必须同时讲清两件事 —— 怎么用（四条），以及它不做什么（一段强调）。
 *
 * 四条的顺序是从「看」到「处置」：先知道哪里有东西，再知道空的表示什么，
 * 最后才是两个动作（不感兴趣 / 置顶）。顺序反了，用户会先看到按钮却不知道该点点什么。
 */

import { useEffect } from "react";
import { lockPageScroll } from "../../logic/scrollLock";

interface IntroSheetProps {
  onClose: () => void;
}

const STEPS: { title: string; body: string }[] = [
  {
    title: "有色格子 = 这一天有已核实的安排",
    body: "点任意一格，看当天有哪几件，再点某一件看它要什么条件、适合谁。",
  },
  {
    title: "格子空着，不代表这个月没事",
    body: "只有月份精度的事（「本月开放报名」这种）列在日历旁的「本月清单」里，不按天拆 —— 日期年年浮动，硬拆到某一天是假精确。",
  },
  {
    title: "用不上的，点「不感兴趣」",
    body: "它立刻从日历上消失，之后不再挡路。想找回来，去「我的」点「放回年历表」。",
  },
  {
    title: "怕忘的，点「置顶提醒」",
    body: "置顶后文案能改，改成你要做的那一步更有用 —— 比如「问学长数模队还缺不缺人」。",
  },
];

export default function IntroSheet({ onClose }: IntroSheetProps) {
  useEffect(() => {
    const release = lockPageScroll();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      release();
    };
  }, [onClose]);

  return (
    <div
      className="campus-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="campus-ev-intro-title"
    >
      <button type="button" className="campus-modal-mask" aria-label="关闭使用说明" onClick={onClose} />
      <div className="campus-modal-card campus-ev-intro">
        <div className="campus-modal-head">
          <div>
            <p className="campus-ev-intro-kicker">第一次用 · 30 秒看完</p>
            <h2 id="campus-ev-intro-title" className="campus-ev-detail-title">
              这个年历表怎么用
            </h2>
          </div>
          <button type="button" className="campus-modal-close" aria-label="关闭" onClick={onClose}>
            ×
          </button>
        </div>

        <p className="campus-ev-intro-lead">
          它只做一件事：把全年值得关注的比赛和考证排在时间轴上，让你知道「原来还有这回事、大概什么时候」。
          报不报、怎么报，看完这里再去官网。
        </p>

        <ol className="campus-ev-intro-list">
          {STEPS.map((step) => (
            <li key={step.title}>
              <b>{step.title}</b>
              <span>{step.body}</span>
            </li>
          ))}
        </ol>

        <p className="campus-ev-intro-caution">
          这里帮的是<strong>信息差</strong>，不是报名服务。名额、资格、具体日期一律以赛事官网、
          本校教务处 / 团委通知为准 —— 我们只负责提醒你「有这回事」，剩下的请你亲自去核对。
        </p>

        <div className="campus-ev-intro-actions">
          <button type="button" className="campus-btn is-primary" onClick={onClose}>
            知道了，开始看
          </button>
          <span className="campus-ev-intro-foot">之后可从上方「使用说明」重新打开</span>
        </div>
      </div>
    </div>
  );
}
