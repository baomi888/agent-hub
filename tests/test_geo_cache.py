# -*- coding: utf-8 -*-
"""api/geo.py 的按 IP 缓存行为测试（离线，不联网、不需要高德 key）。

验证四件事：
  1. 连续两次同 IP 请求 ⇒ 高德只被调用 1 次（缓存真的生效）
  2. 失败结果**不**缓存 ⇒ 高德抖动一次后，下一次仍会重试（不会把用户卡死 30 分钟）
  3. 缓存有容量上限 ⇒ 灌超过 _CACHE_MAX 个不同 IP 不会无限增长
  4. 过期条目会被丢弃（用 TTL=0 模拟过期）
"""
import asyncio
import time
import types

import api.geo as geo


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


class _Client:
    """替换 httpx.AsyncClient，记录被调了几次。"""

    calls = 0
    payload = {"status": "1", "province": "广东省", "city": "广州市", "adcode": "440100"}
    raise_exc: Exception | None = None

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None):
        type(self).calls += 1
        if type(self).raise_exc is not None:
            raise type(self).raise_exc
        return _Resp(type(self).payload)


def _patch_client():
    import httpx

    httpx.AsyncClient = _Client


def _make_request(ip="8.8.8.8"):
    headers = {"x-forwarded-for": ip}
    return types.SimpleNamespace(headers=headers, client=None)


def _reset():
    geo._cache.clear()
    _Client.calls = 0
    _Client.payload = {"status": "1", "province": "广东省", "city": "广州市", "adcode": "440100"}
    _Client.raise_exc = None


async def main():
    _patch_client()
    geo.config.AMAP_API_KEY = "test-key"  # type: ignore[attr-defined]

    fails = []

    def check(name, cond, extra=""):
        print(f"{'PASS' if cond else 'FAIL'}  {name}  {extra}")
        if not cond:
            fails.append(name)

    # 1. 同 IP 两次 ⇒ 高德只调一次
    _reset()
    r1 = await geo.locate(_make_request("8.8.8.8"))
    r2 = await geo.locate(_make_request("8.8.8.8"))
    check("同 IP 两次请求，高德只被调用 1 次", _Client.calls == 1, f"(实际 {_Client.calls} 次)")
    check("两次结果一致", (r1.city, r1.ok) == (r2.city, r2.ok), f"({r1.city}/{r1.ok})")
    check("结果 ok=true 且城市=广州市", r1.ok and r1.city == "广州市")

    # 2. 不同 IP ⇒ 各自查一次（缓存不能串号）
    _reset()
    await geo.locate(_make_request("8.8.8.8"))
    await geo.locate(_make_request("1.1.1.1"))
    check("两个不同 IP ⇒ 调 2 次", _Client.calls == 2, f"(实际 {_Client.calls} 次)")

    # 3. 失败不缓存：第一次失败、第二次成功
    _reset()
    _Client.raise_exc = RuntimeError("高德超时")
    f1 = await geo.locate(_make_request("9.9.9.9"))
    check("失败时 ok=false 且给中文 reason", (not f1.ok) and bool(f1.reason), f"({f1.reason})")
    _Client.raise_exc = None
    ok2 = await geo.locate(_make_request("9.9.9.9"))
    check("失败后重试能成功（失败没被缓存）", ok2.ok and ok2.city == "广州市", f"(city={ok2.city})")
    check("失败那次确实打到了高德", _Client.calls == 2, f"(实际 {_Client.calls} 次)")

    # 4. 容量上限：灌 2 倍容量，字典不应超过上限
    _reset()
    old_max = geo._CACHE_MAX
    geo._CACHE_MAX = 10
    try:
        for i in range(25):
            await geo.locate(_make_request(f"1.1.1.{i}"))
        check(
            f"灌 25 个 IP（上限 10）后缓存不超上限",
            len(geo._cache) <= 10,
            f"(实际 {len(geo._cache)})",
        )
    finally:
        geo._CACHE_MAX = old_max

    # 5. 过期条目被丢弃
    #
    # ⚠️ 断言只能写「过期后会重查」和「读到 None」，
    # **不能**写「查完之后缓存里没有这个键」——
    # 因为流程是「读时发现过期→删→调高德→又写回新结果」，
    # 查完它当然还在缓存里，只是存的是**新值**而不是陈旧值。
    # （第一版我就是这么写错的，被测试抓出来了。）
    _reset()
    old_ttl = geo._CACHE_TTL
    geo._CACHE_TTL = 0.0
    try:
        await geo.locate(_make_request("5.5.5.5"))
        check("TTL=0 时每次都重查（过期即重取）", _Client.calls == 1)
        # 关键：手动塞一个「已过期」条目，_cache_get 必须返回 None 并清掉它
        geo._cache["6.6.6.6"] = (time.monotonic() - 9999, geo.GeoOut(ok=True, city="旧城市"))
        got = geo._cache_get("6.6.6.6")
        check("读到过期条目返回 None（不用陈旧值）", got is None, f"(实际 {got})")
        check("过期条目被顺手清掉", "6.6.6.6" not in geo._cache)
    finally:
        geo._CACHE_TTL = old_ttl

    # ⚠️ 「未过期条目正常命中」必须放在 TTL 还原**之后**：
    # 第一版把它写在与 TTL=0 同一个 try 块里，于是刚塞进去的"新条目"
    # 相对 TTL=0 立刻就是过期的 ⇒ 断言必挂。
    # ⇒ 改 TTL 的用例要在 finally 里还原，且新用例另起一段，别复用同一份全局状态。
    geo._cache["7.7.7.7"] = (time.monotonic(), geo.GeoOut(ok=True, city="新城市"))
    got2 = geo._cache_get("7.7.7.7")
    check("未过期条目正常命中", got2 is not None and got2.city == "新城市")

    _reset()
    print(f"\n{'全部通过' if not fails else '失败: ' + ', '.join(fails)}")
    return 1 if fails else 0


def test_geo_cache_behaviour():
    """geo /locate 的缓存行为（pytest 入口，见 main() 里的逐条断言）。"""
    assert asyncio.run(main()) == 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
