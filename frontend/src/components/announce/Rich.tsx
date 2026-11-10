"use client";

/**
 * 公告正文的行内强调。
 *
 * 只认两种标记，理由不是"实现简单"，而是**排版权限**：
 *   **加粗**    → 这个词是重点
 *   ==着色==    → 这一处变了
 *
 * 不引 markdown 库：一整篇公告只会用到这两样，为它拉一个解析器
 * 反而多出一堆本篇用不到的节点类型（列表、表格、链接），
 * 而且 markdown 的 `**` 语义是"强调"不是"加粗"，落到中文排版上还会带斜体。
 *
 * 不支持嵌套：`**a==b==c**` 会被拆成两段而不是套起来。
 * 这是有意的——写公告的时候不该在这种地方花心思。
 */

import type { ReactNode } from "react";

/**
 * 一次扫完，避免先替换 `**` 再替换 `==` 时互相吃掉。
 * ★ 必须放在函数里而不是模块级：带 g 的正则会把 lastIndex 挂在对象上，
 *   模块级那份被所有实例共用，一旦哪次 exec 中途退出，下次进来的起点就错了。
 */
function tokens() {
  return /\*\*([^*]+)\*\*|==([^=]+)==/g;
}

export default function Rich({ text }: { text: string }) {
  const out: ReactNode[] = [];
  const re = tokens();
  let last = 0;
  let m: RegExpExecArray | null;

  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1] !== undefined) {
      out.push(
        <strong className="ann-strong" key={m.index}>
          {m[1]}
        </strong>
      );
    } else {
      out.push(
        <span className="ann-em" key={m.index}>
          {m[2]}
        </span>
      );
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));

  return <>{out}</>;
}
