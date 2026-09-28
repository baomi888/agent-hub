// 浏览器地理定位封装：只在用户手动开启「定位」时请求一次权限，
// 之后把坐标缓存到 localStorage（1 小时有效），发送消息时自动附带，
// 让天气 / 本地相关问答免手输城市。无权限 / 不支持时全部安全降级为 null。

export interface GeoPoint {
  lat: number;
  lon: number;
  city?: string;
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
    if (!g || typeof g.lat !== "number" || typeof g.lon !== "number") return null;
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

/** 请求一次定位（必须在用户手势里调用，否则浏览器会拒）。返回坐标或 null。 */
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

/** 取可发送的定位坐标：开启且缓存有效才返回，否则 null。 */
export function getSendGeo(): { lat: number; lon: number } | null {
  if (!isGeoEnabled()) return null;
  const g = getCachedGeo();
  if (!g) return null;
  return { lat: g.lat, lon: g.lon };
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
