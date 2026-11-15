"use client";
import { useEffect, useId, useRef, useState } from "react";

type Props = {
  className?: string;
  title?: string;
  /** 思考态：嘴变○、摇摆加快；AI 正在生成时传入 */
  mood?: "idle" | "thinking";
  /** 变化时歪一下头（如切换能力开关）；传会变的标识即可 */
  tiltKey?: string | number;
  /**
   * 手里的道具。默认空手（全站原有的用法都不变）。
   *
   * `"list"` = 举着一张待办清单。用在「一句话整理」的空态：那里要的不是
   * 又一个 logo，而是「把话拆成条目」这件事的执行者。空手的吉祥物放在那儿
   * 只是个装饰，举清单的它才和旁边演示的东西是同一件事。
   */
  holding?: "list";
};

/** 待办清单道具：一条已勾、两条待办。跟着 mascot-body 一起摇，才像"举着" */
function HoldingList() {
  return (
    <g className="mascot-hold">
      {/* 手臂：从身体右侧伸出来。不画这只手的话，纸片会像凭空飘在旁边 */}
      <path d="M80 57 C85.5 55.5 89 56.5 92 59.5" fill="none" stroke="var(--mc-stroke)" strokeWidth={5} />
      {/* 清单本体：斜一点，像随手举着而不是贴在画布上 */}
      <g transform="rotate(-7 101 45)">
        <rect
          x="88"
          y="25"
          width="26"
          height="40"
          rx="5"
          fill="var(--mc-paper)"
          stroke="var(--mc-stroke)"
          strokeWidth={4}
        />
        {/* 第一行是完成态：金勾 + 被划掉的线。品牌金只在"完成"这一处出现 */}
        <path d="M92.5 34.5 l2.6 2.6 l4.6-5.4" fill="none" stroke="#F5B840" strokeWidth={3.2} />
        <path d="M102.5 33.5 h6" stroke="var(--mc-stroke)" strokeWidth={2.8} opacity={0.28} />
        {/* 还没做的两条：只留短横，细节太多在 40px 以下会糊成灰块 */}
        <path d="M92.5 44.5 h5" stroke="var(--mc-stroke)" strokeWidth={2.8} opacity={0.32} />
        <path d="M100.5 44.5 h7" stroke="var(--mc-stroke)" strokeWidth={2.8} opacity={0.32} />
        <path d="M92.5 53.5 h5" stroke="var(--mc-stroke)" strokeWidth={2.8} opacity={0.32} />
        <path d="M100.5 53.5 h7" stroke="var(--mc-stroke)" strokeWidth={2.8} opacity={0.32} />
      </g>
    </g>
  );
}

/**
 * 知穗吉祥物（漫画 Q 版）：会眨眼、会呼吸漂浮、hover 时弹一下。
 *
 * ★ 2026-11-15 第 14 批：颜色全部改由 CSS 变量驱动（`mascot.css` 里定义，
 *   深色在 `.dark .mascot` 覆盖）。原先硬编码的那几个色是按浅色纸调的：
 *   深色下 `currentColor` 变成浅米白 → 描边发光、**眼睛成了两颗白色亮点**，
 *   再叠上 8px 的金色光晕，整只东西看着像一颗亮着的虫蛹 —— 用户的原话是
 *   「看着恐怖」。现在深色走"哑光焦糖脸 + 深棕豆豆眼 + 暖棕描边"，
 *   不再有"内部发光"的观感，眼神也从发光变成有神（双高光）。
 *
 * 交互彩蛋：
 *  - mood="thinking" 时嘴变成○、整体摇摆加快（AI 正在生成）
 *  - mood 从 thinking 回落 idle（生成完成）时自动眨一次眼
 *  - tiltKey 变化时歪一下头（如切换能力开关）
 */
