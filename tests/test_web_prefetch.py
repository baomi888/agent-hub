"""联网预检索（用户开了「智能联网」→ 服务端先搜一轮）的回归。

为什么需要这层测试
------------------
2026-11-03 用户实测「广东石油化工学院周边有什么好吃的店」：底部「智能联网」
是**开着**的（截图淡金实心底），但回答里一个来源都没有，正文却写着
「我搜到的」「高德 4.55 分」「519 个摊位」—— 全是模型编的。

根因是「搜不搜由模型自主决定」。知识库那边早就有对应的设计原则
（`api/chat.py`：用户明确开了检索，不能靠模型自觉，所以结构上先检索一轮），
联网漏了同一条。修法是给联网也加结构化预检索。

本文件锁住三件事：
  1. 跳过判据：寒暄/纯生成/极短追问不搜；带年份的一律搜（时效信息）
  2. 预检索真的把结果编号入袋，且与 web_search 工具**共用同一套编号**
     （否则来源卡 id 与正文 [Wn] 会错位）
  3. 失败一律降级成"没有结果"，不许抛异常打断整轮提问
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent.tools as tools  # noqa: E402
from api import chat as chat_api  # noqa: E402
from core import config  # noqa: E402


def _payload(*links):
    return {
        "organic": [
            {"title": f"标题{i}", "snippet": f"摘要{i}", "link": url, "date": "2026-11-01"}
            for i, url in enumerate(links, 1)
        ]
    }


def _patch_search(monkeypatch, payload):
    monkeypatch.setattr(config, "SERPER_API_KEY", "test-key")
    calls = []

    def _search(query, count=10):
        calls.append((query, count))
        return payload

    monkeypatch.setattr("core.serper.search_serper", _search)
    return calls


# ---------------- 1. 跳过判据 ----------------


def test_skip_prefetch_for_greeting_and_rewrite():
    """纯寒暄、纯改写/生成类不需要外部事实，搜了也是白花一次往返。"""
    for q in ("你好", "你好呀", "谢谢", "帮我翻译这段话", "润色一下上面这段", "继续", "详细点"):
        assert chat_api._skip_prefetch_web(q) is True, q


def test_skip_prefetch_short_question():
    """极短追问说的是上一轮的内容，再搜一次没有意义。"""
    assert chat_api._skip_prefetch_web("嗯？") is True
    assert chat_api._skip_prefetch_web("然后呢") is True


def test_do_search_for_local_life_question():
    """用户踩的那个问题必须搜 —— 它含"周边""好吃"，但既无年份也无时效词。

    这条是本次修复的靶心：旧逻辑（模型自主）在这种问题上静默不搜。
    """
    assert chat_api._skip_prefetch_web("广东石油化工学院周边有什么好吃的店") is False
    assert chat_api._skip_prefetch_web("茂名马拉松怎么报名") is False


def test_year_never_skipped():
    """带年份的一律搜：赛事/政策/价格这类信息一年就能变。"""
    assert chat_api._skip_prefetch_web("2026 年有什么好看的电影") is False
    # 即使同时命中"续写"这类跳过词，年份优先
    assert chat_api._skip_prefetch_web("续写一篇 2026 年的新闻评论") is False


# ---------------- 2. 编号与入袋 ----------------


def test_prefetch_numbers_and_fills_bag(monkeypatch):
    """预检索要把结果编号写进袋里，否则收尾时来源卡是空的。"""
    _patch_search(monkeypatch, _payload("https://a.cn/1", "https://b.cn/2"))
    bag: list[dict] = []
    text, hits = tools.prefetch_web_sources("茂名 好吃的", bag, 6)

    assert hits == 2
    assert "[W1]" in text and "[W2]" in text
    assert [s["id"] for s in bag] == [1, 2]
    assert bag[0]["url"] == "https://a.cn/1"
    assert bag[0]["domain"] == "a.cn"


def test_prefetch_drops_bad_scheme_before_numbering(monkeypatch):
    """坏链必须在编号之前剔掉：否则模型看到的 [2] 与袋里 id=2 指的不是同一条。"""
    _patch_search(
        monkeypatch,
        {
            "organic": [
                {"title": "坏", "snippet": "", "link": "javascript:alert(1)"},
                {"title": "好", "snippet": "", "link": "https://real.cn/x"},
            ]
        },
    )
    bag: list[dict] = []
    text, hits = tools.prefetch_web_sources("测试", bag, 6)

    assert hits == 1
    assert "javascript" not in text
    assert [s["url"] for s in bag] == ["https://real.cn/x"]
    assert "**[W1]**" in text


def test_prefetch_then_tool_share_numbering(monkeypatch):
    """预检索 → 模型再补搜一次时，编号必须接着往下排。

    如果两条路径各用一套编号，正文会同时出现两组 [W1]，前端按 id 找网址就指错。
    """
    _patch_search(monkeypatch, _payload("https://a.cn/1"))
    bag: list[dict] = []
    tools.prefetch_web_sources("第一问", bag, 6)
    assert [s["id"] for s in bag] == [1]

    # 换个问法搜到的是**另一个**网址，编号必须接着排（同网址会复用 W1，见下一条）
    _patch_search(monkeypatch, _payload("https://c.cn/3"))
    out = tools.web_search.func(
        query="换个问法",
        max_results=5,
        config={"configurable": {"web_sources": bag, "download_allowlist": []}},
    )
    assert "**[W2]**" in out
    assert [s["id"] for s in bag] == [1, 2]


def test_prefetch_reuses_number_for_same_url(monkeypatch):
    """模型补搜回到同一个网址时复用原编号，来源卡不出现两条一样的。"""
    _patch_search(monkeypatch, _payload("https://a.cn/1"))
    bag: list[dict] = []
    tools.prefetch_web_sources("第一问", bag, 6)
    tools.prefetch_web_sources("第二问", bag, 6)
    assert len(bag) == 1
    assert [s["id"] for s in bag] == [1]


# ---------------- 3. 失败降级 ----------------


def test_prefetch_without_key_degrades(monkeypatch):
    monkeypatch.setattr(config, "SERPER_API_KEY", "")
    bag: list[dict] = []
    text, hits = tools.prefetch_web_sources("随便问问", bag, 6)
    assert hits == 0
    assert bag == []
    assert "SERPER_API_KEY" in text


def test_prefetch_empty_result(monkeypatch):
    _patch_search(monkeypatch, {"organic": []})
    bag: list[dict] = []
    text, hits = tools.prefetch_web_sources("冷门问题", bag, 6)
    assert hits == 0
    assert "未找到" in text
    assert bag == []


def test_prefetch_exception_does_not_raise(monkeypatch):
    """搜索接口炸了也只能降级：这一轮提问不能因此失败。"""
    monkeypatch.setattr(config, "SERPER_API_KEY", "test-key")

    def boom(query, count=10):
        raise RuntimeError("网络炸了")

    monkeypatch.setattr("core.serper.search_serper", boom)
    bag: list[dict] = []
    text, hits = tools.prefetch_web_sources("随便问问", bag, 6)
    assert hits == 0
    assert "不可用" in text
