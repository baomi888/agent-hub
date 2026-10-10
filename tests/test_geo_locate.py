# -*- coding: utf-8 -*-
"""IP 定位兜底（api/geo.py）的回归测试。

不联网、不需要 AMAP Key：只测纯函数与降级路径。
背景见「部署说明.md 第十四节」—— 公网 http 下浏览器 geolocation 不可用，
只能由服务端按来源 IP 推断城市，这条链路绝不能因为异常把对话打断。

可从任意位置直接跑： python tests/test_geo_locate.py
"""
import os
import sys

# 允许从任意 cwd 运行：把项目根塞进 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from api.geo import _client_ip, _is_public_ip
from core import config

# ---------- 1. 纯函数：公网 IP 判定 ----------
cases = [
    ("203.0.113.25", True),       # 示例公网 IP（RFC 5737 TEST-NET-3，仅作演示）
    ("114.114.114.114", True),    # 南京 DNS
    ("223.5.5.5", True),          # 阿里 DNS
    ("127.0.0.1", False),
    ("192.168.1.5", False),
    ("172.16.0.10", False),       # 示例内网 IP（RFC 1918 私有段）
    ("10.0.0.1", False),
    ("198.51.100.7", False),       # TEST-NET-2，ipaddress 判为 reserved
    ("::1", False),
    ("不是IP", False),
]
for raw, want in cases:
    got = _is_public_ip(raw)
    assert got == want, f"_is_public_ip({raw!r}) = {got}, 期望 {want}"
print("PASS: _is_public_ip 全部符合预期")

# ---------- 2. _client_ip：必须认 X-Forwarded-For 第一跳 ----------
class FakeClient:
    def __init__(self, host):
        self.host = host


class FakeReq:
    def __init__(self, headers=None, host="127.0.0.1"):
        self.headers = headers or {}
        self.client = FakeClient(host)


r = FakeReq({"x-forwarded-for": "223.5.5.5, 10.0.0.1"}, "127.0.0.1")
assert _client_ip(r) == "223.5.5.5", _client_ip(r)
print("PASS: XFF 多跳时取第一跳公网 IP ->", _client_ip(r))

r = FakeReq({}, "127.0.0.1")
assert _client_ip(r) is None
print("PASS: 纯内网访问 -> None（开发环境，没有可查的公网 IP）")

r = FakeReq({"x-real-ip": "114.114.114.114"}, "127.0.0.1")
assert _client_ip(r) == "114.114.114.114"
print("PASS: 只有 X-Real-IP 时也能取到")

# ---------- 3. 路由：无 Key 时的降级 ----------
from main import app  # noqa: E402

client = TestClient(app)
# 定位接口现在也要登录（多用户隔离后所有业务路由默认鉴权），先注册再拿 Cookie
client.post("/api/auth/register/", json={"username": "geotester", "password": "test12345"})
_lg = client.post("/api/auth/login/", json={"username": "geotester", "password": "test12345"})
assert _lg.status_code == 200, (_lg.status_code, _lg.text)

orig = config.AMAP_API_KEY
try:
    config.AMAP_API_KEY = ""
    resp = client.get("/api/geo/locate")
    assert resp.status_code == 200, resp.status_code
    d = resp.json()
    print("无 Key ->", d)
    assert d["ok"] is False and "AMAP_API_KEY" in d["reason"], d
    print("PASS: 无 Key 时 200 + 中文原因，不抛异常")

    # ---------- 4. 路由：有 Key 但来源是内网 ----------
    config.AMAP_API_KEY = "fake-key-for-test"
    resp = client.get("/api/geo/locate")
    d = resp.json()
    print("内网来源 ->", d)
    assert d["ok"] is False and "内网" in d["reason"], d
    print("PASS: 内网来源不会被拿去查高德（避免定位到机房）")
finally:
    config.AMAP_API_KEY = orig

# ---------- 5. 斜杠规矩：走中间件的归一化 ----------
for p in ("/api/geo/locate", "/api/geo/locate/"):
    r2 = client.get(p)
    assert r2.status_code == 200, f"{p} -> {r2.status_code}"
assert "location" not in client.get("/api/geo/locate").headers
print("PASS: /api/geo/locate 与带斜杠版本都是 200（无 3xx）")

print("\n全部通过")
sys.exit(0)
