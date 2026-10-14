"""`location_note` 的经纬度归一化（2026-10-14 新增）。

背景：这段提示词片段会被**整段**当 Agent 图缓存键
（`build_agent` 里 `key = (bool(has_kb), note, scene)`）。
浏览器 `navigator.geolocation` 给 6 位小数，同一张桌子上刷新两次末位就变
⇒ 键永不命中 ⇒ 每次 miss 都在 `_AGENT_LOCK` 里重新编译整张图。

修法：在 `location_note` 内部把坐标收成 1 位小数（≈11km，城市级精度）。
本用例锁住三件事：① 末位抖动不再改变 key；② 提示词里只剩 1 位小数；
③ 特殊输入不崩（非数字 / 0 / 只给一半坐标）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.builder import location_note  # noqa: E402


def test_last_digit_jitter_keeps_same_key():
    """核心回归：同一个人的两次定位只差末位小数，生成的 prompt 必须完全相同。"""
    a = location_note({"city": "广州", "lat": 23.129163, "lon": 113.264435})
    b = location_note({"city": "广州", "lat": 23.129184, "lon": 113.264521})
    assert a == b
    assert a != ""


def test_prompt_shows_one_decimal():
    note = location_note({"city": "广州", "lat": 23.129163, "lon": 113.264435})
    assert "23.1, 113.3" in note
    assert "23.129163" not in note


def test_string_coords_normalized_too():
    """前端如果把坐标当字符串传过来，也要得到同一套结果（否则又是两个键）。"""
    num = location_note({"city": "北京", "lat": 39.9042, "lon": 116.4074})
    txt = location_note({"city": "北京", "lat": "39.9042", "lon": "116.4074"})
    assert num == txt
    assert "39.9, 116.4" in txt


def test_only_coords_branch_also_coarse():
    """第二条分支（只有经纬度、没反查出城市）同样要归一。"""
    note = location_note({"lat": 31.230456, "lon": 121.473789})
    assert "31.2, 121.5" in note
    assert "未反查出城市名" in note


def test_city_only_branch_has_no_coords():
    note = location_note({"city": "上海"})
    assert "上海" in note
    assert "经纬度" not in note


def test_bad_input_does_not_raise():
    """脏数据不能把整轮对话搞崩 —— 原样留着就好。"""
    note = location_note({"city": "", "lat": "abc", "lon": None})
    assert note == ""  # city 空、lon 是 None ⇒ has_xy 不成立 ⇒ 空串

    note2 = location_note({"city": "某地", "lat": "abc", "lon": 1.0})
    assert "某地" in note2  # 有城市名就仍可用

    note3 = location_note({"lat": "abc", "lon": "def"})
    assert "abc" in note3  # 走「只有坐标」分支，原样透出，不抛异常


def test_zero_coord_counts_as_present():
    """0 是合法坐标（赤道 / 本初子午线），不能因为它是 falsy 就被当成没有。"""
    note = location_note({"lat": 0, "lon": 0})
    assert "0.0, 0.0" in note


def test_empty_and_none():
    assert location_note(None) == ""
    assert location_note({}) == ""
