// 浏览器地理定位封装：只在用户手动开启「定位」时请求一次权限，
// 之后把坐标缓存到 localStorage（1 小时有效），发送消息时自动附带，
// 让天气 / 本地相关问答免手输城市。无权限 / 不支持时全部安全降级为 null。
//
// ⚠️ 关键背景：navigator.geolocation **只在安全上下文**（HTTPS 或 localhost）下存在。
// 用公网 IP 走 http 访问时（如 http://8.163.62.25:3000）它直接不可用，
// 这是浏览器硬策略，**前端绕不过去**。所以这里准备了第二级：
//     浏览器定位（精确） → 失败 → 服务端按来源 IP 推断城市（城市级）
// 第二级由后端 GET /api/geo/locate 提供。

export interface GeoPoint {
  lat?: number;
  lon?: number;
  city?: string;
  /** 定位来源：browser = 浏览器（精确）；ip = 服务端按 IP 推断（城市级） */
  source?: "browser" | "ip";
  ts: number;
}

const ENABLED_KEY = "baomi.geo.enabled";
const CACHE_KEY = "baomi.geo.cache";
const TTL = 1000 * 60 * 60; // 1 小时

export function isGeoEnabled(): boolean {
  try {
    return localStorage.getItem(ENABLED_KEY) === "1";
  } catch {
    return false;
  }
}

export function setGeoEnabled(v: boolean): void {
  try {
    localStorage.setItem(ENABLED_KEY, v ? "1" : "0");
  } catch {
    /* ignore */
  }
}

export function getCachedGeo(): GeoPoint | null {
  try {
    const s = localStorage.getItem(CACHE_KEY);
    if (!s) return null;
    const g = JSON.parse(s) as GeoPoint;
    if (!g) return null;
    // IP 定位只有城市名没有坐标，所以「有坐标」和「有城市名」满足其一即可
    const hasXY = typeof g.lat === "number" && typeof g.lon === "number";
    const hasCity = typeof g.city === "string" && g.city.length > 0;
    if (!hasXY && !hasCity) return null;
    if (Date.now() - (g.ts || 0) > TTL) return null;
    return g;
  } catch {
    return null;
  }
}

function cacheGeo(g: GeoPoint): void {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(g));
  } catch {
    /* ignore */
  }
}

/** 请求一次浏览器定位（必须在用户手势里调用，否则浏览器会拒）。返回坐标或 null。 */
export function requestGeo(): Promise<GeoPoint | null> {
  return new Promise((resolve) => {
    if (typeof navigator === "undefined" || !("geolocation" in navigator)) {
      resolve(null);
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const g: GeoPoint = {
          lat: +pos.coords.latitude.toFixed(4),
          lon: +pos.coords.longitude.toFixed(4),
          source: "browser",
          ts: Date.now(),
        };
        cacheGeo(g);
        resolve(g);
      },
      () => resolve(null),
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 600000 }
    );
  });
}

/** 兜底定位：让服务端按访问来源的公网 IP 推断城市（城市级精度，无坐标）。 */
export async function requestIpGeo(): Promise<GeoPoint | null> {
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 8000);
    // 定位接口也已鉴权，同源也要显式带 Cookie
    const res = await fetch("/api/geo/locate", {
      signal: ctrl.signal,
      credentials: "include",
    });
    clearTimeout(timer);
    if (!res.ok) return null;
    const d = (await res.json()) as {
      ok?: boolean;
      city?: string;
      province?: string;
    };
    // 接口失败时不抛异常，只回 ok=false（原因在 reason 字段），这里统一当没定位到
    if (!d.ok) return null;
    const city = d.city || d.province || "";
    if (!city) return null;
    const g: GeoPoint = { city, source: "ip", ts: Date.now() };
    cacheGeo(g);
    return g;
  } catch {
    return null;
  }
}

export interface AcquiredGeo {
  geo: GeoPoint;
  source: "browser" | "ip";
}

/**
 * 取得可用定位的统一入口：**优先浏览器定位**，拿不到再退到服务端 IP 定位。
 * 返回来源标记，方便上层给出准确提示（IP 定位是城市级，别说成精确定位）。
 */
export async function acquireGeo(): Promise<AcquiredGeo | null> {
  const g = await requestGeo();
  if (g) return { geo: g, source: "browser" };

  // 走到这里多半是公网 http：geolocation 不存在 / 被拒 / 超时
  const ip = await requestIpGeo();
  if (ip) return { geo: ip, source: "ip" };

  return null;
}

/** 取可发送的定位：开启且缓存有效才返回，否则 null。坐标与城市名有哪个带哪个。 */
export function getSendGeo(): { lat?: number; lon?: number; city?: string } | null {
  if (!isGeoEnabled()) return null;
  const g = getCachedGeo();
  if (!g) return null;
  const out: { lat?: number; lon?: number; city?: string } = {};
  if (typeof g.lat === "number" && typeof g.lon === "number") {
    out.lat = g.lat;
    out.lon = g.lon;
  }
  if (g.city) out.city = g.city;
  return Object.keys(out).length ? out : null;
}

/** 把解析到的城市名写回本地缓存（让角标/提示能显示当前城市）。 */
export function updateCachedCity(city: string): void {
  const g = getCachedGeo();
  if (!g) return;
  g.city = city;
  cacheGeo(g);
}

// 免 Key、支持 CORS 的客户端逆地理服务（高德逆地理只在后端用，不回传前端）。
// 仅用于界面角标展示当前城市，失败不影响定位本身。
const REVERSE_URL = "https://api.bigdatacloud.net/data/reverse-geocode-client";

/** 坐标 -> 中文城市名（如「杭州市」），网络失败返回 null。 */
export async function reverseGeocodeCity(
  lat: number,
  lon: number
): Promise<string | null> {
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 8000);
    const res = await fetch(
      `${REVERSE_URL}?latitude=${lat}&longitude=${lon}&localityLanguage=zh`,
      { signal: ctrl.signal }
    );
    clearTimeout(timer);
    if (!res.ok) return null;
    const data = (await res.json()) as {
      city?: string;
      locality?: string;
      principalSubdivision?: string;
    };
    const raw = data.city || data.locality || data.principalSubdivision || "";
    return raw ? raw.replace(/市$/, "") : null;
  } catch {
    return null;
  }
}
