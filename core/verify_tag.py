"""自动化验证流量的可信打标（`X-Verify: 1`）。

为什么需要这一层
----------------
看板的真实用户视图数据源是 `api_call_logs`，而我们的截图 / 深色取证 / 宽度量测
脚本走的是**同一个站点、同一批接口**，还会带着真实账号的 cookie 登录态。
不区分的话，"真实用户调用"里混着自动化 —— 2026-11-15 实测：三个测试号一天
300 次调用、0 次对话，被面板算成"人均 100 次"的真实用户。

为什么判据是"谁在发"而不是"请求头说了什么"
------------------------------------------
`X-Verify` 是客户端自报的，任何人都能加。若只看头，一个真实用户加一行
`X-Verify: 1` 就能把自己的调用从看板上抹掉 —— 那不是"清洗数据"，是给了
一个把证据藏起来的开关。所以：

  声明（请求头）+ 身份（内部账号 / 管理员）**同时成立**才采信。

普通账号发的头会被忽略，调用照常计入真实用户。匿名请求（未登录的身份探测）
允许打标：它们本来就落在 anonymous 桶，打标只是让自动化流量在明细里可辨认。

判定结果写在 `api_call_logs.internal`（请求级），不用"账号级排除"代替：
同一个内部账号既可能是人点、也可能是脚本在跑，按账号排除会把真人使用一起抹掉。
"""

from __future__ import annotations

import threading
import time

VERIFY_HEADER = "x-verify"

# 内部账号集合的缓存时长。每次请求都查库是没必要的（而且中间件在请求路径上，
# 多一次 SELECT 就是一次白给的延迟）；60 秒的陈旧窗口只影响"刚标记完的那一分钟内
# 打标是否生效"，可以接受。改标记后由 api/admin.py 调 invalidate() 立即失效。
CACHE_TTL = 60.0

_LOCK = threading.Lock()
_EXPIRES_AT = 0.0
_INTERNAL: frozenset[str] = frozenset()


def invalidate() -> None:
    """让缓存立刻失效（标记 / 取消标记内部账号后调用，测试里也用）。"""
    global _EXPIRES_AT, _INTERNAL
    with _LOCK:
        _EXPIRES_AT = 0.0
        _INTERNAL = frozenset()


def _load_internal() -> frozenset[str]:
    """内部账号 + 管理员的 uid 集合（短缓存）。查不到就沿用上一次（失败即收紧）。"""
    global _EXPIRES_AT, _INTERNAL
    now = time.monotonic()
    if now < _EXPIRES_AT:
        return _INTERNAL
    ids: set[str] = set()
    try:
        from core import config

        ids.update(str(x) for x in (getattr(config, "ADMIN_UIDS", None) or ()) if x)
        names = [str(x) for x in (getattr(config, "ADMIN_USERNAMES", None) or ()) if x]
        from core import session as store

        with store._get_conn() as conn:  # noqa: SLF001 —— 项目内既定用法
            for r in conn.execute("SELECT id FROM users WHERE is_internal = 1"):
                ids.add(str(r[0]))
            if names:
                marks = ",".join("?" * len(names))
                for r in conn.execute(
                    "SELECT id FROM users WHERE username IN (" + marks + ")", names
                ):
                    ids.add(str(r[0]))
    except Exception:  # noqa: BLE001 —— 打标判定绝不能影响请求本身
        with _LOCK:
            return _INTERNAL
    frozen = frozenset(ids)
    with _LOCK:
        _INTERNAL = frozen
        _EXPIRES_AT = now + CACHE_TTL
    return frozen


def is_verify(header_value: str | None, uid: str | None) -> bool:
    """这次请求要不要标成自动化验证流量。

    参数是**已经解析好的**头与 uid（中间件在 `call_next` 之后才拿得到 uid），
    这样本模块不依赖 Request，便于单测。
    """
    if (header_value or "").strip() != "1":
        return False
    if not uid:
        # 未登录的验证流量（例如截登录页）。它本来就不进真实用户，
        # 打标是为了让"这批自动化请求"在明细里能被单独看见。
        return True
    return str(uid) in _load_internal()
