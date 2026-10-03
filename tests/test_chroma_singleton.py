# -*- coding: utf-8 -*-
"""Chroma 客户端单例自测（不联网、不污染真实数据）。

背景：exists / list_all / get_status / peek 这几个"只读一下"的接口，
每次调用都 new 一个 PersistentClient（实测首次 0.588s、之后 0.098s/次），
而它们又全跑在 async 端点里 —— 每条请求都要卡住事件循环几十到几百毫秒。

本测试验证三件事：
  1. chroma_client() 是进程级单例；
  2. peek / delete / list_all / warmup 都走单例，不再各自 new；
  3. 功能没退化：list_all 结构完整、不存在的库 exists() 返回 False。

可从任意位置运行：python tests/test_chroma_singleton.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chromadb  # noqa: E402

from core import config  # noqa: E402
from kb import pipelines  # noqa: E402
from kb import rag_core  # noqa: E402

FAILED: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  [ok] " if cond else "  [FAIL] ") + msg)
    if not cond:
        FAILED.append(msg)


def test_1_singleton() -> None:
    """多次调用拿到的是同一个 client 实例。"""
    print("\n[1] chroma_client() 返回同一实例")
    a = rag_core.chroma_client()
    b = rag_core.chroma_client()
    check(a is b, "两次调用是同一个对象")
    check(rag_core._CHROMA_CLIENT is a, "模块级缓存已写入")


def test_2_speed() -> None:
    """量单例的收益：和"每次新建"对比同一段调用。"""
    print("\n[2] 单例 vs 每次新建（各 20 次）")

    # 模拟旧行为：每次都 new
    t = time.perf_counter()
    for _ in range(20):
        chromadb.PersistentClient(path=config.PERSIST_DIR)
    old = time.perf_counter() - t

    t = time.perf_counter()
    for _ in range(20):
        rag_core.chroma_client()
    new = time.perf_counter() - t

    print(f"       每次新建：{old * 1000:.1f}ms / 20 次（{old / 20 * 1000:.1f}ms 每次）")
    print(f"       单例：    {new * 1000:.3f}ms / 20 次（{new / 20 * 1000:.3f}ms 每次）")
    check(new * 20 < old, "单例明显更快")
    check(new < 0.005, "20 次调用几乎不耗时（<5ms）")


def test_3_read_paths_use_singleton() -> None:
    """peek / delete / list_all / warmup 都走单例，不再自己 new PersistentClient。"""
    print("\n[3] 读路径全部复用单例")

    # 只有单例那一行允许出现 PersistentClient，其余读路径必须复用
    hits = 0
    for path in ("kb/rag_core.py", "kb/pipelines.py"):
        text = open(path, encoding="utf-8").read()
        hits += text.count("chromadb.PersistentClient")
    check(hits == 1, f"全项目只剩 1 处 PersistentClient（单例里那处），实测 {hits} 处")

    # 功能验证：列表能正常返回，且不存在的库不炸
    try:
        rows = pipelines.list_all()
        check(isinstance(rows, list), "list_all() 返回列表")
        if rows:
            keys = set(rows[0].keys())
            check({"kb_id", "name", "chunks", "files"} <= keys, f"列表项字段完整：{sorted(keys)}")
        else:
            print("       （本机没有知识库，跳过字段检查）")
    except Exception as e:
        check(False, f"list_all() 抛异常：{e}")

    check(pipelines.exists("__no_such_kb__") is False, "不存在的库 exists() 返回 False 而非抛错")
    check(pipelines.get_status("__no_such_kb__") is None, "不存在的库 get_status() 返回 None")


def test_4_thread_safe_first_call() -> None:
    """并发首次调用也只建一个（双重检查锁）。"""
    print("\n[4] 并发首次调用只建一个实例")
    import threading

    rag_core._CHROMA_CLIENT = None
    got = []
    lock = threading.Lock()

    def worker():
        c = rag_core.chroma_client()
        with lock:
            got.append(id(c))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    check(len(set(got)) == 1, f"8 个线程拿到同一实例（实测 {len(set(got))} 个）")


def main() -> int:
    print("=" * 60)
    print("Chroma 客户端单例自测")
    print("=" * 60)
    test_1_singleton()
    test_2_speed()
    test_3_read_paths_use_singleton()
    test_4_thread_safe_first_call()
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
