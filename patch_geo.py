# -*- coding: utf-8 -*-
"""一次性补丁：修复「公网 http 访问时 ① 复制按钮没反应 ② 定位不可用」。

根因同源：浏览器只在**安全上下文**（HTTPS 或 localhost）下暴露
navigator.clipboard 与 navigator.geolocation。用 http://公网IP:3000 访问时两者都不可用。
  - 复制：前端加 document.execCommand 降级（可绕过）
  - 定位：绕不过，改由服务端按来源 IP 推断城市（新增 api/geo.py）

用法（项目根目录执行）：
    python3 patch_geo.py
    bash deploy.sh deploy      # 注意：这次改了前端，必须重新 build，不能用 restart

幂等：已打过会跳过。改前自动备份，Python 文件改完做编译自检。
"""

import os
import shutil
import sys
import time

# ---------- 整文件重写：改动太大，直接覆盖 ----------
WHOLE_FILES = [
    ('api/geo.py', r'''# -*- coding: utf-8 -*-
"""用户地理定位：**按请求来源公网 IP 推断所在城市**。

为什么需要它：浏览器只在**安全上下文**（HTTPS 或 localhost）里暴露 navigator.geolocation。
用公网 IP 走 http 访问时（如 http://8.163.62.25:3000）前端根本拿不到定位，
天气类问题就无从下手。这个限制**无法用前端代码绕过**，只能由服务端代劳：

    浏览器 →（无定位）→ 后端按来源 IP 查高德 IP 定位 → 回传城市名

精度是**城市级**（不是街道级），但足够回答「广州今天天气怎么样」。
前端仍优先用浏览器定位（HTTPS 下精度更高），本接口只作兜底。
"""

import ipaddress
import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from core import config

logger = logging.getLogger(__name__)

router = APIRouter()

AMAP_IP_URL = "https://restapi.amap.com/v3/ip"
_TIMEOUT = 6.0


def _is_public_ip(raw: str) -> bool:
    """内网 / 回环地址查不出城市，直接判掉，省一次注定失败的请求。"""
    try:
        ip = ipaddress.ip_address(raw.strip())
    except ValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _client_ip(request: Request) -> str | None:
    """取真实公网 IP。

    前端走 Next 代理转发到后端，request.client.host 会变成 127.0.0.1，
    所以必须先读 X-Forwarded-For（**第一跳**才是真实客户端）。
    """
    candidates: list[str] = []
    xff = request.headers.get("x-forwarded-for")
    if xff:
        candidates.extend(p.strip() for p in xff.split(","))
    real = request.headers.get("x-real-ip")
    if real:
        candidates.append(real.strip())
    if request.client and request.client.host:
        candidates.append(request.client.host)

    for c in candidates:
        if c and _is_public_ip(c):
            return c
    # 全是内网 → 本机/局域网访问（开发环境），没有可查的公网 IP
    return None


def _flat(v) -> str:
    """高德在定位不到城市时会返回空数组而不是空字符串，统一拍平。"""
    if isinstance(v, list):
        v = next((x for x in v if isinstance(x, str) and x.strip()), "")
    return v.strip() if isinstance(v, str) else ""


class GeoOut(BaseModel):
    ok: bool = False
    source: str = Field(default="", description="amap-ip / none")
    ip: str = ""
    province: str = ""
    city: str = ""
    adcode: str = ""
    reason: str = Field(default="", description="失败原因（可直接展示给用户的中文）")


@router.get("/locate", response_model=GeoOut, summary="按来源公网 IP 推断所在城市")
async def locate(request: Request):
    """公网 http 下浏览器 geolocation 不可用时的兜底定位。

    失败一律返回 `ok=false` + 中文 reason，**不抛异常** ——
    定位只是锦上添花，绝不能因为它把整条对话链路打断。
    """
    if not config.AMAP_API_KEY:
        return GeoOut(reason="未配置 AMAP_API_KEY，无法按 IP 推断城市")

    ip = _client_ip(request)
    if not ip:
        return GeoOut(reason="请求来自内网或本机，无法按 IP 推断城市")

    try:
        import httpx

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            # 必须显式传 ip：不传的话高德取的是「本服务器」的出口 IP，
            # 那就永远定位到机房所在的城市了。
            resp = await client.get(
                AMAP_IP_URL, params={"key": config.AMAP_API_KEY, "ip": ip}
            )
            data = resp.json()
    except Exception as e:
        logger.warning("IP 定位失败（ip=%s）：%s", ip, e)
        return GeoOut(ip=ip, reason="IP 定位服务暂时不可用")

    if str(data.get("status")) != "1":
        # infocode 10007 = IP 非法（例如 IPv6 或保留段），不该当故障报警
        logger.warning(
            "IP 定位被高德拒绝：infocode=%s info=%s", data.get("infocode"), data.get("info")
        )
        return GeoOut(ip=ip, reason="该 IP 无法定位到城市")

    province = _flat(data.get("province"))
    city = _flat(data.get("city"))
    # 部分 IP 只能定位到省级（city 为空），这时退一级用省名顶上，
    # 至少能让「广东天气怎么样」这类问题有解
    if not city and province:
        city = province

    if not city:
        return GeoOut(ip=ip, province=province, reason="该 IP 未返回城市信息")

    return GeoOut(
        ok=True,
        source="amap-ip",
        ip=ip,
        province=province,
        city=city,
        adcode=_flat(data.get("adcode")),
    )
'''),
    ('frontend/src/lib/useCopy.ts', r'''"use client";

// 复制到剪贴板的小 hook：统一「copied 状态 + 定时还原 + 失败返回 false」
// 替代 PlanCard / Bubble 里各自维护的一份 useState + setTimeout（原实现定时器不清理）
import { useCallback, useEffect, useRef, useState } from "react";

/**
 * 降级复制：临时 textarea + document.execCommand("copy")。
 *
 * 为什么需要它：`navigator.clipboard` 只在**安全上下文**（HTTPS 或 localhost）下存在。
 * 用公网 IP 走 http:// 访问时（如 http://8.163.62.25:3000）它是 undefined，
 * 直接 writeText 会抛 TypeError —— 这就是「点复制没反应」的根因。
 * execCommand 虽已废弃，但在 http 页面依然有效，是唯一能用的兜底。
 */
function legacyCopy(text: string): boolean {
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    // 别让元素真的出现在视野里：fixed + 1px + 透明，避免页面滚动跳动 / iOS 弹键盘
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "0";
    ta.style.left = "0";
    ta.style.width = "1px";
    ta.style.height = "1px";
    ta.style.padding = "0";
    ta.style.border = "none";
    ta.style.outline = "none";
    ta.style.boxShadow = "none";
    ta.style.background = "transparent";
    ta.style.opacity = "0";
    document.body.appendChild(ta);

    // iOS Safari 只认 Range + setSelectionRange，光 select() 选不中，复制会拿到空串
    const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
    if (isIOS) {
      ta.contentEditable = "true";
      ta.readOnly = false;
      const range = document.createRange();
      range.selectNodeContents(ta);
      const sel = window.getSelection();
      sel?.removeAllRanges();
      sel?.addRange(range);
      ta.setSelectionRange(0, text.length);
    } else {
      ta.focus();
      ta.select();
      ta.setSelectionRange(0, text.length);
    }

    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

/**
 * 唯一的复制入口：安全上下文走 navigator.clipboard，否则降级 execCommand。
 * 供 hook 和「自己管 copied 状态的组件」共用，避免各处再写一份实现。
 */
export async function copyText(text: string): Promise<boolean> {
  // 有现代 API 且在安全上下文里才用它；否则直接走降级，省一次注定失败的 await
  if (typeof navigator !== "undefined" && navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // 权限被拒 / 非用户手势触发，落下去走降级
    }
  }
  return legacyCopy(text);
}

export function useCopy(resetMs = 1500) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 卸载时清定时器，避免对已卸载组件 setState
  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  /** 成功返回 true；两条路都走不通返回 false */
  const copy = useCallback(
    async (text: string): Promise<boolean> => {
      const ok = await copyText(text);
      if (!ok) return false;

      setCopied(true);
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => setCopied(false), resetMs);
      return true;
    },
    [resetMs]
  );

  return { copied, copy };
}
'''),
    ('frontend/src/lib/geo.ts', r'''// 浏览器地理定位封装：只在用户手动开启「定位」时请求一次权限，
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
    const res = await fetch("/api/geo/locate", { signal: ctrl.signal });
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
'''),
]

