"""自动化验证流量的可信打标。

为什么需要这一层
----------------
看板的真实用户视图数据源是 `api_call_logs`，而我们的截图 / 深色取证 / 宽度量测
脚本走的是**同一个站点、同一批接口**，还会带着真实账号的 cookie 登录态。
不区分的话，"真实用户调用"里混着自动化 —— 2026-11-15 实测：三个测试号一天
300 次调用、0 次对话，被面板算成"人均 100 次"的真实用户。

两层判据（第二层是 11-15 补的，见下）
------------------------------------
第一层 `X-Verify: 1`：声明 + 身份同时成立才采信（见 `is_verify`）。它的问题是
**依赖脚本自觉**——而脚本正是最容易忘事的那一方。

第二层 **UA 自动识别**。11-15 复查发现：无头浏览器截图时是真人账号的 cookie +
`HeadlessChrome` 的 UA，而浏览器导航没法加自定义请求头，所以 X-Verify 永远不会被
带上。实测那一天 `苞米呀`（真人账号）单小时 1444 次调用、7 天 12772 次，
占"真实用户"桶的 96%，其中 85.6% 来自同一个 IP 的 `HeadlessChrome`。
UA 不需要脚本配合，忘不了，所以它才是这层的兜底。

为什么 UA 判定**不需要**身份
----------------------------
因为判据不是"谁在发"而是"用什么客户端发的"：真人不会用无头浏览器。若也要求身份，
苞米呀 这种普通账号的自动化流量就永远洗不干净 —— 那正是这次要解决的问题。

代价是：**真实用户自己写脚本刷**也会落进 internal 桶。为了不把这件事藏起来，
`client`（browser/headless/script）会一起入库，面板上自动化调用是按来源单列的
（"其中无头 M 次 / 脚本 K 次"），安全审计仍然看得见谁在用什么刷。

判定结果写在 `api_call_logs.internal`（请求级），不用"账号级排除"代替：
同一个内部账号既可能是人点、也可能是脚本在跑，按账号排除会把真人使用一起抹掉。
"""

from __future__ import annotations

import threading
import time

VERIFY_HEADER = "x-verify"

# 判为"非真人客户端"的 client 取值（`classify_ua` 的返回值）
AUTOMATED_CLIENTS = frozenset({"headless", "script"})

# 脚本/自动化客户端的 UA 标记（小写子串）。只在 UA 不以 `mozilla/` 开头时才看这些，
# 避免"自带浏览器的 App"因为 UA 里顺带出现某个词被误判（误判会把真人藏起来）。
AUTO_UA_MARKS = (
    "curl/",
    "wget/",
    "python-requests",
    "python-urllib",
    "aiohttp/",
    "httpx/",
    "go-http-client",
    "okhttp/",
    "apache-httpclient",
    "java/",
    "libwww-perl",
    "node-fetch",
    "axios/",
    "undici",
    "postmanruntime",
    "insomnia",
    "playwright",
    "puppeteer",
    "selenium",
    "phantomjs",
)

# 内部账号集合的缓存时长。每次请求都查库是没必要的（而且中间件在请求路径上，
# 多一次 SELECT 就是一次白给的延迟）；60 秒的陈旧窗口只影响"刚标记完的那一分钟内
# 打标是否生效"，可以接受。改标记后由 api/admin.py 调 invalidate() 立即失效。
CACHE_TTL = 60.0

_LOCK = threading.Lock()
_EXPIRES_AT = 0.0
_INTERNAL: frozenset[str] = frozenset()


def classify_ua(ua: str | None) -> str:
    """把 User-Agent 归成四类：`browser` / `headless` / `script` / `''`（缺失）。

    刻意**宁漏不误**：判错的代价不对称 —— 把真人判成自动化，他会从"真实用户"里
    消失且没人发现；把自动化判成真人，只是数字偏高，下一次复查还能抓出来。
    所以只有明确的特征词才判自动化，其余一律当浏览器。
    """
    low = (ua or "").strip().lower()
    if not low:
        return ""
    # HeadlessChrome / HeadlessFirefox / HeadlessEdg 都带这个词
    if "headless" in low:
        return "headless"
    # 浏览器外壳（含 Android WebView、各家 App 内置浏览器）一律先当浏览器
    if low.startswith("mozilla/"):
        return "browser"
    if any(mark in low for mark in AUTO_UA_MARKS):
        return "script"
    return "browser"


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


def is_verify(header_value: str | None, uid: str | None, ua: str | None = None) -> bool:
    """这次请求要不要标成自动化验证流量。

    参数是**已经解析好的**头、uid 与 UA（中间件在 `call_next` 之后才拿得到 uid），
    这样本模块不依赖 Request，便于单测。
    """
    # 第二层：客户端本身就不是人用的。不需要声明，也不需要内部身份 ——
    # 否则普通账号的脚本流量永远洗不干净（见模块 docstring）。
    if classify_ua(ua) in AUTOMATED_CLIENTS:
        return True
    if (header_value or "").strip() != "1":
        return False
    if not uid:
        # 未登录的验证流量（例如截登录页）。它本来就不进真实用户，
        # 打标是为了让"这批自动化请求"在明细里能被单独看见。
        return True
    return str(uid) in _load_internal()
