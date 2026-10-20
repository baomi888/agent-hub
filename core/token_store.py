"""令牌吊销名单：让 JWT 也能"退出登录"。

为什么需要这张表
----------------
JWT 是**无状态**的：服务端不存会话，验签通过就认。好处是每请求零查库，
坏处是**签出去的令牌在过期前收不回来**——用户点了"退出登录"，手里那个
token 只要没到期，拿着它仍然能进。sky-take-out 的 `logout` 就是这么写的
（`return Result.success()`，什么都不做），属于教学项目里被简化掉的一块。

这里补上最小闭环：给每个令牌一个 `jti`（JWT ID），退出时把 `jti` 写进
黑名单，验签时顺带查一次。查询走主键索引，是 O(1) 的一次 sqlite 读。

为什么不存"白名单"
------------------
白名单（每个签发过的 token 都落库）等于把无状态退回有状态，200 人在线就是
200 行常驻 + 每请求一次读。黑名单只在退出时才写一行，绝大多数令牌一辈子
不会进这张表。

表结构在 core/session.py::_init_db 里建（和会话表同一个库，测试改库路径时
能一起被隔离），本模块只负责读写。
"""

from __future__ import annotations

import time

from core.session import _get_conn


def revoke(jti: str, exp: int) -> None:
    """把令牌拉黑。重复退出不报错（INSERT OR REPLACE 幂等）。"""
    if not jti:
        return
    now = int(time.time())
    with _get_conn() as conn:
        # 顺手清掉已过期的行：不清理也不会错（过期令牌本来就验不过），
        # 但表会一直长，清理成本极低就放这儿了
        conn.execute("DELETE FROM revoked_tokens WHERE exp < ?", (now,))
        conn.execute(
            "INSERT OR REPLACE INTO revoked_tokens (jti, exp) VALUES (?, ?)",
            (jti, int(exp or 0)),
        )


def is_revoked(jti: str) -> bool:
    """令牌是否已被拉黑。空 jti 视为未拉黑（兼容没有 jti 的历史令牌）。"""
    if not jti:
        return False
    with _get_conn() as conn:
        row = conn.execute("SELECT 1 FROM revoked_tokens WHERE jti = ? LIMIT 1", (jti,)).fetchone()
    return row is not None


def purge(now: int | None = None) -> int:
    """清掉过期行，返回删除条数。给运维脚本 / 定时任务用。"""
    ts = int(time.time()) if now is None else int(now)
    with _get_conn() as conn:
        cur = conn.execute("DELETE FROM revoked_tokens WHERE exp < ?", (ts,))
        return cur.rowcount or 0
