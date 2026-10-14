# -*- coding: utf-8 -*-
"""api/feedback.py 写接口的限流护栏（离线，不落库）。

要验的是那条最容易漏的点：**限流必须在归属校验之前**。
若顺序写反（先查归属再限流），脚本就能靠批量刷 404 来无限试探会话 id 是否存在，
反而把「不泄露会话是否存在」这条隐私保证给漏了。
"""
import types

import pytest

import api.feedback as fb
from core import quota


class _Store:
    """替换 core.session：记录被调用的顺序，便于断言限流早于归属校验。"""

    def __init__(self, own: bool = True):
        self.own = own
        self.calls: list[str] = []

    def get_conversation(self, uid, sid):
        self.calls.append("get_conversation")
        return {"sid": sid} if self.own else None

    def get_message(self, uid, sid, mid):
        self.calls.append("get_message")
        return {"id": mid}

    def add_feedback(self, *a):
        self.calls.append("add_feedback")
        return True

    def list_feedbacks(self, *a):
        self.calls.append("list_feedbacks")
        return []


def _req(sid="s1", rating="up", mid=None):
    return fb.FeedbackRequest(sid=sid, message_id=mid, rating=rating)


def _patch(monkeypatch, store, limit):
    monkeypatch.setattr(fb, "store", store)
    monkeypatch.setattr(quota, "hit", lambda uid, n=None: _Hit(limit))
    monkeypatch.setattr(fb, "_FEEDBACK_RATE", limit)


class _Hit:
    def __init__(self, limit):
        self.limit = limit
        self.n = 0

    def __call__(self, uid, n=None):
        self.n += 1
        return self.n


def test_quota_called_before_ownership_check(monkeypatch):
    """★ 核心断言：**限流必须早于归属校验**。

    ⚠️ 第一版这条测试只断言了「get_conversation 被调用过」，
    于是把 quota.hit 挪到归属校验之后它照样全绿 —— 断言写空了，
    反证（git 式改坏代码再跑）当场揭穿。改法：用共享的 order 列表记录
    真实调用顺序，再断言下标先后。
    """
    order: list[str] = []
    store = _Store(own=True)
    monkeypatch.setattr(fb, "store", store)
    monkeypatch.setattr(quota, "hit", lambda uid, n=None: order.append("quota") or 1)

    orig_get = store.get_conversation

    def _tracked(uid, sid):
        order.append("get_conversation")
        return orig_get(uid, sid)

    monkeypatch.setattr(store, "get_conversation", _tracked)

    fb.add_feedback(_req(), uid="u1")

    assert "quota" in order, "限流根本没被调用"
    assert "get_conversation" in order, "归属校验根本没跑"
    assert order.index("quota") < order.index("get_conversation"), (
        f"限流必须早于归属校验，实际顺序={order}"
    )


def test_rate_limited_blocks_write(monkeypatch):
    """超限时抛 RateLimited，且**不应该**走到落库。"""
    store = _Store(own=True)
    _patch(monkeypatch, store, limit=10)

    calls = {"n": 0}

    def _boom(uid, n=None):
        calls["n"] += 1
        raise quota.RateLimited(30.0)

    monkeypatch.setattr(quota, "hit", _boom)
    with pytest.raises(quota.RateLimited):
        fb.add_feedback(_req(), uid="u1")
    # RateLimited 由 main.py 注册成全局 429；这里断言「没落库」
    assert "add_feedback" not in store.calls


def test_not_owned_still_404(monkeypatch):
    """会话不属于自己 ⇒ 404，且不泄露别的信息。"""
    store = _Store(own=False)
    _patch(monkeypatch, store, limit=10)
    with pytest.raises(Exception) as ei:
        fb.add_feedback(_req(), uid="u1")
    assert getattr(ei.value, "status_code", None) == 404


def test_bad_rating_400(monkeypatch):
    """rating 非法 ⇒ 400（归属校验通过之后才轮到它）。"""
    store = _Store(own=True)
    _patch(monkeypatch, store, limit=10)
    with pytest.raises(Exception) as ei:
        fb.add_feedback(_req(rating="maybe"), uid="u1")
    assert getattr(ei.value, "status_code", None) == 400


def test_quota_limit_is_tighter_than_global():
    """反馈的每分钟上限应严于全局默认值（脚本刷写库要挡住，正常用户不受影响）。"""
    from core import config

    assert fb._FEEDBACK_RATE <= config.RATE_PER_MINUTE
