/**
 * 番茄钟提示音偏好 · 读写与清洗
 *
 * 和其他 store 一样只管「拿、洗、放」，规则留在纯函数里。
 *
 * 0.3.3 去掉了自定义音频链接，存量数据里可能还有两种残留，读取时都兜住：
 *  - `chime: "custom"`：已经不是合法的 ChimeId，`isChimeId` 会判无效 → 回落到默认音色，
 *    不会出现"设置里什么都没选"的空态；
 *  - 多出来的 `customUrl` 字段：不解构、不转发，下次保存时自然被丢掉，不用专门写迁移。
 */

import { CAMPUS_KEYS } from "../storage/keys";
import { readJson, writeJson } from "../storage";
import type { SoundPrefs } from "../types";
import { DEFAULT_CHIME, clampVolume, isChimeId } from "./chime";

export const DEFAULT_SOUND_PREFS: SoundPrefs = {
  chime: DEFAULT_CHIME,
  volume: 0.6,
};

/** 把任意来源的数据洗成偏好；缺字段用默认值补齐 */
export function sanitizeSoundPrefs(raw: unknown): SoundPrefs {
  if (!raw || typeof raw !== "object") return { ...DEFAULT_SOUND_PREFS };

  const row = raw as Record<string, unknown>;
  const chime = isChimeId(row.chime) ? row.chime : DEFAULT_SOUND_PREFS.chime;
  const volume = clampVolume(Number(row.volume));

  return { chime, volume };
}

export function loadSoundPrefs(): SoundPrefs {
  return sanitizeSoundPrefs(readJson<unknown>(CAMPUS_KEYS.focusSound));
}

export function saveSoundPrefs(prefs: SoundPrefs): void {
  // 显式只写这两个字段：即使调用方传进来的是带旧字段的对象，也不会把它再存回去
  writeJson(CAMPUS_KEYS.focusSound, { chime: prefs.chime, volume: prefs.volume });
}