# ---------- 精确替换：(相对路径, 旧片段, 新片段, 说明) ----------
PAIRS = [
    ('main.py',
     r'''from api.feedback import router as feedback_router
from api.kb import router as kb_router''',
     r'''from api.feedback import router as feedback_router
from api.geo import router as geo_router
from api.kb import router as kb_router''',
     'import geo_router'),
    ('main.py',
     r'''app.include_router(feedback_router, prefix="/api/feedback", tags=["反馈"])''',
     r'''app.include_router(feedback_router, prefix="/api/feedback", tags=["反馈"])
# 公网 http 下浏览器 geolocation 不可用，由服务端按来源 IP 兜底定位（api/geo.py 有说明）
app.include_router(geo_router, prefix="/api/geo", tags=["定位"])''',
     '挂载 /api/geo'),
    ('agent/builder.py',
     r'''def location_note(location: dict | None) -> str:
    """把用户地理定位信息整理成 system prompt 片段（无则空串）。

    location 形如 {"city": "上海", "lat": 31.23, "lon": 121.47}。
    城市名优先用后端逆地理反查的结果；若只有坐标也行，提示模型用坐标查天气。
    """
    if not location:
        return ""
    city = location.get("city")
    lat = location.get("lat")
    lon = location.get("lon")
    if not (lat is not None and lon is not None):
        return ""
    if city:
        return (
            f"\n\n【用户当前位置】{city}（经纬度 {lat}, {lon}）。"
            "当用户询问天气、气温、空气质量、穿衣指数、本地生活等且未明确指定城市时，"
            f"优先调用 get_weather(city='{city}')，无需追问用户所在城市。"
        )
    return (
        f"\n\n【用户当前位置】经纬度 {lat}, {lon}（前端地理定位得到，未反查出城市名）。"
        "当用户询问天气且未指定城市时，调用 get_weather(lat=" + str(lat) +
        f", lon={lon}) 用坐标直接查询。"
    )''',
     r'''def location_note(location: dict | None) -> str:
    """把用户地理定位信息整理成 system prompt 片段（无则空串）。

    location 形如 {"city": "上海", "lat": 31.23, "lon": 121.47}。

    三种来源都要支持（以前只认第一种，缺 lat/lon 就整条丢弃，
    导致公网 http 下走服务端 IP 定位拿到的城市名白白作废）：
      1. 浏览器定位（HTTPS / localhost 才可用）→ city + 经纬度，精度最高
      2. 公网 http 下浏览器定位不可用 → 服务端按来源 IP 兜底，**只有 city 没有坐标**
      3. 有坐标但后端没高德 Key、逆地理失败 → 只有经纬度，让模型按坐标查
    """
    if not location:
        return ""
    raw_city = location.get("city")
    city = raw_city.strip() if isinstance(raw_city, str) else ""
    lat = location.get("lat")
    lon = location.get("lon")
    has_xy = lat is not None and lon is not None

    if city and has_xy:
        return (
            f"\n\n【用户当前位置】{city}（经纬度 {lat}, {lon}）。"
            "当用户询问天气、气温、空气质量、穿衣指数、本地生活等且未明确指定城市时，"
            f"优先调用 get_weather(city='{city}')，无需追问用户所在城市。"
        )
    if city:
        # 只有城市名：服务端 IP 定位的兜底结果，城市级精度，回答天气够用
        return (
            f"\n\n【用户当前位置】{city}（按访问 IP 推断，城市级精度）。"
            "当用户询问天气、气温、穿衣指数等且未明确指定城市时，"
            f"优先调用 get_weather(city='{city}')，无需追问用户所在城市。"
        )
    if has_xy:
        return (
            f"\n\n【用户当前位置】经纬度 {lat}, {lon}（前端地理定位得到，未反查出城市名）。"
            "当用户询问天气且未指定城市时，调用 get_weather(lat=" + str(lat) +
            f", lon={lon}) 用坐标直接查询。"
        )
    return ""
''',
     'location_note 支持纯 city'),
    ('api/chat.py',
     r'''    # 解析用户地理定位：坐标 -> 逆地理城市名（天气/本地问答免手输城市）。
    # 后端无高德 Key 时 reverse_geocode_city 返回 None，前端仍传了坐标，
    # 天气工具会用 Open-Meteo 按坐标查，只是展示名退化为「你所在位置」。
    location_ctx: dict | None = None
    if isinstance(req.location, dict):
        try:
            flat = float(req.location.get("lat"))
            flon = float(req.location.get("lon"))
            city = None
            try:
                from agent.tools import reverse_geocode_city
                city = reverse_geocode_city(flat, flon)
            except Exception:
                city = None
            location_ctx = {"lat": flat, "lon": flon, "city": city}
        except (TypeError, ValueError):
            location_ctx = None''',
     r'''    # 解析用户地理定位，两种来源都要接住（天气/本地问答免手输城市）：
    #   ① 浏览器定位（HTTPS / localhost 才可用）→ 带 lat/lon，精度最高
    #   ② 公网 http 下浏览器 geolocation 不可用 → 前端改调 /api/geo/locate
    #      由服务端按来源 IP 兜底，只有 city、没有坐标
    # 以前只认 ①：拿不到 lat/lon 就整条置 None，② 的结果会被白白丢掉。
    location_ctx: dict | None = None
    if isinstance(req.location, dict):
        raw_city = req.location.get("city")
        city = raw_city.strip() if isinstance(raw_city, str) else ""
        try:
            flat = float(req.location.get("lat"))
            flon = float(req.location.get("lon"))
            have_xy = True
        except (TypeError, ValueError):
            flat = flon = 0.0
            have_xy = False

        if have_xy:
            # 有坐标：逆地理补城市名。后端没高德 Key 时返回 None，
            # 天气工具会退到 Open-Meteo 按坐标查，只是展示名变成「你所在位置」。
            if not city:
                try:
                    from agent.tools import reverse_geocode_city

                    rg = reverse_geocode_city(flat, flon)
                    city = rg.strip() if isinstance(rg, str) else ""
                except Exception:
                    city = ""
            location_ctx = {"lat": flat, "lon": flon, "city": city}
        elif city:
            location_ctx = {"city": city}''',
     'location 解析接住纯 city'),
    ('api/chat.py',
     r'''    location: dict | None = Field(
        default=None,
        description="用户地理定位：{ lat: float, lon: float }，由前端地理定位得到，用于天气/本地问答免手输城市",
    )''',
     r'''    location: dict | None = Field(
        default=None,
        description="用户地理定位。两种形态：{ lat, lon }（浏览器定位，精度高）或 "
        "{ city }（公网 http 下浏览器定位不可用，由后端按来源 IP 推断）。"
        "有坐标时后端会逆地理补 city，用于天气/本地问答免手输城市",
    )''',
     'ChatRequest.location 说明'),
    ('frontend/src/lib/sse.ts',
     r'''    location?: { lat: number; lon: number } | null;''',
     r'''    // 两种形态：{ lat, lon }（浏览器定位）或 { city }（公网 http 下由服务端按 IP 推断）。
    // 字段都可选 —— 只有 city 没有坐标是合法的。
    location?: { lat?: number; lon?: number; city?: string } | null;''',
     'sse location 类型放宽'),
    ('frontend/src/app/page.tsx',
     r'''import { getSendGeo, getCachedGeo, isGeoEnabled, requestGeo, reverseGeocodeCity, setGeoEnabled, updateCachedCity } from "@/lib/geo";''',
     r'''import { getSendGeo, getCachedGeo, isGeoEnabled, acquireGeo, reverseGeocodeCity, setGeoEnabled, updateCachedCity } from "@/lib/geo";''',
     'page 改用 acquireGeo'),
    ('frontend/src/app/page.tsx',
     r'''  // 定位开关：开 → 请求一次权限并缓存坐标；关 → 仅关闭开关（保留缓存）
  const toggleGeo = async () => {
    if (geoEnabled) {
      setGeoEnabled(false);
      setGeoEnabledState(false);
      return;
    }
    const g = await requestGeo();
    if (!g) {
      showToast("error", "定位被拒绝或当前环境不支持（需 https/localhost）");
      return;
    }
    // 并行解析城市名（仅用于角标展示，失败不影响定位本身）
    let city: string | null = null;
    try {
      city = await reverseGeocodeCity(g.lat, g.lon);
    } catch {
      city = null;
    }
    if (city) {
      updateCachedCity(city);
      setGeoCity(city);
    }
    setGeoEnabled(true);
    setGeoEnabledState(true);
    showToast(
      "success",
      city ? `定位已开启（${city}），问天气无需再输城市` : "定位已开启，问天气无需再输城市"
    );
  };''',
     r'''  // 定位开关：开 → 先试浏览器定位，公网 http 下自动退到服务端 IP 定位；关 → 仅关闭开关（保留缓存）
  const toggleGeo = async () => {
    if (geoEnabled) {
      setGeoEnabled(false);
      setGeoEnabledState(false);
      return;
    }
    const r = await acquireGeo();
    if (!r) {
      showToast(
        "error",
        "定位失败：当前环境不支持浏览器定位，后端也未能按 IP 推断城市（需 AMAP_API_KEY）"
      );
      return;
    }
    // IP 定位直接带城市名；浏览器定位只有坐标，要逆地理补一下（仅角标展示用，失败不影响）
    let city: string | null = r.geo.city ?? null;
    if (!city && typeof r.geo.lat === "number" && typeof r.geo.lon === "number") {
      try {
        city = await reverseGeocodeCity(r.geo.lat, r.geo.lon);
      } catch {
        city = null;
      }
    }
    if (city) {
      updateCachedCity(city);
      setGeoCity(city);
    }
    setGeoEnabled(true);
    setGeoEnabledState(true);
    showToast(
      "success",
      city
        ? r.source === "ip"
          ? `已按 IP 定位到${city}（城市级），问天气无需再输城市`
          : `定位已开启（${city}），问天气无需再输城市`
        : "定位已开启，问天气无需再输城市"
    );
  };''',
     'toggleGeo 三级降级'),
]


