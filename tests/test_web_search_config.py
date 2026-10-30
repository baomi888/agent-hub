"""回归：web_search 的形参 `config` 不能被函数内的 `from core import config` 覆盖。

2026-10-30 线上现象：用户问「帮我联网搜索今天的新闻」，Agent 永远回
「联网搜索暂时不可用」。真实情况是 **Serper 请求成功了**（4 秒、有 10 条结果、
按次计费已经扣掉），但紧接着这一行：

    allow = _configurable(config).get("download_allowlist")

把 `config` 当成了 dict，而它此刻已经被函数体内的

    from core import config

覆盖成了 `core.config` **模块对象**（Python 的函数作用域规则：函数内任何
import 都会在局部命名空间创建同名变量，遮蔽同名形参）。

于是 `(模块对象).get(...)` 抛 `AttributeError: module 'core.config' has no
attribute 'get'`，被 `except Exception` 吞成一句"暂时不可用"。用户以为等一会儿
就好，实际是确定性失败；这笔开销还一直在烧 Serper 的额度。

修法是 `from core import config as app_config`（download_file 早就用 `as _cfg`
避开了这个坑，web_search 这处漏了）。

本用例钉住两件事：
  ① 搜索成功时，返回的是**结果正文**，不是"不可用"；
  ② 注入的 `download_allowlist` 真的被追加了 —— 这一条正是被 bug 吃掉的行为，
     撤掉修复就变红，避免写成恒真断言。
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

_FAKE = {
    "organic": [
        {"title": "标题一", "snippet": "摘要一", "link": "https://example.com/a"},
        {"title": "标题二", "snippet": "摘要二", "link": "https://example.com/b"},
    ]
}


@pytest.fixture(autouse=True)
def _fake_api_key(monkeypatch):
    """钉一个假 key，让这几条用例不依赖本机 / CI 的 `.env`。

    没有 key 时 `web_search` 会在**发请求之前**就返回「需要 SERPER_API_KEY」，
    于是四条用例全挂在最前面那道守卫上，测不到它们真正想测的形参遮蔽。
    `search_serper` 本来就被每条用例 mock 掉了，这个 key 不会真的被用出去。
    """
    from core import config

    monkeypatch.setattr(config, "SERPER_API_KEY", "test-key-not-used")


def _invoke(query: str, configurable: dict):
    from agent.tools import web_search

    return web_search.invoke(
        {"query": query, "max_results": 5},
        config={"configurable": configurable},
    )


def test_搜索成功时不能回不可用字样(monkeypatch):
    import core.serper as serper

    monkeypatch.setattr(serper, "search_serper", lambda q, n: dict(_FAKE))
    out = _invoke("今天的新闻", {"download_allowlist": []})
    assert "不可用" not in out, out
    assert "标题一" in out and "https://example.com/a" in out, out


def test_注入的允许清单要真的被写入(monkeypatch):
    """这一条才是 bug 的直接证据：bug 时这里抛 AttributeError，清单一条都不进。"""
    import core.serper as serper

    monkeypatch.setattr(serper, "search_serper", lambda q, n: dict(_FAKE))
    allow: list = []
    out = _invoke("今天的新闻", {"download_allowlist": allow})
    assert "不可用" not in out, out
    assert allow == ["https://example.com/a", "https://example.com/b"], allow


def test_没有注入清单时也不能崩(monkeypatch):
    """configurable 为空（测试 / 脚本直调）时 `_configurable` 回空 dict，不该抛。"""
    import core.serper as serper

    monkeypatch.setattr(serper, "search_serper", lambda q, n: dict(_FAKE))
    out = _invoke("今天的新闻", {})
    assert "不可用" not in out, out
    assert "标题一" in out, out


def test_搜索失败时文案仍如实(monkeypatch):
    """search_serper 返回 None（真·网络失败）时，"暂时不可用"这句话是对的。"""
    import core.serper as serper

    monkeypatch.setattr(serper, "search_serper", lambda q, n: None)
    out = _invoke("今天的新闻", {"download_allowlist": []})
    assert "暂时不可用" in out, out


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
