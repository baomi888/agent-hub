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
