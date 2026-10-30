"use client";

import { useState } from "react";

/**
 * 能力开关：检索 / 智能联网。替代原来的四档模式条。
 *
 * ★ 当前只是**视觉**，没有接线。原因：后端契约（`use_kb` / `use_web`）还没上线，
 *   前端不知道该发什么字段，先接线就是死代码 + 假承诺。
 *   所以这里的状态完全留在组件内，点了**不会影响这一轮怎么答**。
 *   等 `POST /api/chat/stream` 收下两个布尔值，再把 `onChange` 接到页面状态、
 *   去掉内部 useState 即可，DOM 与样式一行不用动。
 *
 * 三个视觉上的决定：
 *  1. 两个开关装在同一枚胶囊里（和原模式条同款），而不是两个独立按钮 ——
 *     它们是一组「这轮要不要带外部资料」的设置，不是一个动作按钮。
 *     也让浅金描边的「内测反馈」胶囊继续是那一行唯一像按钮的东西。
 *  2. 状态由**金色轨道**承担，标签只在开的时候变深金加粗。
 *     整枚胶囊不变色：两枚金色药丸并排会互相抢，而金在这套设计里只该是点缀。
 *  3. 整枚 chip 就是按钮（标签 + 轨道一起点），热区 ≈ 76×22，
 *     不用为了「够大」把轨道撑成 44px 高、把这一行顶高。
 */
export interface CapValue {
  /** 检索：从会话所绑的资料库里找依据 */
  kb: boolean;
  /** 智能联网：允许上网查最新信息 */
  web: boolean;
}

/** 默认都不勾：不检索、不联网，纯对话。 */
export const CAP_DEFAULT: CapValue = { kb: false, web: false };

const ITEMS: { key: keyof CapValue; label: string; hint: string }[] = [
  { key: "kb", label: "检索", hint: "从你资料库的文档里找依据（这个会话要先绑一个库）" },
  { key: "web", label: "智能联网", hint: "允许上网查最新信息，答案会附来源" },
];

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

  const flip = (key: keyof CapValue) => {
    const next = { ...value, [key]: !value[key] };
    if (!controlled) setInner(next);
    onChange?.(next);
  };

  return (
    <div className="cap-toggles" role="group" aria-label="这轮回答要不要带外部资料">
      {ITEMS.map((it) => {
        const on = value[it.key];
        return (
          <button
            key={it.key}
            type="button"
            role="switch"
            aria-checked={on}
            disabled={disabled}
            title={it.hint}
            className={`cap-tog${on ? " on" : ""}`}
            onClick={() => flip(it.key)}
          >
            <span className="cap-tog-label">{it.label}</span>
            <span className="cap-track" aria-hidden="true">
              <span className="cap-knob" />
            </span>
          </button>
        );
      })}
    </div>
  );
}
