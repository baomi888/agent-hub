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
  /** 地级市，已去掉末尾「市」（「茂名」） */
  city?: string;
  /** 区/县（「茂南区」「电白区」「化州市」）。只有浏览器定位拿得到；
   *  IP 兜底是高德城市级数据，这里**永远为空** —— 不给假精度 */
  district?: string;
  /** 定位来源：browser = 浏览器（精确）；ip = 服务端按 IP 推断（城市级）；
   *  manual = 用户自己填的城市（没有坐标，但优先级最高） */
  source?: "browser" | "ip" | "manual";
  ts: number;
}

/**
 * 两级地名：「茂名 · 茂南区」。
 *
 * 为什么两级都显示：区县名对本地人很清楚，但重名和生僻的也不少
 * （「新兴县」「电白区」），只给区名有人反而不知道自己在哪；
 * 只给市名又回到"看不出定位准不准"的老问题。中间点一颗 · 就够。
 */
export function formatPlace(p: { city?: string; district?: string } | null | undefined): string {
  if (!p) return "";
  const city = (p.city || "").trim();
  const district = (p.district || "").trim();
  if (city && district && city !== district) return `${city} · ${district}`;
  return district || city;
}

const ENABLED_KEY = "baomi.geo.enabled";
const CACHE_KEY = "baomi.geo.cache";
/**
 * 用户手填的城市。**单独一个 key、不带 TTL**，因为它和自动定位是两种性质：
 * 自动定位是「猜的」，一小时过期合理；手填是「用户明确说的」，过期就等于把他的
 * 修正偷偷撤销掉 —— 他会看到角标过一小时又变回错的城市。
 */
const MANUAL_KEY = "baomi.geo.manual";
const TTL = 1000 * 60 * 60; // 1 小时

/** 取用户手填的城市（没有则空串）。 */
export function getManualCity(): string {
  try {
    return (localStorage.getItem(MANUAL_KEY) || "").trim();
  } catch {
    return "";
  }
}

/** 记下用户手填的城市；传空串等于清掉。返回规范化后的值。 */
export function setManualCity(name: string): string {
  const v = (name || "").trim().replace(/市$/, "");
  try {
    if (v) localStorage.setItem(MANUAL_KEY, v);
    else localStorage.removeItem(MANUAL_KEY);
  } catch {
    /* 隐私模式：存不下只是这次记不住，不能让页面挂掉 */
  }
  return v;
}

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
    // 手填城市优先，且不信 TTL：用户明确改过，一小时后自动变回去等于白改
    const manual = getManualCity();
    if (manual) return { city: manual, source: "manual", ts: Date.now() };

    const s = localStorage.getItem(CACHE_KEY);
    if (!s) return null;
    const g = JSON.parse(s) as GeoPoint;
    if (!g) return null;
    // IP 定位只有城市名没有坐标，所以「有坐标」和「有地名」满足其一即可
    const hasXY = typeof g.lat === "number" && typeof g.lon === "number";
    const hasName =
      (typeof g.city === "string" && g.city.length > 0) ||
      (typeof g.district === "string" && g.district.length > 0);
    if (!hasXY && !hasName) return null;
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
    // 与 reverseGeocodePlace 保持一致，去掉末尾「市」：
    // 否则浏览器定位显示「茂名」、IP 兜底显示「茂名市」，同一处角标两种写法，
    // 用户会以为定位在两套系统之间反复横跳
    const raw = (d.city || d.province || "").trim().replace(/市$/, "");
    if (!raw) return null;
    const g: GeoPoint = { city: raw, source: "ip", ts: Date.now() };
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
  /* 手填城市：只送城市名，**不送坐标**。
     因为后端有坐标时会自己按坐标重新逆地理（agent/tools.py::reverse_geocode_city），
     那个结果会覆盖用户手填的 —— 用户明明改成了「大理」，传坐标过去又被推回「茂名」，
     等于没得改。没有坐标时后端直接用我们给的城市名，这才是"手填优先"真正生效的条件。 */
  const manual = getManualCity();
  if (manual) return { city: manual };

  const g = getCachedGeo();
  if (!g) return null;
  const out: { lat?: number; lon?: number; city?: string } = {};
  if (typeof g.lat === "number" && typeof g.lon === "number") {
    out.lat = g.lat;
    out.lon = g.lon;
  }
  /* ⚠️ 这里只送**市级**，不带区县，尽管缓存里有 district。
     原因在 agent/builder.py::location_note —— 它会把 city 原样拼成
     `get_weather(city='{city}')` 写进 system prompt。若这里送「茂名 · 茂南区」，
     模型就会拿这个带分隔符的串去查高德地理编码，必然解析不到城市。
     真精度靠 lat/lon（Open-Meteo 按坐标查，1~11km 网格），区县只用于展示。 */
  if (g.city) out.city = g.city;
  return Object.keys(out).length ? out : null;
}

/** 把逆地理解析到的市/区县写回本地缓存（让角标/提示能显示当前位置）。 */
export function updateCachedPlace(place: { city?: string; district?: string }): void {
  const g = getCachedGeo();
  if (!g) return;
  if (place.city) g.city = place.city;
  g.district = place.district || "";
  cacheGeo(g);
}

// 免 Key、支持 CORS 的客户端逆地理服务（高德逆地理只在后端用，不回传前端）。
// 仅用于界面角标展示当前城市/区县，失败不影响定位本身。
const REVERSE_URL = "https://api.bigdatacloud.net/data/reverse-geocode-client";

/** 比区县还细的行政级别：乡镇/村/社区/街道。显示出来用户反倒不知道在哪，一律不用 */
const TOO_FINE = /(村|社区|街道|镇|乡)$/;

/**
 * 坐标 → 市 + 区县。网络失败返回 null。
 *
 * ⚠️ 取值顺序踩过坑：这个接口同时给 `city`（地级市）和 `locality`（区县），
 * 原来写 `city || locality || ...`，`city` 在前 ⇒ **区县名永远取不到**，
 * 角标只显示「茂名市」。实测同一坐标返回 city=茂名市 / locality=茂南区
 * / administrative[adminLevel=6].name=茂南区，能力一直有，是被这行顺序吃掉的。
 *
 * 现在优先从 `localityInfo.administrative` 里挑 adminLevel=6 的那一级
 * （= 区县级，`chinaAdminCode` 到这里是三段），比盲取 `locality` 稳：
 * 直辖市的 `city` 常为空，而 locality 有时会一路细到街道。
 */
export async function reverseGeocodePlace(
  lat: number,
  lon: number
): Promise<{ city: string; district: string } | null> {
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
      localityInfo?: {
        administrative?: { name?: string; adminLevel?: number; chinaAdminCode?: string }[];
      };
    };

    const rawCity = (data.city || data.principalSubdivision || "").trim();
    const city = rawCity.replace(/市$/, "");

    let district = "";
    const adm = data.localityInfo?.administrative;
    if (Array.isArray(adm)) {
      // 取 adminLevel 最大的那一级里「最像区县」的：6 = 区/县，7 往往已是街道
      const lv = adm
        .filter((x) => x && typeof x.name === "string" && x.name.trim())
        .filter((x) => x.adminLevel === 6)
        .pop();
      if (lv?.name) district = lv.name.trim();
    }
    if (!district) {
      const loc = (data.locality || "").trim();
      if (loc && !TOO_FINE.test(loc)) district = loc;
    }
    // 县级市会出现 city=district=「义乌市」：重复就只留一个
    if (district && (district === rawCity || district === city)) district = "";

    if (!city && !district) return null;
    return { city, district };
  } catch {
    return null;
  }
}
