// 资料检索痕迹：把后端剥出来的「资料块」渲染成默认收起的一行折叠条。
//
// 为什么需要它（2026-11-23 用户反馈）：
//   模型有时把注入给它的资料连标签一起复述进回答正文，用户看到的是一整屏
//   "【资料开始·用户上传】Flume_xxx.docx | score=0.721 | ..."，
//   真正的答案被挤到几屏之外，而且把检索打分这类实现细节漏了出去。
//   后端 core/data_boundary.strip_blocks 已经把这段从正文里摘干净，
//   本组件负责把它放进一个**默认收起**的折叠区：想看就展开，不看就一行。
//
// 设计口径对齐 DeepSeek / 豆包：
//   · 收起时只占一行：「🔍 已参考 2 份资料  ▸」
//   · 展开后是**半透明**小字（过程不该和答案抢视线），限高内部滚动
//   · 不渲染 emoji，全项目统一用手绘风 CornIcon
//   · 资料原文可能很长，逐块再包一层 <details>，默认只露 3 行

import CornIcon from "@/components/CornIcon";
import type { RetrievedBlock } from "./types";

const SOURCE_LABEL: Record<string, string> = {
  kb: "资料库",
  web: "联网",
  upload: "你上传的文件",
};

/** 每块正文收起时显示的行数上限：用 CSS line-clamp，不截字符串，展开还是全文 */
function firstLine(text: string): string {
  const line = (text.split("\n").find((l) => l.trim()) || "").trim();
  return line.length > 42 ? line.slice(0, 42) + "…" : line;
}

export function BlocksTrace({ blocks }: { blocks?: RetrievedBlock[] }) {
  if (!blocks || blocks.length === 0) return null;

  // 按来源归类做摘要：用户更关心"参考了什么"，而不是内部有几种标签
  const bySource = new Map<string, number>();
  for (const b of blocks) {
    bySource.set(b.source, (bySource.get(b.source) || 0) + 1);
  }
  const summary = [...bySource.entries()]
    .map(([src, n]) => `${n} 份${SOURCE_LABEL[src] ?? "资料"}`)
    .join(" · ");

  return (
    <details className="blocks-trace">
      <summary className="blocks-trace-head">
        <CornIcon name="search" />
        <span className="blocks-trace-title">已参考的资料</span>
        <em className="blocks-trace-summary">{summary}</em>
        <span className="hybrid-caret" aria-hidden="true">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M9 6l6 6-6 6" />
          </svg>
        </span>
      </summary>

      <div className="blocks-trace-body">
        {blocks.map((b, i) => (
          <div key={i} className="blocks-item">
            <div className="blocks-item-head">
              <span className="blocks-item-src">{SOURCE_LABEL[b.source] ?? "资料"}</span>
              <span className="blocks-item-preview">{firstLine(b.text)}</span>
            </div>
            <div className="blocks-item-text">{b.text}</div>
            {b.truncated && (
              <div className="blocks-item-more">内容过长，这里只保留开头一部分</div>
            )}
          </div>
        ))}
      </div>
    </details>
  );
}