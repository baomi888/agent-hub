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
            "weather_code": 61,
            "temperature_2m": 21,
            "apparent_temperature": 20,
            "relative_humidity_2m": 55,
            "wind_direction_10m": "东南",
            "wind_speed_10m": 12.5,
        }
    }
    _stub_urlopen(monkeypatch, lambda url: payload)

    out = tools.get_weather.invoke({"days": 1, "lat": 39.90, "lon": 116.40})

    # WMO 61 → "小雨"；格式逐字钉住（空行、单位、标点都不能变）
    assert out == ("你所在位置当前小雨，气温 21℃（体感 20℃），湿度 55%，东南风约 12.5 km/h。")


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
    assert (
        tools.get_weather.invoke({"days": 0, "lat": 1.0, "lon": 1.0})
        == "天数必须是 1~7 的整数，收到的是 0。"
    )
    assert (
        tools.get_weather.invoke({"days": 8, "lat": 1.0, "lon": 1.0})
        == "天数必须是 1~7 的整数，收到的是 8。"
    )
