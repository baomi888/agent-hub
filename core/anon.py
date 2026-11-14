from __future__ import annotations

"""免登录发帖的身份：一枚设备 cookie，加上由它派生出来的固定昵称。

为什么不做"账号"：论坛的定位是站内随手发一句（占座、问一句、出个闲置），
注册会把发帖率打到地板。但完全没有身份又不行 —— 删自己的帖、看「我发的帖」、
管理员追溯都要有一个对象。

所以身份就是 `baomi_anon` 这枚 cookie（HttpOnly / 30 天）。它的值落库成
`user_id = "anon:<32 位 hex>"`，于是 `kb_posts` 一行都不用改结构，
既有的 `user_id == uid` 那套"是不是我的 / 能不能删"判断原样成立。

代价必须写清楚，界面上也要说：
  - 换设备、清 cookie、隐私模式 → 对服务器来说就是另一个人，认不回来；
  - 所以「我发的帖」只能是「这台设备的帖」，文案不能写"我的账号"；
  - 跨设备认领唯一的路是登录，而登录后的帖子 `user_id` 是真 uid，
    与匿名帖不是同一个人 —— 要打通得加一张认领表，这一版刻意不做
    （登录本身在论坛里已经不是前置条件，为它加一张表不划算）。
"""

import hashlib
import re
import secrets

COOKIE = "baomi_anon"
PREFIX = "anon:"

# cookie 值只收 32 位小写十六进制。收窄格式有两个用处：
# ① 伪造的任意字符串进不了库（user_id 会被当昵称显示）；
# ② 判"这是不是一枚合法的匿名身份"不需要查库。
_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")

# 30 天。短了会让人觉得"我刚发的东西没了"，长了等于在这台机器上留一个
# 永久标识 —— 30 天刚好覆盖一个学期的使用节奏。
MAX_AGE = 30 * 24 * 3600

NICK_PREFIX = "玉米"


def new_token() -> str:
    return secrets.token_hex(16)


def is_valid(token: str) -> bool:
    return bool(_TOKEN_RE.match(token or ""))


def uid_of(token: str) -> str:
    return PREFIX + token


def is_anon(uid: str) -> bool:
    return (uid or "").startswith(PREFIX)


def name_of(uid: str) -> str:
    """由 uid 派生的固定昵称：玉米 + 四位数字。

    同一台设备永远是同一个名字（哈希稳定），不同设备几乎不会撞。
    不复用一个随机数落库：那要多一列，而且每次读到还得回填 —— 派生更省。
    """
    h = hashlib.sha256((uid or "").encode("utf-8")).digest()
    n = 1000 + int.from_bytes(h[:4], "big") % 9000
    return f"{NICK_PREFIX}{n}"