export default function MascotLogo({
  className = "",
  title,
  mood = "idle",
  tiltKey,
  holding,
}: Props) {
  const raw = useId();
  const clip = `cob${raw.replace(/[:]/g, "")}`;
  const [happy, setHappy] = useState(false);
  const [tilt, setTilt] = useState(false);
  const prevMood = useRef(mood);
  const prevTilt = useRef(tiltKey);

  // 思考态回落（生成完成）→ 眨一次眼
  useEffect(() => {
    if (prevMood.current === "thinking" && mood !== "thinking") {
      setHappy(true);
      const t = setTimeout(() => setHappy(false), 750);
      prevMood.current = mood;
      return () => clearTimeout(t);
    }
    prevMood.current = mood;
  }, [mood]);

  // 标识变化（如能力开关切换）→ 歪一下头
  useEffect(() => {
    if (tiltKey !== undefined && prevTilt.current !== tiltKey) {
      prevTilt.current = tiltKey;
      setTilt(true);
      const t = setTimeout(() => setTilt(false), 600);
      return () => clearTimeout(t);
    }
  }, [tiltKey]);

  const cls = [
    "mascot",
    "corn-logo",
    mood === "thinking" ? "thinking" : "",
    happy ? "happy" : "",
    tilt ? "tilt" : "",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={cls} role="img" aria-label={title ?? "知穗吉祥物"} style={{ color: "var(--ink)" }}>
      <svg viewBox="0 0 120 120" width="100%" height="100%" style={{ display: "block", overflow: "visible" }}>
        <g className="mascot-tilt" strokeLinecap="round" strokeLinejoin="round">
          {/* 玉米须（顶端三缕）。深色下单独给它一个更亮的暖色（--mc-silk）：
              和描边同色的话，这三缕在暗纸面上会糊成"三根触角"，剪影也散了 */}
          <path
            d="M52 22 C51 15 55 11 60 10 M60 22 C60 14 63 11 67 9 M67 22 C69 16 72 14 76 13"
            fill="none"
            stroke="var(--mc-silk)"
            strokeWidth={4.2}
          />
          <g className="mascot-body">
            {/* 叶子 */}
            <path
              d="M42 78 C28 80 19 92 17 106 C31 104 42 95 46 84 Z"
              fill="var(--mc-leaf-a)"
              stroke="var(--mc-stroke)"
              strokeWidth={5}
            />
            <path
              d="M78 78 C92 80 101 92 103 106 C89 104 78 95 74 84 Z"
              fill="var(--mc-leaf-b)"
              stroke="var(--mc-stroke)"
              strokeWidth={5}
            />
            {/* 脸 */}
            <rect
              x="37"
              y="24"
              width="46"
              height="68"
              rx="23"
              fill="var(--mc-face)"
              stroke="var(--mc-stroke)"
              strokeWidth={5}
            />
            {/* 玉米粒纹理：深色下压到 0.2（见 mascot.css），太亮会像虫壳上的裂纹 */}
            <clipPath id={clip}>
              <rect x="37" y="24" width="46" height="68" rx="23" />
            </clipPath>
            <g
              className="mascot-grain"
              clipPath={`url(#${clip})`}
              fill="none"
              stroke="var(--mc-grain)"
              strokeWidth={3}
            >
              <path d="M48.5 28 C46 45 46 70 48.5 90" />
              <path d="M71.5 28 C74 45 74 70 71.5 90" />
              <path d="M38 34 C50 38 70 38 82 34" />
              <path d="M38 42 C50 46 70 46 82 42" />
              <path d="M38 76 C50 80 70 80 82 76" />
              <path d="M38 84 C50 88 70 88 82 84" />
            </g>
            {/* 腮红（呼吸） */}
            <g className="mascot-blush">
              <ellipse cx="44.5" cy="64" rx="4.2" ry="2.6" fill="var(--mc-blush)" opacity={0.55} />
              <ellipse cx="75.5" cy="64" rx="4.2" ry="2.6" fill="var(--mc-blush)" opacity={0.55} />
            </g>
            {/* 眼睛 + 双高光（眨眼）。眼白用 --mc-eye：浅色下是墨色，
                深色下是深棕 —— 绝不跟着 --ink 翻白，翻白就是"发光的空洞眼" */}
            <g className="mascot-eye">
              <circle cx="52" cy="57" r="5" fill="var(--mc-eye)" />
              <circle cx="68" cy="57" r="5" fill="var(--mc-eye)" />
              <circle cx="53.7" cy="55.2" r="1.9" fill="var(--mc-eye-shine)" />
              <circle cx="69.7" cy="55.2" r="1.9" fill="var(--mc-eye-shine)" />
              <circle cx="50.6" cy="58.9" r="0.9" fill="var(--mc-eye-shine)" opacity={0.75} />
              <circle cx="66.6" cy="58.9" r="0.9" fill="var(--mc-eye-shine)" opacity={0.75} />
            </g>
            {/* 嘴：默认微笑；思考态换成○ */}
            <path
              className="mascot-mouth-smile"
              d="M52.6 66.4 Q60 73.6 67.4 66.4"
              fill="none"
              stroke="var(--mc-mouth)"
              strokeWidth={3.9}
            />
            <ellipse className="mascot-mouth-o" cx="60" cy="69" rx="3.4" ry="4" fill="none" stroke="var(--mc-mouth)" strokeWidth={4} />
            {/* 脸高光：深色下压到 0.22，原来那道白弧在深底上像镜面反光（湿壳感） */}
            <path className="mascot-shine" d="M45 31 C48 27 53 25 58 25" fill="none" stroke="var(--mc-shine)" strokeWidth={4} />
            {/* 道具放在 body 里：跟着身体一起摇，才像"举在手里" */}
            {holding === "list" ? <HoldingList /> : null}
          </g>
        </g>
      </svg>
    </div>
  );
}
