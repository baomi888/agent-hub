"""知识库浏览量。

统计口径：**独立访客数**（同一个人反复打开同一个库只算一次），不是 PV。
理由：
  - 这是学校里的资料库，不是广告位。同一个人刷 50 次不该让它排到第一，
    "最多人用的库"这句需求里，"人"比"次"更接近用户想看的答案；
  - PV 计数每打开一次就写一行，一天几百行，而访客表一个人一行，
    永远不会因为刷新而膨胀；
  - 想改 PV 也不难：把主键去掉、`INSERT OR IGNORE` 换成 `INSERT` 就行，
    届时这张表可以直接沿用（加一个自增 id 列即可）。

放独立模块而不是塞进 kb_board：留言是用户产出的内容（要能删、要留痕），
浏览量是一行计数，两者的生命周期和删除语义完全不同 —— 删留言的时候
不该顺手把"多少人看过"也抹掉。
"""

from __future__ import annotations

import time

from core import session as store

_TABLES_SQL = """
    CREATE TABLE IF NOT EXISTS kb_views (
        kb_id TEXT NOT NULL,
        uid   TEXT NOT NULL,
        at    INTEGER NOT NULL,
        PRIMARY KEY (kb_id, uid)
    );
"""

# 建过表的库路径（与 kb_board._READY 同一个理由：测试会换库）
_READY: set[str] = set()


def _ensure() -> None:
    path = store._db_path()  # noqa: SLF001 —— 同一个测试缝
    if path in _READY:
        return
    with store._get_conn() as conn:  # noqa: SLF001
        conn.executescript(_TABLES_SQL)
    _READY.add(path)


def touch(kb_id: str, uid: str) -> None:
    """记一次访问。同一个人重复访问只更新主键，不新增行。

    为什么用 `INSERT OR IGNORE` 而不是先 SELECT 再 INSERT：
    并发打开同一个库时，先查后写在两条请求之间会有窗口，两边都查不到、
    两边都插入 —— 没有唯一约束就会多算一次。唯一主键让数据库来兜这件事。
    """
    if not kb_id or not uid:
        return
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        conn.execute(
            "INSERT OR IGNORE INTO kb_views (kb_id, uid, at) VALUES (?, ?, ?)",
            (kb_id, uid, int(time.time())),
        )


def count(kb_id: str) -> int:
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        row = conn.execute("SELECT COUNT(*) FROM kb_views WHERE kb_id = ?", (kb_id,)).fetchone()
    return int(row[0]) if row else 0


def counts() -> dict[str, int]:
    """一次查出所有库的访客数，给广场卡片用（理由同 kb_board.counts_by_kb）。"""
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        rows = conn.execute("SELECT kb_id, COUNT(*) FROM kb_views GROUP BY kb_id").fetchall()
    return {r[0]: int(r[1]) for r in rows}


def purge_kb(kb_id: str) -> int:
    """库删除时连访客记录一起清。库都没了，留着这些行没人能看见。"""
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        cur = conn.execute("DELETE FROM kb_views WHERE kb_id = ?", (kb_id,))
        return int(cur.rowcount or 0)
