# -*- coding: utf-8 -*-
"""B4：把换词接进聊天检索链路（api/chat._retrieval_query）的回归。

这一步的风险和 B3 完全不同
--------------------------
B3 是个只读的预览接口，调坏了没人受影响。B4 改的是**每一句 RAG 提问的检索词**，
调坏了是全站检索质量下降，而且现象很隐蔽（回答变差，但不报错）。
所以这一份测试守的不是"能换词"，是四条**不许破的底线**：

1. **展示不改**：函数只返回检索用文本，用户原话一个字都不能被改写。
2. **开关能当场关掉**：ENHANCE_CHAT_ENABLED=0 必须退回"只有改写"的旧行为。
3. **词表被改坏不许把聊天搞挂**：组员删一列、改个表头，load_vocab 会抛 ValueError，
   这时必须原样放行让检索照跑（预览接口红着脸报错是对的，聊天挂掉是灾难）。
4. **两处调用点都要接上**：图片理解模式和 RAG 模式各一处，只改一处会变成
   "同一句话在两种模式下命中的片段不一样"，这类 bug 极难被发现。

不联网、不调 LLM、不碰 Chroma：rewrite_query 打桩，词表用临时文件。
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-dummy-for-import")

import pytest  # noqa: E402

import api.chat as chat  # noqa: E402
import core.config  # noqa: E402
from service import enhance as enhance_svc  # noqa: E402

VOCAB_TEXT = (
    "canonical,aliases,negative_aliases\n"
    "高等数学,高数,\n"
    "线性代数,线代,\n"
    "考研二战,二战,二战考研\n"
)


@pytest.fixture
def vocab(monkeypatch, tmp_path):
    """临时词表 + 清缓存。返回词表路径。"""
    p = tmp_path / "vocab.csv"
    p.write_text(VOCAB_TEXT, encoding="utf-8-sig")
    monkeypatch.setattr(core.config, "ENHANCE_VOCAB_PATH", str(p))
    # 裁决表指向不存在的文件：这条链路不该依赖它（缺失会优雅退化）
    monkeypatch.setattr(core.config, "ENHANCE_ADJUDICATION_PATH", str(tmp_path / "no.csv"))
    enhance_svc.reset_cache()
    return str(p)


@pytest.fixture
def chat_on(monkeypatch):
    monkeypatch.setattr(core.config, "ENHANCE_CHAT_ENABLED", True)


@pytest.fixture
def chat_off(monkeypatch):
    monkeypatch.setattr(core.config, "ENHANCE_CHAT_ENABLED", False)


def _no_rewrite(monkeypatch):
    """把指代改写打桩成"原样返回"，隔离出换词这一段。"""
    monkeypatch.setattr(chat, "rewrite_query", lambda q, h: q)


# ==================== 1. 底线：展示不改 ====================

def test_returns_retrieval_text_only_and_keeps_original(monkeypatch, vocab, chat_on):
    """★ 返回的检索词被换了，但传进去的原话必须原封不动。"""
    _no_rewrite(monkeypatch)
    original = "高数怎么复习"
    query, changes, rewritten = chat._retrieval_query(original, [])

    assert query == "高等数学怎么复习", "检索词没换上，等于白接"
    assert original == "高数怎么复习", "原话被改了——展示层会跟着变，这是红线"
    assert changes == [{"raw": "高数", "canonical": "高等数学"}]
    assert rewritten is False


def test_changes_carry_no_offsets(monkeypatch, vocab, chat_on):
    """★ 替换列表故意不带 start/end。

    这里的文本可能已经被 LLM 补全过指代，偏移在**用户原话**上没有意义；
    带了偏移前端一定会拿去高亮，然后错位。要高亮走 /api/query/enhance。
    """
    _no_rewrite(monkeypatch)
    _, changes, _ = chat._retrieval_query("高数和线代", [])
    assert changes, "这一步本来就该有替换"
    for c in changes:
        assert set(c) == {"raw", "canonical"}, f"多带了字段：{c}"


# ==================== 2. 开关 ====================

def test_switch_off_restores_old_behaviour(monkeypatch, vocab, chat_off):
    """ENHANCE_CHAT_ENABLED=0 → 只改写不换词，且不发提示。"""
    _no_rewrite(monkeypatch)
    query, changes, rewritten = chat._retrieval_query("高数怎么复习", [])
    assert query == "高数怎么复习"
    assert changes == []
    assert rewritten is False


# ==================== 3. 顺序：先改写，再换词 ====================

def test_rewrite_runs_before_enhance(monkeypatch, vocab, chat_on):
    """★ 顺序反了就会漏词：rewrite 补出来的新文本里可能才第一次出现口语词。

    这里打桩把「那它呢」补全成「高数那章怎么复习」——如果 enhance 在 rewrite
    之前跑，它看到的还是「那它呢」，一个词都换不出来。
    """
    monkeypatch.setattr(chat, "rewrite_query", lambda q, h: "高数那章怎么复习")
    query, changes, rewritten = chat._retrieval_query("那它呢", [{"role": "user", "content": "高数"}])

    assert rewritten is True
    assert query == "高等数学那章怎么复习", "rewrite 补出来的口语词没被换 —— 顺序反了"
    assert changes == [{"raw": "高数", "canonical": "高等数学"}]


def test_rewrite_alone_still_reports(monkeypatch, vocab, chat_on):
    """只有改写、没有可换的词时，也要如实报 rewritten（前端要发提示）。"""
    monkeypatch.setattr(chat, "rewrite_query", lambda q, h: "RRF 融合算法有什么缺点")
    query, changes, rewritten = chat._retrieval_query("那它呢", [])
    assert rewritten is True
    assert changes == []
    assert query == "RRF 融合算法有什么缺点"


# ==================== 4. 降级：词表坏了不许把聊天搞挂 ====================

def test_broken_vocab_does_not_break_retrieval(monkeypatch, tmp_path, chat_on):
    """★★ 组员把词表改坏（删了必需列）时，必须原样放行。

    这是这一步最重要的测试：预览接口挂掉是好事（谁改坏的谁立刻看见），
    但聊天挂掉是灾难。换词是增益，不是检索的前置条件。
    """
    bad = tmp_path / "bad.csv"
    bad.write_text("idx,word,alias\n1,高等数学,高数\n", encoding="utf-8-sig")  # 列名全错
    monkeypatch.setattr(core.config, "ENHANCE_VOCAB_PATH", str(bad))
    enhance_svc.reset_cache()
    _no_rewrite(monkeypatch)

    query, changes, rewritten = chat._retrieval_query("高数怎么复习", [])
    assert query == "高数怎么复习", "词表坏了还硬换，说明异常没兜住"
    assert changes == []
    assert rewritten is False


def test_any_exception_in_enhance_falls_back(monkeypatch):
    """连非预期异常（不是 ValueError）也要兜住 —— 兜底写在最外层。"""
    monkeypatch.setattr(core.config, "ENHANCE_CHAT_ENABLED", True)
    monkeypatch.setattr(chat, "rewrite_query", lambda q, h: q)

    def boom():
        raise RuntimeError("磁盘满了 / 编码炸了 / 谁知道")

    monkeypatch.setattr(enhance_svc, "get_vocab", boom)
    text, changes = enhance_svc.enhance_for_retrieval("高数")
    assert (text, changes) == ("高数", [])


# ==================== 5. 提示文案 ====================

def test_note_mentions_both_actions_in_one_line(monkeypatch):
    """★ 状态条是覆盖式的：连发两条用户只看到最后一条。所以合成一句。"""
    note = chat._enhance_note(
        [{"raw": "高数", "canonical": "高等数学"}], True, "高等数学那章怎么复习"
    )
    assert "改写" in note and "高数→高等数学" in note
    assert note.count("检索词") == 1


def test_note_only_enhance(monkeypatch):
    note = chat._enhance_note([{"raw": "线代", "canonical": "线性代数"}], False, "线性代数")
    assert "改写" not in note and "线代→线性代数" in note


def test_note_truncates_many_changes():
    """换了很多词时只报前两个 + 总数，不能把状态条刷成一整段。"""
    changes = [
        {"raw": f"词{i}", "canonical": f"标准词{i}"} for i in range(5)
    ]
    note = chat._enhance_note(changes, False, "x")
    assert "等 5 处" in note
    assert "词4" not in note, "只该报前两个"


# ==================== 6. 两处调用点都接上了 ====================

def test_both_call_sites_are_wired():
    """★ 图片理解模式与 RAG 模式各有一处检索。

    只改一处会造成"同一句话在两种模式下命中的片段不一样"——这类 bug 不会报错，
    只会让人觉得"有时候检索不准"，几乎不可能靠人工发现。
    """
    import inspect

    src = inspect.getsource(chat)
    n = sum(1 for line in src.splitlines()
            if "_retrieval_query" in line and not line.strip().startswith("def "))
    assert n >= 2, f"只接了 {n} 处调用，图片模式/RAG 模式漏了一处"
    assert "rewrite_query, req.question, history" not in src, \
        "还有地方在直接调 rewrite_query，绕过了换词"
