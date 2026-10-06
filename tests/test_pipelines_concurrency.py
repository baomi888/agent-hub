# -*- coding: utf-8 -*-
"""并发安全自测：注册表锁 + 文件清单原子写。

纯本地、不联网、不碰真实数据（PROJECT_ROOT 指到临时目录，跑完还原）。
可从任意位置运行：python tests/test_pipelines_concurrency.py
"""

import json
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import config  # noqa: E402
from kb import pipelines  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="kb_conc_")
_OLD_ROOT = config.PROJECT_ROOT

# 把落盘位置挪到临时目录，别污染真实的 data/kb_files.json
config.PROJECT_ROOT = _TMP
os.makedirs(os.path.join(_TMP, "data"), exist_ok=True)

FAILED: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  [ok] " if cond else "  [FAIL] ") + msg)
    if not cond:
        FAILED.append(msg)


# 多用户改造后 pipelines 的每个函数都要 owner。本文件只测并发与原子写，
# 归属逻辑另见 test_owner_isolation.py，这里统一用一个假 owner 把门禁放行。
OWNER = "conc_owner"


def _own(kb: str) -> str:
    """把 kb_id 登记到测试账号名下，免得每个用例都先建个真库。"""
    with pipelines._REGISTRY_LOCK:
        pipelines._load_owners()
        pipelines._KB_OWNERS[kb] = OWNER
        pipelines._save_owners()
    return kb


def test_1_concurrent_records() -> None:
    """10 个线程同时往同一个库登记不同文件，一条都不能丢。

    旧实现全量覆盖写且不重读磁盘：每个线程手里的清单都是自己进来时的快照，
    最后写的人把前面 9 个人全冲掉。
    """
    print("\n[1] 并发登记文件记录")
    kb = _own("conc_kb")
    n_threads, per_thread = 10, 5

    def worker(tid: int) -> None:
        for j in range(per_thread):
            pipelines.add_file_records(OWNER, kb, [{
                "name": f"t{tid}_f{j}.txt",
                "chunks": 1,
                "added_at": 0,
                "origin": "upload",
            }])

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    names = pipelines.get_file_names(OWNER, kb)
    check(len(names) == n_threads * per_thread,
          f"{n_threads}x{per_thread} 条记录全在（实际 {len(names)}）")

    # 磁盘上也得是对的：不看内存缓存，直接读文件
    with open(pipelines._files_path(), encoding="utf-8") as f:
        on_disk = json.load(f)
    check(len(on_disk.get(kb, [])) == n_threads * per_thread,
          f"落盘内容同样完整（实际 {len(on_disk.get(kb, []))}）")


def test_2_never_read_partial() -> None:
    """写清单的整个过程里，文件要么是旧版完整内容、要么是新版完整内容。

    另一个线程持续读，一旦读到截断的 JSON（JSONDecodeError）就说明不是原子写。
    """
    print("\n[2] 写入过程中读不到半截内容")
    kb = _own("atomic_kb")
    stop = threading.Event()
    broken: list[str] = []

    def reader() -> None:
        """只关心"会不会读到半截内容"。

        Windows 上 os.replace 期间目标文件瞬时打不开（PermissionError），
        那是平台行为、重试即可，不算数据损坏；JSONDecodeError 才是真读到了截断内容。
        """
        p = pipelines._files_path()
        while not stop.is_set():
            if not os.path.exists(p):
                time.sleep(0.001)
                continue
            try:
                with open(p, encoding="utf-8") as f:
                    json.load(f)
            except (FileNotFoundError, PermissionError):
                time.sleep(0.001)  # 替换瞬间 / 被占用，重试
            except Exception as e:
                broken.append(f"{type(e).__name__}: {e}")
                return

    r = threading.Thread(target=reader, daemon=True)
    r.start()
    for i in range(40):
        pipelines.add_file_records(OWNER, kb, [{"name": f"a{i}.txt", "chunks": 1, "added_at": 0}])
    stop.set()
    r.join(timeout=2)
    check(not broken, f"40 次写入期间持续读取，未遇到半截文件（异常：{broken[:2]}）")

    leftovers = [f for f in os.listdir(os.path.join(_TMP, "data")) if f.endswith(".tmp")]
    check(not leftovers, f"没有残留 .tmp 文件（{leftovers}）")


