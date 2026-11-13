"""知识库收藏。

为什么单独一张表、和 `kb_views` 分开：
  - 浏览是**无意识**产生的（点进去就算），收藏是**有意识**的（我要留着它）；
  - 两者的读法也不同：浏览数用来排序"哪个库热"，收藏是"我的清单"，
    登录后要能一眼列出、并且带上来源与作者；
  - 混成一张表就得靠一个布尔列区分，之后每个查询都要记得带上它 —— 迟早漏。

口径：一个人对同一个库只有一条（主键去重）。重复点收藏是幂等的，
再点一次是取消（接口层用 PUT/DELETE 表达，不是 toggle —— toggle 在
网络重试时会翻来覆去，用户看到的状态和以为的不一样）。
"""

from __future__ import annotations

import time

from core import session as store

_TABLES_SQL = """
    CREATE TABLE IF NOT EXISTS kb_favorites (
        kb_id TEXT NOT NULL,
        uid   TEXT NOT NULL,
        at    INTEGER NOT NULL,
        PRIMARY KEY (kb_id, uid)
    );
"""

_READY: set[str] = set()


def _ensure() -> None:
    path = store._db_path()  # noqa: SLF001 —— 与 kb_board / kb_views 同一个测试缝
    if path in _READY:
        return
    with store._get_conn() as conn:  # noqa: SLF001
        conn.executescript(_TABLES_SQL)
    _READY.add(path)


def add(kb_id: str, uid: str) -> bool:
    """收藏。返回**这次是不是新加的**（已经收藏过 → False）。

    用 `INSERT OR IGNORE` + rowcount 判断：先查后写会有并发窗口
    （两次点击都查不到、都插入），而唯一主键让数据库来兜这件事。
    """
    if not kb_id or not uid:
        return False
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        cur = conn.execute(
            "INSERT OR IGNORE INTO kb_favorites (kb_id, uid, at) VALUES (?, ?, ?)",
            (kb_id, uid, int(time.time())),
        )
        return cur.rowcount > 0


def remove(kb_id: str, uid: str) -> bool:
    """取消收藏。返回**这次是不是真的删掉了**（本来没收藏 → False）。"""
    if not kb_id or not uid:
        return False
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        cur = conn.execute(
            "DELETE FROM kb_favorites WHERE kb_id = ? AND uid = ?", (kb_id, uid)
        )
        return cur.rowcount > 0


def has(kb_id: str, uid: str) -> bool:
    if not kb_id or not uid:
        return False
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT 1 FROM kb_favorites WHERE kb_id = ? AND uid = ?", (kb_id, uid)
        ).fetchone()
    return bool(row)


def count(kb_id: str) -> int:
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT COUNT(*) FROM kb_favorites WHERE kb_id = ?", (kb_id,)
        ).fetchone()
    return int(row[0]) if row else 0


def counts() -> dict[str, int]:
    """一次查出所有库的收藏数，给广场卡片用（同 kb_views.counts）。"""
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        rows = conn.execute(
            "SELECT kb_id, COUNT(*) FROM kb_favorites GROUP BY kb_id"
        ).fetchall()
    return {r[0]: int(r[1]) for r in rows}


def mine(uid: str) -> list[dict]:
    """我收藏的库，最近收藏的在前。只剩 kb_id 与时间，展示信息由 API 层补。"""
    if not uid:
        return []
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        rows = conn.execute(
            "SELECT kb_id, at FROM kb_favorites WHERE uid = ? ORDER BY at DESC", (uid,)
        ).fetchall()
    return [{"kb_id": r[0], "at": int(r[1])} for r in rows]


def mine_ids(uid: str) -> set[str]:
    return {x["kb_id"] for x in mine(uid)}


def purge_kb(kb_id: str) -> int:
    """库删除时清掉收藏记录。否则「我的收藏」里会留一张点开就 404 的卡片。"""
    _ensure()
    with store._get_conn() as conn:  # noqa: SLF001
        cur = conn.execute("DELETE FROM kb_favorites WHERE kb_id = ?", (kb_id,))
        return int(cur.rowcount or 0)
