import Constants from "expo-constants";
import { Platform } from "react-native";

/**
 * 版本号与「检查更新」。
 *
 * 壳里的页面是线上站点，改页面不用发版；但壳本身（图标、启动屏、权限、
 * 以后的原生能力）改了必须重新装 APK。安卓是侧载分发，没有应用商店代劳，
 * 所以壳得自己知道「线上最新是哪个版本」。
 *
 * 版本的真值只有两处：
 *   1. `app.json` —— 构建时写进 APK 的 versionName / versionCode；
 *   2. `frontend/public/app-version.json` —— 线上告诉老包现在最新是哪个版本。
 * 两处都由 `tools/bump_app_version.py` 一起改，下面的兜底常量也是它改的。
 */

/** 与 app.json 的 version 保持一致；versionCode 读不到时用它显示。 */
const VERSION_FALLBACK = "0.2.1";

/**
 * 显示用的版本号。
 *
 * 正式包里 `Constants.expoConfig` 来自构建时嵌入的 app.config；万一某个
 * 构建链没带上（bare 工程偶发），退回编译进 JS 的兜底值，也不要显示 “null”。
 */
export const APP_VERSION = Constants.expoConfig?.version ?? VERSION_FALLBACK;

/**
 * 原生构建号：安卓是 versionCode，iOS 是 buildNumber。
 * 这两个值写死在原生包里，网站改不动 —— 正好拿来判断「这个壳是不是旧的」。
 */
function nativeBuildNumber(): number {
  if (Platform.OS === "android") {
    return Constants.platform?.android?.versionCode ?? 0;
  }
  const n = Number(Constants.platform?.ios?.buildNumber ?? 0);
  return Number.isFinite(n) ? n : 0;
}

export const APP_BUILD = nativeBuildNumber();

/** 加载页 / 错误页底部那行小字：「v0.2.0 · 2」。 */
export const APP_VERSION_LABEL = APP_BUILD
  ? `v${APP_VERSION} · ${APP_BUILD}`
  : `v${APP_VERSION}`;

export type AppUpdate = {
  version: string;
  /** 安卓 versionCode；0 表示线上没写 */
  versionCode: number;
  /** 新 APK / 下载页，必须是 https */
  url: string;
  /** 一两句更新说明，可空 */
  notes?: string;
};

/** 逐段比 1.10.2 vs 1.9.0；缺段按 0 算。只在读不到 versionCode 时兜底。 */
function compareVersion(a: string, b: string): number {
  const pa = a.split(/[.\-+]/).map((x) => parseInt(x, 10) || 0);
  const pb = b.split(/[.\-+]/).map((x) => parseInt(x, 10) || 0);
  const len = Math.max(pa.length, pb.length);
  for (let i = 0; i < len; i += 1) {
    const d = (pa[i] ?? 0) - (pb[i] ?? 0);
    if (d !== 0) return d > 0 ? 1 : -1;
  }
  return 0;
}

/**
 * 拉一次版本清单，只有真的更新时才返回内容。
 *
 * 全部失败路径都返回 null（没网、404、站点返回了 HTML、超时）——
 * 更新检查是锦上添花，不能因为它弹错误框，更不能挡住首屏。
 * URL 强制 https：清单是线上文件，但万一被改坏了，也不该让 App 去
 * 打开一个 `javascript:` 或 `file:` 之类的东西。
 */
export async function checkForUpdate(
  manifestUrl: string,
  timeoutMs = 4000,
): Promise<AppUpdate | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    // 带时间戳绕过 CDN / WebView 的缓存：刚发的版本必须立刻能看到
    const res = await fetch(`${manifestUrl}?t=${Date.now()}`, {
      signal: controller.signal,
      headers: { Accept: "application/json" },
    });
    if (!res.ok) return null;

    const data = (await res.json()) as Partial<AppUpdate>;
    if (!data || typeof data.version !== "string") return null;
    if (typeof data.url !== "string" || !/^https:\/\//i.test(data.url)) return null;

    const code = Number(data.versionCode ?? 0) || 0;
    const newer =
      code > 0 && APP_BUILD > 0
        ? code > APP_BUILD
        : compareVersion(data.version, APP_VERSION) > 0;
    if (!newer) return null;

    return {
      version: data.version,
      versionCode: code,
      url: data.url,
      notes: typeof data.notes === "string" ? data.notes : undefined,
    };
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}