def test_3_transient_lock_recovers() -> None:
    """有人短暂开着清单文件（杀毒扫描那种）时，重试要能把这次写入救回来。

    不重试的话这次登记就静默丢了：界面上文件在（内存里有），重启后消失。
    """
    print("\n[3] 瞬时占用后重试能落盘")
    kb = _own("transient_kb")
    p = pipelines._files_path()
    pipelines.add_file_records(OWNER, kb, [{"name": "first.txt", "chunks": 1, "added_at": 0}])

    blocker = open(p, "r", encoding="utf-8")  # 占住句柄
    done = threading.Event()

    def writer() -> None:
        pipelines.add_file_records(OWNER, kb, [{"name": "second.txt", "chunks": 1, "added_at": 0}])
        done.set()

    t = threading.Thread(target=writer, daemon=True)
    t.start()
    time.sleep(0.08)  # 让它先撞上一次 WinError 5
    blocker.close()
    t.join(timeout=5)

    names = pipelines.get_file_names(OWNER, kb)
    check(done.is_set(), "写线程未被卡死")
    check("second.txt" in names, f"被占用 80ms 后仍成功落盘（实际 {names}）")


def test_4_old_file_survives() -> None:
    """一直写不进去时，旧文件必须原样保留——不能变成截断的 JSON。

    这是原子写最关键的兜底：宁可丢一次登记，也不能让整个清单读不出来。
    """
    print("\n[4] 持续占用下旧文件完好")
    kb = _own("blocked_kb")
    p = pipelines._files_path()
    pipelines.add_file_records(OWNER, kb, [{"name": "keep.txt", "chunks": 1, "added_at": 0}])
    good = open(p, encoding="utf-8").read()

    blocker = open(p, "r", encoding="utf-8")
    try:
        pipelines.add_file_records(OWNER, kb, [{"name": "lost.txt", "chunks": 1, "added_at": 0}])
    finally:
        blocker.close()

    with open(p, encoding="utf-8") as f:
        after = f.read()
    check(after == good, "写失败后磁盘内容与原文件逐字节一致（没被截断）")
    try:
        json.loads(after)
        ok = True
    except Exception:
        ok = False
    check(ok, "仍是合法 JSON，下次启动能正常读回")
    check("keep.txt" in pipelines.get_files(OWNER, kb).__str__(), "旧记录完好")


def test_5_kb_lock() -> None:
    """同库共用一把锁、异库互不相干，且锁真的互斥。"""
    print("\n[5] 按库串行锁")
    l1, l2 = pipelines.kb_lock("kb_a"), pipelines.kb_lock("kb_a")
    l3 = pipelines.kb_lock("kb_b")
    check(l1 is l2, "同一 kb_id 拿到同一把锁（否则等于没锁）")
    check(l1 is not l3, "不同 kb_id 是不同锁（不该互相阻塞）")

    inside = threading.Event()
    entered = threading.Event()

    def holder() -> None:
        with pipelines.kb_lock("kb_c"):
            entered.set()
            time.sleep(0.3)
            inside.set()

    t = threading.Thread(target=holder)
    t.start()
    entered.wait(1)
    # 主线程去抢：互斥的话必须等 holder 出来
    got = pipelines.kb_lock("kb_c").acquire(timeout=0.05)
    check(not got, "持锁期间别的线程抢不到（真互斥）")
    if got:
        pipelines.kb_lock("kb_c").release()
    t.join(timeout=2)
    check(inside.is_set(), "持锁线程正常走完并释放")


def test_6_reentrant() -> None:
    """RLock：内部函数互相调用时不能把自己锁死。

    add_file_records 里会调 get_or_create，两者都要 _REGISTRY_LOCK，
    用普通 Lock 会在第二次 acquire 时直接死锁。
    """
    print("\n[6] 锁可重入")
    done = threading.Event()

    def worker() -> None:
        with pipelines._REGISTRY_LOCK:
            pipelines.add_file_records(OWNER, _own("re_kb"), [{"name": "x.txt", "chunks": 1, "added_at": 0}])
        done.set()

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout=3)
    check(done.is_set(), "持 _REGISTRY_LOCK 时再调 add_file_records 未死锁")


def main() -> int:
    print(f"临时目录：{_TMP}")
    try:
        test_1_concurrent_records()
        test_2_never_read_partial()
        test_3_transient_lock_recovers()
        test_4_old_file_survives()
        test_5_kb_lock()
        test_6_reentrant()
    finally:
        config.PROJECT_ROOT = _OLD_ROOT
        # 还原到真实清单，别让临时数据留在内存里
        pipelines._load_files()
        pipelines._load_owners()

    print("\n" + ("全部通过" if not FAILED else f"失败 {len(FAILED)} 项：{FAILED}"))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
