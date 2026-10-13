# -*- coding: utf-8 -*-
"""第二档回归：缓存并发踩踏（single-flight）与 Serper 统一入口。不联网。

覆盖（2026-10-13）：
  1. _get_bm25：缓存失效后的并发构建只跑一次，其余线程复用结果；超大库负缓存生效；
  2. _embed_query：同题并发只发一次 embedding 请求；
  3. core.serper：TTL 缓存命中、并发 single-flight、失败重试、无 key 直接 None；
  4. boost_app_query 迁移后行为不变。

可从任意位置运行：python tests/test_tier2_singleflight.py
"""

import os
import sys
import threading
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import serper  # noqa: E402
from kb import rag_core  # noqa: E402

FAILED: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  [ok] " if cond else "  [FAIL] ") + msg)
    if not cond:
        FAILED.append(msg)


def _run_concurrent(fn, n=4):
    """n 个线程齐发调用 fn，返回 [结果...]（顺序无关）。"""
    barrier = threading.Barrier(n)
    results: list = []

    def worker():
        barrier.wait()
        results.append(fn())

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


# ==================== 1. BM25 single-flight + 负缓存 ====================

class _FakeCollection:
    def __init__(self, n_docs: int, delay: float = 0.15):
        self.calls = 0
        self._n = n_docs
        self._delay = delay

    def get(self, include=None):
        self.calls += 1
        time.sleep(self._delay)  # 模拟整库拉取的耗时，给并发踩踏留窗口
        docs = [f"深度学习 与 向量 检索 文档 {i}" for i in range(self._n)]
        metas = [{"source": "a.pdf", "page": i + 1} for i in range(self._n)]
        ids = [str(i) for i in range(self._n)]
        return {"documents": docs, "metadatas": metas, "ids": ids}


def test_1_bm25_singleflight() -> None:
    print("\n[1] _get_bm25：并发构建只跑一次 + 超大库负缓存")
    name = "kb_sf_test"
    col = _FakeCollection(3)
    vs = SimpleNamespace(_collection=col)

    results = _run_concurrent(lambda: rag_core._get_bm25(vs, name))
    check(col.calls == 1, f"4 线程并发只构建一次（实际 {col.calls} 次）")
    check(all(r is results[0] for r in results), "所有线程拿到同一个索引对象")
    check(not rag_core._BM25_INFLIGHT, "in-flight 集合已清空")

    # 超大库：第一次全库拉取后发现超限 → 负缓存，TTL 内不再重复拉取
    name2 = "kb_sf_big"
    big = _FakeCollection(10, delay=0.01)
    vs2 = SimpleNamespace(_collection=big)
    orig_max = rag_core.config.HYBRID_MAX_DOCS
    rag_core.config.HYBRID_MAX_DOCS = 5
    try:
        r1 = rag_core._get_bm25(vs2, name2)
        r2 = rag_core._get_bm25(vs2, name2)
    finally:
        rag_core.config.HYBRID_MAX_DOCS = orig_max
    check(r1 is None and r2 is None, "超大库两次调用都返回 None（退回纯向量）")
    check(big.calls == 1, f"负缓存生效：第二次没有重复全库拉取（实际 {big.calls} 次）")

    # 失效后负缓存也被清掉，会重新拉取
    rag_core._cache_invalidate(name2)
    rag_core._get_bm25(vs2, name2)
    check(big.calls == 2, "_cache_invalidate 后负缓存同步失效")

    rag_core._BM25_CACHE.pop(name, None)
    rag_core._BM25_CACHE.pop(name2, None)
    rag_core._BM25_NEG.pop(name2, None)


# ==================== 2. _embed_query single-flight ====================

class _FakeEmbeddings:
    def __init__(self):
        self.calls: list[str] = []

    def embed_query(self, q: str):
        self.calls.append(q)
        time.sleep(0.15)  # 模拟 embedding 网络请求耗时
        return [float(len(q)), 1.0, 0.5]


