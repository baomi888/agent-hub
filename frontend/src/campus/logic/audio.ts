/**
 * 提示音播放 —— 板块内**唯一**碰 Web Audio 的地方。
 *
 * 两个浏览器限制决定了这里的写法：
 *  1. `AudioContext` 必须在用户手势里创建或恢复，否则一直是 suspended，播出来是静音。
 *     所以点「开始」时会调一次 `primeAudio()` 预热，到点再播就不受限制。
 *  2. 合成音也可能被自动播放策略拦掉。
 *     这里一律静默失败：**弹窗该出现还是会出现**，声音只是锦上添花，不能因为它没响就让人以为这轮白做了。
 *
 * 合成参数（波形、低通、包络）全在 logic/chime.ts 里，这里只负责把参数变成声音。
 *
 * 0.3.3 起只剩合成音：原先那条「自定义音频链接」的 `<audio>` 路径已随功能一起下线，
 * 页面里不再有第二套播放器，也就不存在两套音量/自动播放行为的差异。
 */

import type { ChimeId, SoundPrefs } from "../types";
import { clampVolume, findChime, type ChimeNote, type ChimePreset } from "./chime";

let context: AudioContext | null = null;
/** 正在响的那一声的总输出，用来在下次播放前把它掐掉（试听连点不会叠成一团） */
let activeMaster: GainNode | null = null;

function getAudioContextCtor(): typeof AudioContext | undefined {
  if (typeof window === "undefined") return undefined;
  return (
    window.AudioContext ??
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
  );
}

export function isAudioSupported(): boolean {
  return getAudioContextCtor() !== undefined;
}

/**
 * 预热音频上下文。**必须在点击事件里调用**（开始计时、点试听都算）。
 * 重复调用无害。
 */
export function primeAudio(): void {
  const Ctor = getAudioContextCtor();
  if (!Ctor) return;

  if (!context) {
    try {
      context = new Ctor();
    } catch {
      context = null;
      return;
    }
  }

  if (context.state === "suspended") {
    void context.resume().catch(() => {
      // 被策略拦了就等下一次手势，不打扰用户
    });
  }
}

/** 一个音：振荡器 → 包络 → 主输出 */
function scheduleTone(
  ctx: AudioContext,
  output: AudioNode,
  note: ChimeNote,
  startAt: number,
  gainScale = 1,
): void {
  const osc = ctx.createOscillator();
  const env = ctx.createGain();

  osc.type = note.wave;
  osc.frequency.setValueAtTime(note.freq, startAt);
  if (note.glideTo !== undefined && note.glideTo > 0) {
    // 水滴那种下滑音：指数滑到目标，听感是"落下"而不是"变调"
    osc.frequency.exponentialRampToValueAtTime(note.glideTo, startAt + note.duration);
  }

  const peak = Math.max(0.0001, note.gain * gainScale);
  // 起音 12ms：直接跳到峰值会有"啪"的爆音，这一步是不刺耳的关键之一
  env.gain.setValueAtTime(0.0001, startAt);
  env.gain.exponentialRampToValueAtTime(peak, startAt + 0.012);
  // 指数衰减到接近 0（不能写 0，exponentialRamp 不接受 0）
  env.gain.exponentialRampToValueAtTime(0.0001, startAt + note.duration);

  osc.connect(env).connect(output);
  osc.start(startAt);
  osc.stop(startAt + note.duration + 0.05);
}

/** 把一组音符排进时间轴，返回总时长（秒），供调用方判断何时收尾 */
function schedulePreset(ctx: AudioContext, preset: ChimePreset, volume: number): number {
  const master = ctx.createGain();
  const target = Math.max(0.0001, clampVolume(volume) * 0.9);
  const startedAt = ctx.currentTime + 0.02;

  // 掐掉上一声：连续试听几个音色时，不这样做会叠在一起变成一团噪音。
  // 用 8ms 淡出而不是直接归零，避免"咔"的截断声。
  if (activeMaster) {
    try {
      activeMaster.gain.cancelScheduledValues(ctx.currentTime);
      activeMaster.gain.setValueAtTime(activeMaster.gain.value, ctx.currentTime);
      activeMaster.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.008);
    } catch {
      // 已断开或已结束的节点，忽略
    }
  }
  activeMaster = master;

  // 整体 120ms 渐入：从静音直接拉到目标音量同样是"吓一跳"的来源
  master.gain.setValueAtTime(0.0001, startedAt);
  master.gain.exponentialRampToValueAtTime(target, startedAt + 0.12);

  const filter = ctx.createBiquadFilter();
  filter.type = "lowpass";
  filter.frequency.value = preset.lowpass;
  // Q 取 0.6：略微圆滑但不会在截止点附近鼓起一个峰
  filter.Q.value = 0.6;

  master.connect(filter).connect(ctx.destination);

  let total = 0;
  for (const note of preset.notes) {
    scheduleTone(ctx, master, note, startedAt + note.at);
    if (note.overtone !== undefined && note.overtone > 0) {
      // 泛音层：同一时刻叠一个高八度，音量压到 22%，
      // 作用是让钟声"更实"，不是"更响"
      scheduleTone(
        ctx,
        master,
        { ...note, freq: note.freq * note.overtone, glideTo: undefined },
        startedAt + note.at,
        0.22,
      );
    }
    total = Math.max(total, note.at + note.duration);
  }

  return total;
}

/** 播一个内置音色 */
export function playChime(id: ChimeId, volume: number): void {
  if (id === "none") return;
  const preset = findChime(id);
  if (!preset || preset.notes.length === 0) return;

  const Ctor = getAudioContextCtor();
  if (!Ctor) return;
  if (!context) primeAudio();
  if (!context) return;

  if (context.state === "suspended") {
    void context.resume().catch(() => {});
  }

  try {
    schedulePreset(context, preset, volume);
  } catch {
    // 上下文被系统回收（iOS 切后台常见）：下一次手势会重建，这里不抛
  }
}

/**
 * 到点播放。
 *
 * 保留这一层而不是让调用方直接 `playChime`：试听与到点必须是同一条路径，
 * 以后若要加「按结束原因换音色」之类的规则，只改这里一处。
 */
export function playFinishSound(prefs: SoundPrefs): void {
  playChime(prefs.chime, prefs.volume);
}
