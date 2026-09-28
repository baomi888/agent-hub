"use client";
import React from "react";

type Props = {
  className?: string;
  title?: string;
};

/**
 * 苞米吉祥物（漫画 Q 版）：会眨眼、会呼吸漂浮、hover 时弹一下。
 * 描边走 currentColor，外层 color 取 --ink，明暗主题自动适配；
 * 脸 / 叶 / 腮红为固定色，在浅纸与深夜两底都站得住。
 */
export default function MascotLogo({ className = "", title }: Props) {
  const raw = React.useId();
  const clip = `cob${raw.replace(/[:]/g, "")}`;
  return (
    <div
      className={`mascot corn-logo ${className}`}
      role="img"
      aria-label={title ?? "苞米吉祥物"}
      style={{ color: "var(--ink)" }}
    >
      <svg viewBox="0 0 120 120" width="100%" height="100%" style={{ display: "block", overflow: "visible" }}>
        <g strokeLinecap="round" strokeLinejoin="round">
          {/* 玉米须 */}
          <path
            d="M52 22 C51 15 55 11 60 10 M60 22 C60 14 63 11 67 9 M67 22 C69 16 72 14 76 13"
            fill="none"
            stroke="currentColor"
            strokeWidth={4}
          />
          <g className="mascot-body">
            {/* 叶子 */}
            <path d="M42 78 C28 80 19 92 17 106 C31 104 42 95 46 84 Z" fill="#7C9A54" stroke="currentColor" strokeWidth={5} />
            <path d="M78 78 C92 80 101 92 103 106 C89 104 78 95 74 84 Z" fill="#8FAF62" stroke="currentColor" strokeWidth={5} />
            {/* 脸 */}
            <rect x="37" y="24" width="46" height="68" rx="23" fill="#F5B840" stroke="currentColor" strokeWidth={5} />
            {/* 玉米粒纹理 */}
            <clipPath id={clip}>
              <rect x="37" y="24" width="46" height="68" rx="23" />
            </clipPath>
            <g clipPath={`url(#${clip})`} fill="none" stroke="currentColor" strokeWidth={3} opacity={0.28}>
              <path d="M48.5 28 C46 45 46 70 48.5 90" />
              <path d="M71.5 28 C74 45 74 70 71.5 90" />
              <path d="M38 34 C50 38 70 38 82 34" />
              <path d="M38 42 C50 46 70 46 82 42" />
              <path d="M38 76 C50 80 70 80 82 76" />
              <path d="M38 84 C50 88 70 88 82 84" />
            </g>
            {/* 腮红（呼吸） */}
            <g className="mascot-blush">
              <ellipse cx="44.5" cy="64" rx="4.2" ry="2.6" fill="#EF8A56" opacity={0.55} />
              <ellipse cx="75.5" cy="64" rx="4.2" ry="2.6" fill="#EF8A56" opacity={0.55} />
            </g>
            {/* 眼睛 + 高光（眨眼） */}
            <g className="mascot-eye">
              <circle cx="52" cy="57" r="4.6" fill="currentColor" />
              <circle cx="68" cy="57" r="4.6" fill="currentColor" />
              <circle cx="53.6" cy="55.4" r="1.7" fill="#FFFDF6" />
              <circle cx="69.6" cy="55.4" r="1.7" fill="#FFFDF6" />
            </g>
            {/* 嘴 */}
            <path d="M53.5 67 Q60 73 66.5 67" fill="none" stroke="currentColor" strokeWidth={4} />
            {/* 脸高光 */}
            <path d="M45 31 C48 27 53 25 58 25" fill="none" stroke="#FFFFFF" strokeWidth={4} opacity={0.5} />
          </g>
        </g>
      </svg>
    </div>
  );
}
