"""A4 回归：换 embedding 模型必须被拦住（2026-10-20 审计修复）。

`config.EMBEDDING_MODEL` 是环境变量——改它不改代码、不过评审、不发版，
但库里所有既有向量是旧模型算的。新查询向量落在另一个语义空间：
维度通常相同，余弦相似度照样算得出来，只是**数值不再可比**。
于是排序接近随机、检索退化成噪声，而日志里什么都没有。

本测试锁死六件事：
  1) 指纹只比"模型 + 主机"，不比 URL 路径（换路径前缀不该误报）；
  2) 维度只在两边都有值时比（老库可能没记维度）；
  3) 不一致时抛可读错误，且错误里同时出现两个模型名；
  4) 无指纹的老库**不报错**（否则升级瞬间现网库全不可用）；
  5) 校验结果缓存，热路径不重复读 metadata；
  6) 开关关掉就完全不校验。
"""

from __future__ import annotations

import pytest

from core import config, embed_guard, metrics
from core.embed_guard import EmbeddingMismatch


def test_same_space_matrix():
    f = embed_guard._same_space
    assert f({"model": "m", "base": "h"}, {"model": "m", "base": "h"}) is True
    assert f({"model": "m", "base": "h"}, {"model": "m2", "base": "h"}) is False
    # 同名模型不同供应商 → 必须报。这是最容易被误判成"没换"的场景
    assert f({"model": "m", "base": "a"}, {"model": "m", "base": "b"}) is False
    # 只有一边有维度 → 不算不一致
    assert f({"model": "m", "base": "h"}, {"model": "m", "base": "h", "dim": 1024}) is True
    # 两边都有维度且不同 → 不一致
    assert f({"model": "m", "base": "h", "dim": 1024}, {"model": "m", "base": "h", "dim": 768}) is False


def test_fingerprint_uses_host_only(monkeypatch):
    monkeypatch.setattr(config, "EMBEDDING_MODEL", "text-embedding-v3")
    monkeypatch.setattr(
        config, "EMBEDDING_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    fp = embed_guard.fingerprint()
    assert fp["model"] == "text-embedding-v3"
    # 只留主机名：换路径前缀（/compatible-mode/v1 → /v1）不该导致误报
    assert fp["base"] == "dashscope.aliyuncs.com"


def test_mismatch_message_is_actionable():
    err = EmbeddingMismatch(
        "kb_abc",
        {"model": "text-embedding-v2", "base": "h"},
        {"model": "text-embedding-v3", "base": "h"},
    )
    msg = str(err)
    assert "text-embedding-v2" in msg and "text-embedding-v3" in msg
    assert "kb_abc" in msg
    # 必须给出下一步；只报错不给出路的消息在运维眼里等于没说
    assert "重建" in msg


def _fresh(monkeypatch, stored, enabled=True):
    monkeypatch.setattr(config, "EMBED_GUARD_ENABLED", enabled)
    monkeypatch.setattr(config, "EMBEDDING_MODEL", "new-model")
    monkeypatch.setattr(config, "EMBEDDING_BASE_URL", "https://h")
    embed_guard.invalidate("kb_test")
    monkeypatch.setattr(embed_guard, "_read", lambda coll: stored)
    return stored


def test_mismatch_raises_and_counts(monkeypatch):
    metrics.reset()
    _fresh(monkeypatch, {"model": "old-model", "base": "h"})
    with pytest.raises(EmbeddingMismatch):
        embed_guard.check("kb_test")
    assert metrics.snapshot()["counters"]["embed_guard_mismatch"]["kb=kb_test"] >= 1


def test_missing_fingerprint_is_tolerated(monkeypatch):
    """老库没指纹 → 放行并补写。绝不因为"查不到"就把现网库全拒掉。"""
    _fresh(monkeypatch, None)

    stamped = {}
    monkeypatch.setattr(embed_guard, "stamp", lambda coll, dim=None: stamped.update({"coll": coll}))

    embed_guard.check("kb_test")
    assert stamped.get("coll") == "kb_test"


def test_check_is_cached(monkeypatch):
    """检索是热路径：校验结果必须缓存，不能每次请求都读 collection metadata。"""
    calls = {"n": 0}

    def counting_read(coll):
        calls["n"] += 1
        return {"model": "new-model", "base": "h"}

    monkeypatch.setattr(config, "EMBED_GUARD_ENABLED", True)
    monkeypatch.setattr(config, "EMBEDDING_MODEL", "new-model")
    monkeypatch.setattr(config, "EMBEDDING_BASE_URL", "https://h")
    embed_guard.invalidate("kb_test")
    monkeypatch.setattr(embed_guard, "_read", counting_read)

    for _ in range(5):
        embed_guard.check("kb_test")
    assert calls["n"] == 1

    # 重建索引 / 换模型后必须能重新校验
    embed_guard.invalidate("kb_test")
    embed_guard.check("kb_test")
    assert calls["n"] == 2


def test_switch_off_disables_check(monkeypatch):
    _fresh(monkeypatch, {"model": "old-model", "base": "h"}, enabled=False)
    embed_guard.check("kb_test")  # 不抛


def test_stamp_noop_when_already_correct(monkeypatch):
    """指纹已经对的时候不该反复 modify collection（那是写操作）。"""
    _fresh(monkeypatch, {"model": "new-model", "base": "h"})

    class _Boom:
        def get_collection(self, _name):
            raise AssertionError("不该走到 Chroma：指纹已经一致")

    import kb.rag_core as rag

    monkeypatch.setattr(rag, "chroma_client", lambda: _Boom())
    embed_guard.stamp("kb_test", 1024)  # 提前返回，不触碰 Chroma
