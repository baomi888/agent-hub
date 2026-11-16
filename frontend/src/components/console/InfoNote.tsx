"use client";

// 页头说明的「一句话 + ⓘ」形态（2026-11-16 P1：说明文下沉）。
//
// 为什么不是直接删：这几句都是 R2（去掉会让用户做一个错误的决定）——
// 「不露个人内容」「回答先从资料库找依据」删了，用户就不知道自己交出去的是什么。
// 但每页 2–4 行、占掉首屏 60px，讲的又是"首次进入该听一次"的事。
//
// 所以拆成两半：
//   claim  —— 不超过 20 字的主张，常驻。它是"现在就该知道的那一句"。
//   detail —— 原文，收进 ⓘ。
//
// detail 是**按需挂载**（open 才渲染），不是 display:none：
// 藏起来的那份不进无障碍树、也不占布局，量高度时不会骗人。
import { useId, useState, type ReactNode } from "react";
import CornIcon from "@/components/CornIcon";

type Props = {
  /** 常驻的一句话主张（≤20 字，由 test_page_notes.py 钉住） */
  claim: ReactNode;
  /** 展开后显示的完整说明 */
  detail: ReactNode;
  /** 无障碍名；默认「展开说明」 */
  label?: string;
  className?: string;
};

export default function InfoNote({ claim, detail, label = "展开说明", className }: Props) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <div className={className ? `info-note ${className}` : "info-note"}>
      <div className="info-note-row">
        <p className="pg-sub">{claim}</p>
        <button
          type="button"
          className="info-note-btn"
          aria-expanded={open}
          aria-controls={id}
          aria-label={open ? "收起说明" : label}
          onClick={() => setOpen((v) => !v)}
        >
          <CornIcon name="info" className="h-4 w-4" />
        </button>
      </div>
      {open ? (
        <p className="info-note-detail" id={id} role="note">
          {detail}
        </p>
      ) : null}
    </div>
  );
}
