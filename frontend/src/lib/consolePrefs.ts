"use client";

// 设置页对本地偏好的读写。
//
// 为什么单独一层：`baomi.prefs` 是**整对象覆盖写**（page.tsx 的 usePrefs 每次都把
// 全部字段一起写回）。设置页只改其中两三项，若自己拼一个新对象写回去，
// 就会把工作台里刚改的模式、每个资料库的切片档位一并冲掉。
// 这里统一走「读进来 → 改这一项 → 写回去」。
//
// ⚠️ 已知边界：设置页与工作台是两个独立路由，不会同时挂载；
// 同一标签页内先改设置再回工作台是安全的（usePrefs 挂载时重新读）。
const PREFS_KEY = "baomi.prefs";
const THEME_KEY = "theme";

export type FontScale = "auto" | "s" | "m" | "l";

export const FONT_SCALE_OPTIONS: { value: FontScale; label: string }[] = [
  { value: "auto", label: "跟随系统" },
  { value: "s", label: "小" },
  { value: "m", label: "标准" },
  { value: "l", label: "大" },
];

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