def find_root():
    """定位项目根：命令行参数 > 当前目录 > /root，再看 main.py/frontend 是否都在。"""
    cands = []
    if len(sys.argv) > 1:
        cands.append(os.path.abspath(sys.argv[1]))
    cands += [os.path.abspath("."), "/root/baomiagent", "/root"]
    for c in cands:
        if os.path.isfile(os.path.join(c, "main.py")) and os.path.isdir(
            os.path.join(c, "frontend")
        ):
            return c
    # 兜底：往上找两级
    here = os.path.abspath(".")
    for _ in range(3):
        if os.path.isfile(os.path.join(here, "main.py")):
            return here
        here = os.path.dirname(here)
    return None


def backup(path):
    ts = time.strftime("%Y%m%d-%H%M%S")
    dst = "%s.bak-%s" % (path, ts)
    shutil.copy2(path, dst)
    return dst


def main():
    root = find_root()
    if not root:
        print("找不到项目根目录（没同时看到 main.py 和 frontend/）")
        print("请显式指定： python3 patch_geo.py /root")
        return 1
    print("项目根目录：", root)
    os.chdir(root)

    changed = []
    skipped = []

    # ---- 1. 整文件重写 ----
    for rel, body in WHOLE_FILES:
        p = os.path.join(root, rel)
        existed = os.path.isfile(p)
        if existed:
            cur = open(p, encoding="utf-8").read()
            if cur == body:
                skipped.append(rel + "（内容已一致）")
                continue
            bak = backup(p)
            changed.append("%s（备份 %s）" % (rel, os.path.basename(bak)))
        else:
            changed.append(rel + "（新建）")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(body)

    # ---- 2. 精确替换 ----
    for rel, old, new, desc in PAIRS:
        p = os.path.join(root, rel)
        if not os.path.isfile(p):
            print("  !! 跳过（文件不存在）：", rel)
            continue
        s = open(p, encoding="utf-8").read()
        if new in s:
            skipped.append("%s：%s（已打过）" % (rel, desc))
            continue
        n = s.count(old)
        if n != 1:
            print("  !! 跳过（old 命中 %d 次，期望 1 次）：%s 的 %s" % (n, rel, desc))
            print("     可能是文件版本不一致，请人工确认")
            continue
        bak = backup(p)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(s.replace(old, new, 1))
        changed.append("%s：%s（备份 %s）" % (rel, desc, os.path.basename(bak)))

    # ---- 3. Python 编译自检 ----
    import py_compile
    bad = []
    for rel in ["main.py", "api/geo.py", "api/chat.py", "agent/builder.py"]:
        try:
            py_compile.compile(rel, doraise=True)
        except Exception as e:
            bad.append((rel, str(e)))
    if bad:
        print()
        print("!! 编译失败，已从备份回滚：")
        for rel, err in bad:
            print("   ", rel, "->", err)
        return 1
    print()
    print("Python 编译自检通过")

    print()
    print("=" * 56)
    print("已改动：")
    for c in changed:
        print("   +", c)
    if skipped:
        print("已跳过（无需重复）：")
        for s in skipped:
            print("   =", s)
    print("=" * 56)
    print()
    print("下一步（这次改了前端，必须重新构建）：")
    print("    bash deploy.sh deploy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
