"use client";

// 首屏「继续上次」：最近一次对话，一行。
//
// 为什么放在六格入口**之下**：那六格回答「我还能去做什么」，这一行回答
// 「我刚才做到哪儿了」。后者是打开 App 最高频的意图，但它只对已经有历史的人
// 成立 —— 所以放在最底下，没有历史会话时整块不渲染，首屏与改动前完全一致
// （与「今天」条同一策略：没数据不占位，不拿空卡占着地方）。
//
// 为什么只有一行、而不是报告里说的三条：实测 393×852（安全区 59/34）下，
// 六格最后一张卡的底边在 528、输入区顶边在 658，留给这一块的是 118px。
// 分区标题 21 + 间距 8 + 会话行 40 ≈ 69，留下 43px 呼吸；三条会话要 152px，
// 首页会多出 34px 滚动 —— 等于把"浪费空白"换成"必须滚动"，不划算。
// 更多的历史在侧栏里都在，这里只放"最该接着做的那一件"。
//
// 为什么不做横滑：首页要"一眼看懂"，横滑得先猜到能滑；而横滑等于在纵向
// 滚动容器里再塞一个滚动容器，流式回答期间的手势冲突是已知的老坑。
import CornIcon from "@/components/CornIcon";
import { fmtAgo } from "@/lib/time";

export type ResumeTarget = {
  id: string;
  title: string;
  /** 后端给秒级时间戳，fmtAgo 自己兼容毫秒 */
  updated_at: number;
};

type Props = {
  session: ResumeTarget | null;
  onResume: (sid: string) => void;
};

export default function ResumeStrip({ session, onResume }: Props) {
  if (!session) return null;
  const title = session.title?.trim();
  if (!title) return null; // 刚建的空白会话不值得"继续"，宁可整块不显示

  return (
    <section className="resume-strip w-full" aria-label="继续上次">
      <div className="welcome-section-head">
        <span className="eyebrow">继续上次</span>
        <span className="rule" />
      </div>
      <button type="button" className="resume-row" onClick={() => onResume(session.id)}>
        <span className="rr-mark" aria-hidden="true">
          <CornIcon name="reply" className="h-4 w-4" />
        </span>
        <span className="rr-title">{title}</span>
        <span className="rr-meta">{fmtAgo(session.updated_at)}</span>
        <CornIcon name="arrow" className="rr-go h-3.5 w-3.5" />
      </button>
    </section>
  );
}
