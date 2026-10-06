# -*- coding: utf-8 -*-
"""URL 安全校验：挡 SSRF。

为什么现在必须做：单用户时"让服务器去访问一个 URL"顶多是访问自己的东西；
多用户之后，任意一个登录用户都能借服务器的身份去访问**服务器能看到、
而他自己从外网看不到**的地址 —— 云厂商元数据服务、内网管理后台、Redis、
同机的其他端口。这就是典型的 SSRF，而且是"过了登录"的 SSRF。

三条会发起服务端请求的通道共用这一份判断：
  - api/kb.py  import-url / import-batch / download
  - kb/importer.py  抓取网页正文、下载文件

判断口径（宁可错杀）：
  1. 只放行 http / https；
  2. 主机名必须能解析，且**解析出来的每一个 IP** 都不能是内网 / 回环 /
     链路本地 / 保留段 —— 只看第一个 IP 是不够的，DNS 可以返回多个；
  3. 拒绝带用户信息的形式（http://user:pass@host），那是绕过校验的老套路；
  4. 不跟随重定向自己判断：重定向交给请求库时可能跳进内网，
     所以调用方要用 follow_redirects=False 或自己逐跳校验。
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

# 云厂商元数据服务是 SSRF 最常被打的靶子，单独列出来便于以后加白名单
_METADATA_HOSTS = {"metadata.google.internal", "metadata", "instance-data"}


class UnsafeURLError(ValueError):
    """URL 不允许被服务端访问。对外直接当作 400 返回。"""


def _is_bad_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """只要命中任意一条就判危险。"""
    if isinstance(ip, ipaddress.IPv6Address):
        # ::1 回环、fc00::/7 唯一本地、fe80::/10 链路本地、::ffff:0:0/96 IPv4 映射
        if ip.ipv4_mapped is not None:
            return _is_bad_ip(ip.ipv4_mapped)
        return (
            ip.is_loopback
            or ip.is_link_local
            or ip.is_private
            or ip.is_reserved
            or ip.is_unspecified
            or ip.is_multicast
        )
    return (
        ip.is_loopback        # 127.0.0.0/8
        or ip.is_link_local   # 169.254.0.0/16 ← 云元数据 169.254.169.254 就在这
        or ip.is_private      # 10/8、172.16/12、192.168/16
        or ip.is_reserved
        or ip.is_unspecified  # 0.0.0.0
        or ip.is_multicast
        # 没有 is_broadcast：那是 IPv4Network 的属性，地址对象上没有。
        # 255.255.255.255 落在 240.0.0.0/4 里，已经被 is_reserved 覆盖。
    )


def assert_safe_url(url: str) -> str:
    """校验一个 URL 可以被服务端请求。不合法抛 UnsafeURLError，合法则原样返回。

    注意：这里的结论只对"解析这一刻"有效。攻击者可以用 DNS rebinding
    （第一次解析给公网 IP、真正连接时给内网 IP）绕过；单机作业做到这一步
    已经能挡掉绝大多数误伤与脚本扫描，真要防 rebinding 得在连接层用
    socket 绑定解析结果再握手，不在本文件的职责范围内。
    """
    if not url or not isinstance(url, str):
        raise UnsafeURLError("URL 为空")

    parsed = urlparse(url.strip())
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise UnsafeURLError(f"仅支持 http/https 链接，当前为：{scheme or '空'}")

    if parsed.username or parsed.password:
        raise UnsafeURLError("URL 中不允许带用户名密码（http://user:pass@host）")

    host = (parsed.hostname or "").strip().rstrip(".")
    if not host:
        raise UnsafeURLError("URL 中缺少主机名")
    if host.lower() in _METADATA_HOSTS:
        raise UnsafeURLError("不允许访问实例元数据服务")

    # 纯 IP 字面量（含十进制 / 十六进制写法）先直接判，省一次 DNS
    try:
        if _is_bad_ip(ipaddress.ip_address(host)):
            raise UnsafeURLError(f"不允许访问内网地址：{host}")
        return url
    except ValueError:
        pass  # 不是 IP，走 DNS

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise UnsafeURLError(f"域名无法解析：{host}")

    if not infos:
        raise UnsafeURLError(f"域名解析结果为空：{host}")

    for info in infos:
        raw = info[4][0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            raise UnsafeURLError(f"域名解析出异常地址：{host} -> {raw}")
        if _is_bad_ip(ip):
            raise UnsafeURLError(f"域名 {host} 解析到内网地址 {ip}，拒绝访问")

    return url
