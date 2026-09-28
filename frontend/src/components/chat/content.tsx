// 消息正文渲染：URL 自动 linkify + **粗体** + 换行；引用 refs 文本解析
// 从 ChatArea.tsx 拆出（2026-09 重构），全部是纯函数，适合单测
import type { ReactNode } from "react";

const URL_RE = /https?:\/\/[^\s<>"{}|\\^`\[\]]+/gi;

export function splitByUrl(text: string): Array<{ type: "text" | "url"; value: string }> {
  const out: Array<{ type: "text" | "url"; value: string }> = [];
  let rest = text;
  while (rest) {
    const m = URL_RE.exec(rest);
    if (!m) {
      out.push({ type: "text", value: rest });
      break;
    }
    if (m.index > 0) out.push({ type: "text", value: rest.slice(0, m.index) });
    out.push({ type: "url", value: m[0] });
    rest = rest.slice(m.index + m[0].length);
    URL_RE.lastIndex = 0;
  }
  return out;
}

export function renderContent(text: string): ReactNode {
  return text.split("**").map((part, i) => {
    const isBold = i % 2 === 1;
    const children = splitByUrl(part).map((seg, j) => {
      if (seg.type === "url") {
        return (
          <a
            key={j}
            href={seg.value}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-link"
            title={seg.value}
          >
            {seg.value}
          </a>
        );
      }
      const lines = seg.value.split("\n").map((line, k, arr) => (
        <span key={k}>
          {line}
          {k < arr.length - 1 && <br />}
        </span>
      ));
      return <span key={j}>{lines}</span>;
    });
    return isBold ? <strong key={i}>{children}</strong> : <span key={i}>{children}</span>;
  });
}

// ============ 引用角标：解析后端 refs 字符串 ============
// 格式：**[1]** 文件名.pdf 第2页 ｜ score=0.812 ｜ 预览片段...
export interface RefItem {
  idx: string;
  source: string;
  score: string;
  preview: string;
}

export function parseRefs(refs?: string): RefItem[] {
  if (!refs) return [];
  return refs
    .split(/\n{2,}/)
    .map((s) => s.trim())
    .filter(Boolean)
    .map((line) => {
      const m = line.match(/^\*\*\[(\d+)\]\*\*\s*([\s\S]*)$/);
      const idx = m?.[1] ?? "";
      const parts = (m?.[2] ?? line).split("｜").map((s) => s.trim());
      const source = parts[0] ?? "";
      const scorePart = parts.find((s) => s.startsWith("score="));
      return {
        idx,
        source,
        score: scorePart ? scorePart.replace("score=", "") : "",
        preview: parts.filter((s) => s !== source && s !== scorePart).join(" "),
      };
    })
    .filter((r) => r.idx);
}
