# -*- coding: utf-8 -*-
"""行为基线（characterization tests）：天气工具的城市别名与天气码映射。

天气工具把两张映射表（`CITY_ALIASES` 城市别名、`WMO_CN` WMO 天气码→中文）
和三个接口地址**写在函数体内**，每次调用都重建。把它们提到模块级是稳妥的
性能/整洁改动（字面量、无变异），但必须先有断言钉住"这两张表产出的文本"，
否则搬动时抄错一个字都不会有人发现。

不联网：`urllib.request.urlopen` 换成本地假响应。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import urllib.request  # noqa: E402

import agent.tools as tools  # noqa: E402


class _FakeResp:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _stub_urlopen(monkeypatch, payload_for):
    """payload_for(url) -> dict；同时记录所有请求 URL 供断言。"""
    seen: list[str] = []

    def _fake(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        seen.append(url)
        return _FakeResp(payload_for(url))

    monkeypatch.setattr(urllib.request, "urlopen", _fake)
    return seen


# ==================== 坐标路径：WMO 天气码 → 中文 + 输出文本格式 ====================

def test_weather_coordinate_current_day_format(monkeypatch):
    monkeypatch.setattr(tools, "_get_amap_key", lambda: "")  # 无高德 Key：逆地理拿不到，标签走兜底

    payload = {
        "current": {
            "weather_code": 61, "temperature_2m": 21, "apparent_temperature": 20,
            "relative_humidity_2m": 55, "wind_direction_10m": "东南", "wind_speed_10m": 12.5,
        }
    }
    _stub_urlopen(monkeypatch, lambda url: payload)

    out = tools.get_weather.invoke({"days": 1, "lat": 39.90, "lon": 116.40})

    # WMO 61 → "小雨"；格式逐字钉住（空行、单位、标点都不能变）
    assert out == (
        "你所在位置当前小雨，气温 21℃（体感 20℃），湿度 55%，东南风约 12.5 km/h。"
    )


def test_weather_coordinate_daily_format(monkeypatch):
    monkeypatch.setattr(tools, "_get_amap_key", lambda: "")

    payload = {
        "current": {"weather_code": 0},
        "daily": {
            "time": ["2026-10-16", "2026-10-17", "2026-10-18"],
            "weather_code": [0, 3, 95],
            "temperature_2m_max": [25, 24, 22],
            "temperature_2m_min": [15, 14, 13],
        },
    }
    _stub_urlopen(monkeypatch, lambda url: payload)

    lines = tools.get_weather.invoke({"days": 3, "lat": 39.90, "lon": 116.40}).split("\n")

    assert lines[0] == "你所在位置（39.90,116.40）未来 3 天天气（Open-Meteo）："
    assert lines[1] == "10月16日 周五：晴，15~25℃。"
    assert lines[2] == "10月17日 周六：阴，14~24℃。"
    assert lines[3] == "10月18日 周日：雷阵雨，13~22℃。"


# ==================== 城市路径：别名归一 ====================

def test_city_alias_normalizes_pinyin_input(monkeypatch):
    """`beijing` 必须被折成「北京」再去查地理编码（大小写无关）。"""
    monkeypatch.setattr(tools, "_get_amap_key", lambda: "fake-key")
    tools._AMAP_CACHE.clear()

    def _payload(url: str) -> dict:
        if "/geocode/geo" in url:
            return {"status": "1", "geocodes": [{"adcode": "110000"}]}
        return {"status": "1", "lives": [{"city": "北京", "weather": "晴", "temperature": "21"}]}

    seen = _stub_urlopen(monkeypatch, _payload)

    out = tools.get_weather.invoke({"city": "BEIJING", "days": 1})

    geo_urls = [u for u in seen if "/geocode/geo" in u]
    assert geo_urls, seen
    assert "address=%E5%8C%97%E4%BA%AC" in geo_urls[0]  # 别名表把 BEIJING 折成了「北京」
    # 后续应答形状不匹配时的兜底文案（本次 stub 给的就是不完整应答）
    assert out == "未找到城市 'BEIJING'，请确认地名（仅支持中国省/市/区县）。"


def test_weather_without_city_or_coords(monkeypatch):
    monkeypatch.setattr(tools, "_get_amap_key", lambda: "fake-key")
    assert tools.get_weather.invoke({}) == (
        "缺少城市名或经纬度，无法查询天气。请指明城市，或在支持定位的环境开启定位。"
    )


def test_weather_day_range_validation(monkeypatch):
    monkeypatch.setattr(tools, "_get_amap_key", lambda: "fake-key")
    assert tools.get_weather.invoke({"days": 0, "lat": 1.0, "lon": 1.0}) == \
        "天数必须是 1~7 的整数，收到的是 0。"
    assert tools.get_weather.invoke({"days": 8, "lat": 1.0, "lon": 1.0}) == \
        "天数必须是 1~7 的整数，收到的是 8。"


# ==================== 城市路径：5~7 天必须走 Open-Meteo ====================
# 这一段是 2026-10-23 审计补的回归。此前的测试**全部走坐标路径**（lat/lon），
# 城市路径的 5~7 天分支一次都没被执行过，于是下面这个 bug 活了很久：
#   `if casts:` 恒为真（高德 free 版 casts 恒 4 条）⇒ 直接 return ⇒
#   描述里承诺的"5~7 天自动走 Open-Meteo"那段代码**永远到不了**；
#   更糟的是标题写的是请求的 days（如 6），下面只有 4 行，模型会照标题补两天。

_DATES = ["2026-10-23", "2026-10-24", "2026-10-25", "2026-10-26",
          "2026-10-27", "2026-10-28", "2026-10-29"]


def _amap_casts(n: int = 4) -> list[dict]:
    """高德 extensions=all 的真实形状：免费版最多给 4 天。"""
    return [{"date": d, "dayweather": "晴", "nightweather": "晴",
             "daytemp": "20", "nighttemp": "10", "daywind": "南", "daypower": "3"}
            for d in _DATES[:n]]


def _city_payload(amap_days: int = 4, open_meteo: bool = True):
    """构造一份完整的城市路径应答（geocode 必须带 location，否则 _resolve 会判未找到）。"""

    def _payload(url: str) -> dict:
        if "/geocode/geo" in url:
            return {"status": "1", "geocodes": [{
                "adcode": "110000", "province": "北京市", "city": "北京市",
                "district": "", "location": "116.407,39.904",
            }]}
        if "weatherInfo" in url:
            if "extensions=base" in url:
                return {"status": "1", "lives": [{
                    "weather": "晴", "temperature": "20", "humidity": "40",
                    "winddirection": "南", "windpower": "3",
                }]}
            return {"status": "1", "forecasts": [{"casts": _amap_casts(amap_days)}]}
        if "open-meteo" in url:
            if not open_meteo:
                raise RuntimeError("open-meteo 模拟故障")
            return {"daily": {
                "time": _DATES,
                "weather_code": [0, 1, 2, 3, 61, 63, 65],
                "temperature_2m_max": [25, 24, 23, 22, 21, 20, 19],
                "temperature_2m_min": [15, 14, 13, 12, 11, 10, 9],
            }}
        raise RuntimeError("未预期的 URL: " + url)

    return _payload


def _date_rows(out: str) -> list[str]:
    return [ln for ln in out.split("\n") if "月" in ln and "日" in ln]


def test_weather_city_5days_uses_open_meteo(monkeypatch):
    """★ P0：高德只给 4 天时，问 5 天必须改走 Open-Meteo，且真的给 5 行。"""
    monkeypatch.setattr(tools, "_get_amap_key", lambda: "fake-key")
    tools._AMAP_CACHE.clear()
    seen = _stub_urlopen(monkeypatch, _city_payload(amap_days=4))

    out = tools.get_weather.invoke({"city": "北京", "days": 5})

    assert any("open-meteo" in u for u in seen), f"没走 Open-Meteo，请求了：{seen}"
    assert out.split("\n")[0] == "北京市未来 5 天天气预报（Open-Meteo）："
    assert len(_date_rows(out)) == 5, out


def test_weather_city_7days_gives_7_rows(monkeypatch):
    """上界也要钉住：问 7 天得 7 行（别只测 5 天就以为好了）。"""
    monkeypatch.setattr(tools, "_get_amap_key", lambda: "fake-key")
    tools._AMAP_CACHE.clear()
    _stub_urlopen(monkeypatch, _city_payload(amap_days=4))

    out = tools.get_weather.invoke({"city": "北京", "days": 7})

    assert len(_date_rows(out)) == 7, out


def test_weather_city_4days_still_uses_amap(monkeypatch):
    """回归：4 天以内仍走高德，不要为了修 5~7 天把 2~4 天也改走 Open-Meteo。"""
    monkeypatch.setattr(tools, "_get_amap_key", lambda: "fake-key")
    tools._AMAP_CACHE.clear()
    seen = _stub_urlopen(monkeypatch, _city_payload(amap_days=4))

    out = tools.get_weather.invoke({"city": "北京", "days": 4})

    assert not any("open-meteo" in u for u in seen), f"4 天不该走 Open-Meteo：{seen}"
    assert "（高德天气预报）" in out
    assert len(_date_rows(out)) == 4, out


def test_weather_city_title_never_lies(monkeypatch):
    """★ 最狠的一条：两个源都拿不全时，标题的天数必须等于实际行数。

    以前标题写请求的 days（6），下面 4 行 ⇒ 模型会照"未来 6 天"补两天编出来。
    """
    monkeypatch.setattr(tools, "_get_amap_key", lambda: "fake-key")
    tools._AMAP_CACHE.clear()
    _stub_urlopen(monkeypatch, _city_payload(amap_days=4, open_meteo=False))

    out = tools.get_weather.invoke({"city": "北京", "days": 6})

    assert "未来 6 天" not in out, f"标题不能写 6 天：{out}"
    assert "未来 4 天天气预报" in out, out
    assert len(_date_rows(out)) == 4, out
    assert "仅能覆盖 4 天" in out, out


def test_weather_city_1day_unaffected(monkeypatch):
    """1 天是实时接口，不能被这次改动碰到。"""
    monkeypatch.setattr(tools, "_get_amap_key", lambda: "fake-key")
    tools._AMAP_CACHE.clear()
    _stub_urlopen(monkeypatch, _city_payload())

    out = tools.get_weather.invoke({"city": "北京", "days": 1})

    assert "当前晴" in out and "（高德实时数据）" in out, out
