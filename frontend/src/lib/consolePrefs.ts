"use client";

// 设置页对本地偏好的读写。
//
// 为什么单独一层：`baomi.prefs` 曾有两套读写实现、行为不一致 —— 设置页走这里的
// 「读进来 → 改这一项 → 写回去」，而 page.tsx 的 usePrefs 是整对象覆盖写，
// 且读端只回 3 个字段、写端却写 6 个：设置页刚设好的 topK / chunkSize / chunkOverlap
// 一回工作台就被默认值静默重置（用户设置永久丢失，且不易察觉）。
// ⇒ 两边**必须共用**本文件的 readPrefs / patchPrefs（usePrefs 已改为复用）。
const PREFS_KEY = "baomi.prefs";
const THEME_KEY = "theme";

/** 检索条数默认值。须与后端 core/config.py::TOP_K_DEFAULT 一致，改一处要改两处 */
export const DEFAULT_TOP_K = 3;

/** 字号档位（2026-10-29 R3 改）：从「小/标准/大」改为「标准/大/特大」。
 *  取消「小」是因为它把正文压到 13px 以下，正好和「字太小」的反馈相反。
 *  档位现在缩放**根字号**（见 tokens.css），全站 rem 字号一次生效。 */
export type FontScale = "auto" | "m" | "l" | "xl";

export const FONT_SCALE_OPTIONS: { value: FontScale; label: string }[] = [
  { value: "auto", label: "跟随系统" },
  { value: "m", label: "标准" },
  { value: "l", label: "大" },
  { value: "xl", label: "特大" },
];

/** 把任何来源的档位值收敛到合法值。"s" 是旧档位名，等价于标准。 */
export function normalizeFontScale(v: unknown): FontScale {
  if (v === "l" || v === "xl" || v === "auto") return v;
  return "m";
}

function read(): Record<string, unknown> {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    return raw ? (JSON.parse(raw) as Record<string, unknown>) : {};
  } catch {
    /* 隐私模式 / 脏数据：按空处理，不让设置页白屏 */
    return {};
  }
}

export function readPrefs(): Record<string, unknown> {
  return read();
}

/** 只覆盖传进来的键，其余原样保留 */
export function patchPrefs(patch: Record<string, unknown>): void {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify({ ...read(), ...patch }));
  } catch {
    /* 写不进去就算了：这只是偏好，不是数据 */
  }
}

export function readTheme(): boolean {
  if (typeof document === "undefined") return false;
  return document.documentElement.classList.contains("dark");
}

export function writeTheme(dark: boolean): void {
  document.documentElement.classList.toggle("dark", dark);
  try {
    localStorage.setItem(THEME_KEY, dark ? "dark" : "light");
  } catch {
    /* 同上 */
  }
}

/** 字号档位落在 html[data-fs] 上，CSS 由 --msg-fs 派生标题比例 */
export function applyFontScale(f: FontScale): void {
  document.documentElement.dataset.fs = f;
  patchPrefs({ fontScale: f });
}