def test_2_embed_query_singleflight() -> None:
    print("\n[2] _embed_query：同题并发只发一次 embedding 请求")
    fake = _FakeEmbeddings()
    orig = rag_core.get_embeddings
    rag_core.get_embeddings = lambda: fake
    rag_core._EMBED_CACHE.clear()
    try:
        results = _run_concurrent(lambda: rag_core._embed_query("什么是向量检索"))
        check(fake.calls == ["什么是向量检索"],
              f"4 线程同题只请求一次（实际 {len(fake.calls)} 次）")
        check(all(r == results[0] for r in results), "所有线程拿到同一向量")

        # 不同问题互不影响
        rag_core._embed_query("另一个问题")
        check(len(fake.calls) == 2, "不同问题各自请求")
        check(not rag_core._EMBED_INFLIGHT, "in-flight 集合已清空")
    finally:
        rag_core.get_embeddings = orig
        rag_core._EMBED_CACHE.clear()


# ==================== 3. Serper 统一入口 ====================

class _FakeResp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class _FakeClient:
    def __init__(self, data=None, fail=False):
        self.calls = 0
        self._data = data or {"organic": [{"title": "t", "link": "u", "snippet": "s"}]}
        self._fail = fail

    def post(self, url, headers=None, json=None):
        self.calls += 1
        time.sleep(0.15)  # 模拟网络耗时
        if self._fail:
            raise RuntimeError("boom")
        return _FakeResp(self._data)


def _reset_serper(client) -> None:
    serper._client = client
    with serper._cond:
        serper._cache.clear()
        serper._inflight.clear()


def test_3_serper_cache_and_singleflight() -> None:
    print("\n[3] core.serper：TTL 缓存 + 并发 single-flight + 失败语义")
    orig_key = serper.config.SERPER_API_KEY
    serper.config.SERPER_API_KEY = "test-key"
    try:
        fake = _FakeClient()
        _reset_serper(fake)
        d1 = serper.search_serper("什么是向量检索", 3)  # 含「什么」不触发 boost
        d2 = serper.search_serper("什么是向量检索", 3)
        check(d1 is not None and d2 is d1, "同词第二次命中缓存（同一对象）")
        check(fake.calls == 1, f"两次调用只发一次真实请求（实际 {fake.calls} 次）")

        _reset_serper(fake)
        results = _run_concurrent(lambda: serper.search_serper("什么是向量检索", 3))
        check(fake.calls == 1, f"4 线程并发同词只发一次请求（实际 {fake.calls} 次）")
        check(all(r is results[0] for r in results), "所有线程拿到同一份结果")
        check(not serper._inflight, "in-flight 集合已清空")

        # 不同 count 是不同缓存键
        fake2 = _FakeClient()
        _reset_serper(fake2)
        serper.search_serper("什么是向量检索", 3)
        serper.search_serper("什么是向量检索", 5)
        check(fake2.calls == 2, "不同 count 不复用缓存")

        # 失败：返回 None 且不写缓存，下次重试
        bad = _FakeClient(fail=True)
        _reset_serper(bad)
        check(serper.search_serper("什么是向量检索", 3) is None, "请求失败返回 None")
        check(serper.search_serper("什么是向量检索", 3) is None, "失败后再次尝试仍 None")
        check(bad.calls == 2, "失败不写缓存，下次会重试")

        # 无 key：不发请求直接 None
        serper.config.SERPER_API_KEY = ""
        _reset_serper(fake2)
        check(serper.search_serper("什么是向量检索", 3) is None, "无 key 返回 None")
        check(fake2.calls == 0, "无 key 不发请求")
    finally:
        serper.config.SERPER_API_KEY = orig_key
        _reset_serper(None)


def test_4_boost_query_moved() -> None:
    print("\n[4] boost_app_query 迁移后行为不变")
    b = serper.boost_app_query
    check(b("微信") == "微信 官网", "裸 App 名补「官网」")
    check(b("什么是深度学习") == "什么是深度学习", "泛问词不改写")
    check(b("深度学习") == "深度学习", "学科裸词（黑名单）不误判为 App")
    check(b("抖音下载") == "抖音下载 官网", "含下载意图补「官网」")
    check(b("") == "", "空串原样返回")


def main() -> int:
    print("=" * 60)
    print("第二档回归：single-flight 与 Serper 统一入口")
    print("=" * 60)
    test_1_bm25_singleflight()
    test_2_embed_query_singleflight()
    test_3_serper_cache_and_singleflight()
    test_4_boost_query_moved()
    print("\n" + "=" * 60)
    if FAILED:
        print(f"失败 {len(FAILED)} 项：")
        for m in FAILED:
            print("  - " + m)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
