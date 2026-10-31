"""web_search 工具的行为回归：形参 config 与 `from core import config` 的名字遮蔽。

背景（2026-10-30 排查）：web_search 的形参叫 config（RunnableConfig，LangChain 注入）。
2026-10-29 的安全修复（cf49ef2）给函数加了 `config: RunnableConfig = None` 形参，
但函数体第一行仍是 `from core import config` —— 这会把形参覆盖成 core.config 模块。
于是 `_configurable(config)` 对模块调用 `.get()` 抛 AttributeError，被函数末尾的
裸 `except Exception` 吞掉，**只要配了 SERPER_API_KEY，每次联网搜索都返回
「联网搜索暂时不可用」，且 Serper 请求已经发出（照扣额度、结果丢弃）**。

本文件是这条 bug 的回归锁：
  1. 有 key + Serper 返回结果 → 必须返回 Markdown 列表，且把链接写进下载允许清单；
  2. 无 key → 返回配置缺失文案（不是"暂时不可用"）；
  3. Serper 返回空结果 → 返回"未找到"文案。

不联网：monkeypatch `core.serper.search_serper`。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent.tools as tools  # noqa: E402
from core import config  # noqa: E402


def _fake_search(payload):
    calls = []

    def _search(query, count=10):
        calls.append((query, count))
        return payload

    return _search, calls


def test_web_search_returns_results_and_fills_allowlist(monkeypatch):
    """核心回归：配了 key 且 Serper 成功时，不能再返回"暂不可用"。"""
    monkeypatch.setattr(config, "SERPER_API_KEY", "test-key")
    payload = {
        "organic": [
            {"title": "标题一", "snippet": "摘要一", "link": "https://example.com/a.pdf"},
            {"title": "标题二", "snippet": "摘要二", "link": "https://example.com/b.pdf"},
        ]
    }
    fake, calls = _fake_search(payload)
    monkeypatch.setattr("core.serper.search_serper", fake)

    allow: list[str] = []
    out = tools.web_search.func(
        query="向量检索 原理",
        max_results=5,
        config={"configurable": {"download_allowlist": allow}},
    )

    assert "联网搜索暂时不可用" not in out, f"名字遮蔽 bug 回归了：{out!r}"
    assert "标题一" in out and "https://example.com/a.pdf" in out
    assert "标题二" in out
    assert len(calls) == 1 and calls[0][1] == 5
    # 下载允许清单由服务端写入，download_file 严格模式依赖它
    assert allow == ["https://example.com/a.pdf", "https://example.com/b.pdf"]


def test_web_search_without_config_still_works(monkeypatch):
    """config 缺省（脚本/单测直接调用）时也不能崩。"""
    monkeypatch.setattr(config, "SERPER_API_KEY", "test-key")
    fake, _ = _fake_search({"organic": [{"title": "T", "link": "https://e.com/x"}]})
    monkeypatch.setattr("core.serper.search_serper", fake)

    out = tools.web_search.func(query="q", max_results=3)
    assert "T" in out and "https://e.com/x" in out


def test_web_search_missing_api_key_message(monkeypatch):
    """没配 key → 明确的配置缺失文案，而不是"暂时不可用"。"""
    monkeypatch.setattr(config, "SERPER_API_KEY", "")
    out = tools.web_search.func(query="q")
    assert "SERPER_API_KEY" in out
    assert "暂时不可用" not in out


def test_web_search_empty_results(monkeypatch):
    monkeypatch.setattr(config, "SERPER_API_KEY", "test-key")
    fake, _ = _fake_search({"organic": []})
    monkeypatch.setattr("core.serper.search_serper", fake)
    out = tools.web_search.func(query="没有结果的问题")
    assert "未找到" in out
