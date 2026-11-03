/**
 * 番茄钟提示音 · 预设与参数（纯数据 + 纯函数）
 *
 * 为什么用 Web Audio 实时合成，而不是打包几个 mp3：
 *  1. 体积 —— 五个音色加起来不到 1 KB 代码；mp3 至少几百 KB，还要处理缓存与跨域；
 *  2. 版权 —— 合成音是算出来的，不涉及采样授权；
 *  3. 可调 —— 音量、时长、泛音都能按参数改，手机外放和耳机需要的亮度不一样。
 *
 * 「不刺耳」是硬指标，不是形容词：
 *  提示音响起的时机是「刚结束 25 分钟专注」，人从沉浸里被拉出来时对高频最敏感。
 *  所以所有音色统一遵守三条 —— 不用方波/锯齿波（谐波多、听着尖）；
 *  一律加低通削掉 4 kHz 以上；起音 12ms 渐入、尾音 1.5–3 秒指数衰减。
 *  默认音色取主流做法里的三音上行琶音（C5 → E5 → G5）。
 */

import type { ChimeId } from "../types";

export interface ChimeNote {
  /** 基频 Hz */
  freq: number;
  /** 相对开始的延迟（秒） */
  at: number;
  /** 持续时长（秒） */
  duration: number;
  /** 单音峰值音量 0–1，再乘主音量 */
  gain: number;
  wave: OscillatorType;
  /** 滑音目标频率；水滴用，其余不填 */
  glideTo?: number;
  /** 泛音倍数：叠一个更弱的高八度，钟声更「实」而不是更响 */
  overtone?: number;
}

export interface ChimePreset {
  id: ChimeId;
  label: string;
  /** 一句听感描述，选择面板里显示 */
  hint: string;
  notes: readonly ChimeNote[];
  /** 低通截止（Hz） */
  lowpass: number;
}

/**
 * 五个内置音色。
 *
 * 共同点：峰值音量都很低（0.03–0.22）。合成音的「响」靠包络和泛音堆出来，
 * 把 gain 拉高只会炸耳朵 —— 外放时 0.2 已经足够叫醒一个房间里的人。
 */
export const CHIME_PRESETS: readonly ChimePreset[] = [
  {
    id: "bell",
    label: "晨钟",
    hint: "三音上行，尾巴长，最像下课的钟",
    lowpass: 4200,
    notes: [
      { freq: 523.25, at: 0, duration: 3.0, gain: 0.18, wave: "sine", overtone: 2 },
      { freq: 659.25, at: 0.18, duration: 2.8, gain: 0.16, wave: "sine", overtone: 2 },
      { freq: 783.99, at: 0.36, duration: 2.8, gain: 0.14, wave: "sine", overtone: 2 },
    ],
  },
  {
    id: "marimba",
    label: "木琴",
    hint: "短促、干爽，不拖泥带水",
    lowpass: 3200,
    notes: [
      { freq: 698.46, at: 0, duration: 0.9, gain: 0.22, wave: "triangle" },
      { freq: 880.0, at: 0.11, duration: 0.9, gain: 0.2, wave: "triangle" },
      { freq: 1046.5, at: 0.22, duration: 1.0, gain: 0.18, wave: "triangle" },
    ],
  },
  {
    id: "musicbox",
    label: "八音盒",
    hint: "轻、亮、音量最低，适合图书馆",
    lowpass: 6000,
    notes: [
      { freq: 783.99, at: 0, duration: 1.8, gain: 0.1, wave: "sine", overtone: 2 },
      { freq: 987.77, at: 0.16, duration: 1.8, gain: 0.09, wave: "sine", overtone: 2 },
      { freq: 1174.66, at: 0.32, duration: 2.0, gain: 0.08, wave: "sine", overtone: 2 },
    ],
  },
  {
    id: "wind",
    label: "风铃",
    hint: "几个高音错开响，像窗外有风",
    lowpass: 7000,
    notes: [
      { freq: 1174.66, at: 0, duration: 3.2, gain: 0.05, wave: "sine" },
      { freq: 1479.98, at: 0.09, duration: 3.0, gain: 0.045, wave: "sine" },
      { freq: 1760.0, at: 0.24, duration: 3.4, gain: 0.04, wave: "sine" },
      { freq: 2349.32, at: 0.41, duration: 2.6, gain: 0.03, wave: "sine" },
    ],
  },
  {
    id: "drop",
    label: "水滴",
    hint: "一声下滑，最安静的一个",
    lowpass: 2400,
    notes: [{ freq: 880.0, at: 0, duration: 0.55, gain: 0.22, wave: "sine", glideTo: 392 }],
  },
  {
    id: "none",
    label: "静音",
    hint: "只弹提醒，不出声",
    lowpass: 0,
    notes: [],
  },
];

const PRESET_MAP = new Map(CHIME_PRESETS.map((preset) => [preset.id, preset]));

/** 按 id 取预设；未知 id 返回 undefined（由调用方回落到默认） */
export function findChime(id: string): ChimePreset | undefined {
  return PRESET_MAP.get(id as ChimeId);
}

export const DEFAULT_CHIME: ChimeId = "bell";

/** 音量夹到 0–1；非法值回落到 0.6（外放清楚、耳机不炸的中间值） */
export function clampVolume(value: number): number {
  if (!Number.isFinite(value)) return 0.6;
  return Math.min(1, Math.max(0, value));
}

/**
 * 判断一个音色 id 是否有效。
 * 存进去的 id 可能是旧版本遗留（比如已下线的 `custom`）或用户手改的，读出来要能兜住。
 */
export function isChimeId(value: unknown): value is ChimeId {
  return typeof value === "string" && PRESET_MAP.has(value as ChimeId);
}
