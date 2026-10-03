# -*- coding: utf-8 -*-
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
