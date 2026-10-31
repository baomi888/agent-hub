"use client";

import { useEffect, useState } from "react";

/**
 * 能力开关：检索 / 智能联网。替代原来的四档模式条。
 *
 * ★ 已接线（2026-10-31，批 A/B 之后）。状态不在组件里：页面层用 `usePrefs()` 持有
 *   `useKb/useWeb`（存 `baomi.prefs`），通过 `value` / `onChange` 受控地传进来，
 *   发消息时由 `lib/sse.ts` 拼进请求体的 `use_kb` / `use_web`。
 *   **两个字段必须一起发**——后端 `_resolve_switches` 只要看到一个非 None 就跳过 `mode`，
 *   只发一个时另一个 `None` 会被 `bool()` 压成 `false`，等于静默替用户关掉一项能力。
 *   组件的 `controlled=false` 分支只是给隔离渲染留的退路（探针里会用到），别在页面里用。
 *
 * 形态（2026-10-30 晚重做，第一版是"文字 + 拨动开关轨道"）：
 *   两颗独立的描边药丸，左边图标、右边文字 —— 学 DeepSeek 的「深度思考 / 智能搜索」。
 *   为什么推翻第一版：
 *   · 拨动轨道是**系统设置**的语汇（开关一项配置），不是"这轮带不带资料"的语汇。
 *     用户看到轨道会以为改的是持久偏好，而不是这次提问。
 *   · 轨道塞在文字右边，一眼读成"标签 + 附属小零件"，主体不像按钮，点起来犹犹豫豫。
 *   · 图标能把语义提前：一眼知道"检索"是翻资料、"联网"是全球。纯文字做不到。
 *
 * 三个视觉决定：
 *  1. 关闭态 = **描边**，开启态 = **实底淡金**。轮廓与填充是两种不同的强调方式，
 *     所以它和右边那枚「内测反馈」（金边 + 金底 + 呼吸点）不会撞成三枚一样的金药丸。
 *  2. 点下去**先动图标、再变色**（颜色推迟 90ms 起跑）。图标是"这一下点到了"的即时
 *     反馈，颜色是"状态变了"的结果，两件事分先后看，动作才有因果感。
 *  3. 不加重字重：12→13px 的小字上，600 会让"智能联网"宽出 2px，整行跟着抖。
 *     状态交给底色和颜色就够了。
 */

export interface CapValue {
  /** 检索：从会话所绑的资料库里找依据 */
  kb: boolean;
  /** 智能联网：允许上网查最新信息 */
  web: boolean;
}

/** 默认都不勾：不检索、不联网，纯对话。 */
export const CAP_DEFAULT: CapValue = { kb: false, web: false };

type CapKey = keyof CapValue;

const ITEMS: { key: CapKey; label: string; hint: string; ico: "pop" | "spin" }[] = [
  {
    key: "kb",
    label: "检索",
    hint: "从你资料库的文档里找依据（这个会话要先绑一个库）",
    ico: "pop",
  },
  {
    key: "web",
    label: "智能联网",
    hint: "允许上网查最新信息，答案会附来源",
    ico: "spin",
  },
];

/* 图标画在 16×16 的网格上、1.5px 描边，实际显示 15px。
   两个图标都是"结构线"而不是"实心块"：实心块在淡金底上会糊成一团。 */
function IcoKb() {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M8 2.3 13.4 5 8 7.7 2.6 5Z" />
      <path d="M2.6 8.1 8 10.8l5.4-2.7" />
      <path d="M2.6 11.1 8 13.8l5.4-2.7" />
    </svg>
  );
}

function IcoWeb() {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="8" cy="8" r="5.6" />
      <ellipse cx="8" cy="8" rx="2.5" ry="5.6" />
      <path d="M2.5 8h11" />
    </svg>
  );
}

export default function CapToggles({
  value: controlled,
  defaultValue = CAP_DEFAULT,
  disabled = false,
  onChange,
}: {
  /** 受控值；不传则自己管（当前 ChatArea 走受控，因为下方提示行要跟着变） */
  value?: CapValue;
  defaultValue?: CapValue;
  disabled?: boolean;
  /** 接后端契约时从这里往上传，现在只用来驱动那行提示 */
  onChange?: (next: CapValue) => void;
}) {
  const [inner, setInner] = useState<CapValue>(defaultValue);
  const value = controlled ?? inner;

  /* 动效计数：`n` 递增 → 给图标换 key → 图标重新挂载 → 动画从第 0 帧重放。
     为什么不用 class 切换：同一颗 chip 连点两次，class 一直存在，浏览器不会重播；
     换 key 是唯一能让同一段 keyframes 反复播的办法，代价只有一次节点替换。 */
  const [tap, setTap] = useState<{ key: CapKey | null; n: number }>({ key: null, n: 0 });

  /* 620ms 后撤掉动效类。颜色比图标晚 90ms 起跑、动画本身 460–560ms，
     全部跑完再撤。不撤的话，这颗 chip 之后每一次 hover 也会被推迟 90ms ——
     为了 0.5 秒的仪式感，让所有后续交互都慢半拍，不划算。 */
  useEffect(() => {
    if (!tap.key) return;
    const t = window.setTimeout(() => setTap((s) => ({ ...s, key: null })), 620);
    return () => window.clearTimeout(t);
  }, [tap]);

  const flip = (key: CapKey) => {
    if (disabled) return;
    const next = { ...value, [key]: !value[key] };
    if (!controlled) setInner(next);
    onChange?.(next);
    setTap((s) => ({ key, n: s.n + 1 }));
  };

  return (
    <div className="cap-chips" role="group" aria-label="这轮回答要不要带外部资料">
      {ITEMS.map((it) => {
        const on = value[it.key];
        const active = tap.key === it.key;
        return (
          <button
            key={it.key}
            type="button"
            role="switch"
            aria-checked={on}
            disabled={disabled}
            title={it.hint}
            className={`cap-chip${on ? " on" : ""}${active ? " seq" : ""}`}
            onClick={() => flip(it.key)}
          >
            <span
              key={active ? tap.n : 0}
              className={`cap-ico cap-ico-${it.ico}${active ? " is-tap" : ""}`}
            >
              {it.key === "kb" ? <IcoKb /> : <IcoWeb />}
            </span>
            <span className="cap-txt">{it.label}</span>
          </button>
        );
      })}
    </div>
  );
}
