"""回归：登录 Cookie 的 Secure 标志必须随请求协议生效（2026-10-20 修复）。

历史 bug：``core/auth.py::set_session_cookie`` 的注释写着「Secure only on https」，
但 ``kwargs`` 里根本没有 ``secure`` 字段，等于**永远不加 Secure**。
后果：上 HTTPS 后会话 Cookie 仍可能在 http 下被带出，或被部分浏览器拒存，登录态丢失。

本测试锁死四条：
  1) http 请求不写 Secure；
  2) 直接 https（``request.url.scheme``）写 Secure；
  3) 经 nginx 反代（``X-Forwarded-Proto: https``）写 Secure（uvicorn 未开 --proxy-headers 时的关键路径）；
  4) HttpOnly 始终存在。
"""

from __future__ import annotations

from fastapi import Response

from core.auth import _is_secure_request, set_session_cookie


class _URL:
    def __init__(self, scheme: str) -> None:
        self.scheme = scheme


class _Req:
    """够用的 Request 替身：set_session_cookie 只读 headers 与 url.scheme。"""

    def __init__(self, scheme: str = "http", headers: dict | None = None) -> None:
        self.url = _URL(scheme)
        self.headers = headers or {}


def _cookie_header(req=None) -> str:
    resp = Response()
    if req is None:
        set_session_cookie(resp, "tok")
    else:
        set_session_cookie(resp, "tok", req)
    return resp.headers.get("set-cookie", "")


def test_http_has_no_secure():
    h = _cookie_header(_Req("http"))
    assert "baomi_session=tok" in h
    assert "secure" not in h.lower()


def test_direct_https_sets_secure():
    assert "secure" in _cookie_header(_Req("https")).lower()


def test_reverse_proxy_https_sets_secure():
    # nginx 把 TLS 终结后转发明文，uvicorn 只会看到 X-Forwarded-Proto
    assert "secure" in _cookie_header(_Req("http", {"x-forwarded-proto": "https"})).lower()


def test_forwarded_proto_first_value_wins():
    assert "secure" in _cookie_header(_Req("http", {"x-forwarded-proto": "https, http"})).lower()


def test_forwarded_http_does_not_set_secure():
    # 头明确说 http 时，不能被 scheme=https 误导
    assert "secure" not in _cookie_header(_Req("https", {"x-forwarded-proto": "http"})).lower()


def test_no_request_defaults_insecure():
    # 兼容旧调用（不传 request）：保守按 http 处理，避免本地调试丢 Cookie
    assert "secure" not in _cookie_header(None).lower()


def test_httponly_always_present():
    assert "httponly" in _cookie_header(_Req("http")).lower()
    assert "httponly" in _cookie_header(_Req("https")).lower()


def test_is_secure_request_helper():
    assert _is_secure_request(None) is False
    assert _is_secure_request(_Req("https")) is True
    assert _is_secure_request(_Req("http")) is False
    assert _is_secure_request(_Req("http", {"x-forwarded-proto": "https"})) is True
