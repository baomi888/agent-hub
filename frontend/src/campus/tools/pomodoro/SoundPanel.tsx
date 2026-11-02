"use client";

/**
 * 提示音设置面板（折叠在计时卡片里）。
 *
 * 为什么用原生 `<details>` 而不是自己写展开状态：
 *  - 手机上 `<summary>` 自带点击热区与键盘可达性，不用补一堆 ARIA；
 *  - 折叠状态不占 React 状态，切换工具时不留残影。
 *
 * 组件本身不发出声音，只把「用户想要什么」上报给容器，
 * 播放统一走 logic/audio.ts —— 避免每个入口各建一个 AudioContext。
 *
 * 只有六个内置音色，没有输入框、没有链接校验：
 * 自定义音频链接在 0.3.3 下线了（歌单页链接本质播不了，见 types.ts 的 ChimeId 注释），
 * 与其留一个大概率失败的入口，不如不提供。
 */

import { CHIME_PRESETS, findChime } from "../../logic/chime";
import type { ChimeId, SoundPrefs } from "../../types";

interface SoundPanelProps {
  prefs: SoundPrefs;
  onChange: (next: SoundPrefs) => void;
  /** 试听：由容器负责真正播放（含预热音频上下文） */
  onPreview: (prefs: SoundPrefs) => void;
}

export default function SoundPanel({ prefs, onChange, onPreview }: SoundPanelProps) {
  const currentPreset = findChime(prefs.chime);

  function pick(chime: ChimeId) {
    const next: SoundPrefs = { ...prefs, chime };
    onChange(next);
    // 选「静音」时别试听：那一下本来就该没有声音，播了反而让人以为没生效
    if (chime !== "none") onPreview(next);
  }

  return (
    <details className="campus-pomo-sound">
      <summary className="campus-pomo-sound-sum">
        <span className="campus-pomo-sound-label">
          提示音 · {currentPreset?.label ?? "晨钟"}
        </span>
        <span className="campus-pomo-sound-value">
          {prefs.chime === "none" ? "已静音" : `音量 ${Math.round(prefs.volume * 100)}%`}
        </span>
      </summary>

      <div className="campus-pomo-sound-body">
        <div className="campus-chime-grid" role="group" aria-label="选择提示音">
          {CHIME_PRESETS.map((preset) => (
            <button
              key={preset.id}
              type="button"
              className={`campus-chime-btn${prefs.chime === preset.id ? " is-on" : ""}`}
              aria-pressed={prefs.chime === preset.id}
              onClick={() => pick(preset.id)}
            >
              {preset.label}
            </button>
          ))}
        </div>

        <p className="campus-pomo-sound-hint">{currentPreset?.hint ?? ""}</p>

        <div className="campus-pomo-sound-row">
          <label className="campus-pomo-vol">
            <span>音量</span>
            <input
              type="range"
              min={0}
              max={100}
              step={5}
              value={Math.round(prefs.volume * 100)}
              aria-label="提示音音量"
              onChange={(event) => onChange({ ...prefs, volume: Number(event.target.value) / 100 })}
              // 松开手才试听：拖动过程中每一格都响会变成噪音
              onPointerUp={() => prefs.chime !== "none" && onPreview(prefs)}
              onKeyUp={() => prefs.chime !== "none" && onPreview(prefs)}
            />
            <b>{Math.round(prefs.volume * 100)}%</b>
          </label>

          <button
            type="button"
            className="campus-btn is-ghost"
            onClick={() => onPreview(prefs)}
            disabled={prefs.chime === "none"}
          >
            试听
          </button>
        </div>

        <p className="campus-sound-tip">
          音色由浏览器实时合成，不下载音频文件，断网也能响。
        </p>
      </div>
    </details>
  );
}
