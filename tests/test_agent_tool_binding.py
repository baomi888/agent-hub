"""工具绑定：能力开关 → 交给模型哪几个工具（agent/builder.py）。

这块是 2026-10-30 改造的落点之一：用户不再选工具，只开「检索 / 智能联网」两
个开关，具体调哪个工具、调几次由模型决定。所以"开关到工具集"的映射必须钉死，
否则会出现"关了联网它还在搜"或者"开了检索却查不到库"。

不编译 Agent 图（那需要真 LLM），只测纯粹的决策函数与清单一致性。
"""

from __future__ import annotations

import pytest

from agent import builder
from agent.tools import AGENT_TOOLS

ALL_NAMES = {t.name for t in AGENT_TOOLS}


def _bound(*, kb_bound: bool, use_web: bool) -> set[str]:
    return {n for n in ALL_NAMES if builder._tool_allowed(n, kb_bound=kb_bound, use_web=use_web)}


def test_tool_set_has_no_calculator():
    """算数工具于 2026-10-30 移除，天气保留。"""
    assert "calculator" not in ALL_NAMES
    assert "get_weather" in ALL_NAMES
    assert ALL_NAMES == {"kb_search", "web_search", "get_weather", "download_file"}


@pytest.mark.parametrize(
    ("kb_bound", "use_web", "expect"),
    [
        # 都不开：只剩永远可用的天气（只读感知，不会凭空增加开销）
        (False, False, {"get_weather"}),
        (True, False, {"kb_search", "get_weather"}),
        # 联网开时连同下载一起绑：download_file 依赖 web_search 产出的允许清单
        (False, True, {"web_search", "download_file", "get_weather"}),
        (True, True, {"kb_search", "web_search", "download_file", "get_weather"}),
    ],
)
def test_switch_to_tools(kb_bound, use_web, expect):
    assert _bound(kb_bound=kb_bound, use_web=use_web) == expect


def test_kb_search_needs_both_switch_and_kb():
    """开了检索但没绑库时不该给 kb_search：给了它调也是空。"""
    assert builder._tool_allowed("kb_search", kb_bound=False, use_web=True) is False
    assert builder._tool_allowed("kb_search", kb_bound=True, use_web=False) is True


def test_weather_not_gated_by_switches():
    for kb_bound in (True, False):
        for use_web in (True, False):
            assert builder._tool_allowed("get_weather", kb_bound=kb_bound, use_web=use_web)


def test_cache_key_distinguishes_switches():
    """开关进缓存键：否则换了开关仍命中上一轮编译好的图（工具集和 prompt 都是旧的）。"""
    def key(*, kb_bound: bool, use_web: bool, ver: int = 0):
        return builder._cache_key(
            kb_bound=kb_bound,
            use_web=use_web,
            scene="",
            activated=frozenset(),
            prompt_version=ver,
        )

    assert key(kb_bound=True, use_web=True) != key(kb_bound=True, use_web=False)
    assert key(kb_bound=True, use_web=True) != key(kb_bound=False, use_web=True)
    assert key(kb_bound=False, use_web=False) != key(kb_bound=False, use_web=True)
    assert key(kb_bound=True, use_web=True, ver=0) != key(kb_bound=True, use_web=True, ver=1)
    assert key(kb_bound=True, use_web=True) == key(kb_bound=True, use_web=True)


def test_build_agent_accepts_switch_kwargs():
    import inspect

    params = inspect.signature(builder.build_agent).parameters
    assert "use_kb" in params and "use_web" in params
    assert params["use_kb"].default is True
    assert params["use_web"].default is True


def test_prompt_mentions_switch_absence():
    """提示词必须在"没开"时明说，否则模型会假装调用不存在的工具。"""
    text = builder.SYSTEM_PROMPT
    assert "本轮未开启" not in text, "静态 prompt 里不该写死某一轮的状态"
    assert "由用户开启的开关决定" in text
    assert "calculator" not in text
    assert "get_weather" in text
