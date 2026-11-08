"use client";

// 首屏三张启动便签：**点一下直接问**，不经过输入框（2026-11-08 激活线）。
//
// 为什么不是「点一下预填」
// ----------------------
// 激活的判据是"发出第一句提问"，不是"输入框里有了字"。原来点完还得再按发送，
// 中间那一步就是流失点：新用户对着一个填好字的输入框，仍然可能不知道要按什么。
//
// 为什么保留 .welcome-egs / .welcome-eg 这套类名
// ---------------------------------------------
// 样式在 app/styles/welcome.css 里，按类名挂的（三列等宽便签 + 长句省略号）。
// 换组件但不换类名，视觉零变动，diff 里只剩"点了之后做什么"这一件事。
//
// 无障碍：button + aria-label 说明"直接提问"，disabled 只在发送中生效
// （正常流程下点完 messages 就有内容，整块欢迎页会被替换掉，轮不到第二次点）。

import { STARTER_PROMPTS } from "@/lib/starterPrompts";

export default function StarterPrompts({
  onPick,
  disabled = false,
}: {
  /** 选中一句：由页面层直接发出去（不预填） */
  onPick: (question: string) => void;
  /** 发送中禁用，避免连点产生两轮请求 */
  disabled?: boolean;
}) {
  return (
    <div className="welcome-egs mt-3 grid w-full grid-cols-1 gap-2 sm:grid-cols-3">
      {STARTER_PROMPTS.map((p) => (
        <button
          key={p.hint}
          type="button"
          onClick={() => onPick(p.question)}
          className="welcome-eg"
          // title 仍然是整句：便签上的 .q 会被 ellipsis 截断，
          // 鼠标悬停要能看到完整的问题，否则用户是在点一句看不全的话
          title={`直接问：${p.question}`}
          aria-label={`直接提问：${p.question}`}
          disabled={disabled}
        >
          <span className="welcome-eg-hint">{p.hint}</span>
          <span className="q">{p.question}</span>
        </button>
      ))}
    </div>
  );
}
