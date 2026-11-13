# -*- coding: utf-8 -*-
"""pytest 全局夹具：让限流窗口只在一个用例**内部**生效。

## 为什么需要它

`core.quota` 的滑动窗口是两层：进程内字典（`_HITS` / `_SHARED_CACHE` 等）
+ 落库的秒桶（`rate_buckets` 表）。这两层都跨用例不清 —— 全量 1600+ 条跑下来，
同一个 IP（TestClient 恒为 `testclient`）的注册桶、同一个 uid 的改密码桶
必然被打满，后跑的用例拿到 429，注册失败后又变成 401「登录名或密码不正确」。

这类红最难判，因为它**看起来像回归**。2026-11-13 实测：同一份「只改了前端 CSS」
的改动，带改动跑全量红 8 条，`git stash` 掉再跑红 11 条 —— 红的**集合**随执行
顺序变，跟改动方向都没有关系。前一天把它归因成"本地 dev server 没停"，
那是错的：真正的原因是这里的桶没隔离。

所以在每个用例前后清空窗口，等价于"中间过了一分钟"。限流测试仍然有效 ——
它们是在**一个用例里**连打十几次，桶照样会满，那才是限流本身要知道的事。
"""

import pytest

from core import quota


def _reset_window() -> None:
    quota._HITS.clear()
    quota._pending.clear()
    quota._last_flush.clear()
    quota._SHARED_CACHE.clear()
    try:
        conn = quota._raw_conn()
        conn.execute("DELETE FROM rate_buckets")
        conn.commit()
    except Exception:
        # 表还没建（用例会在自己的 setUp 里建），或库路径正被换掉 —— 都不算错
        pass


@pytest.fixture(autouse=True)
def _fresh_rate_window():
    _reset_window()
    yield
    _reset_window()
