"""邮件地址与验证码（一张表 `notify_email`）。

## 为什么非要验证

「填个邮箱就能用」听起来省事，实际会造出一个**没有任何地方会报错的失败**：
用户把 `qq.com` 打成 `qq.co`，开关是开的、界面写着「已开启」，然后永远
收不到任何东西，而且他没有任何理由怀疑是地址错了 —— 只会觉得「这功能没用」。

验证码把这件事变成一个当场就能看见的结果：收到 = 通了，收不到 = 现在就知道。

顺带解决第二个问题：发验证码这一步就是在跑完整条 SMTP 链路（连接 → 登录 →
投递）。所以用户点一次「发送验证码」，等于免费做了一次发信自检，
比事后去日志里猜「服务端到底配没配」强得多。

## 三个防滥用的闸门

这个接口会往外发信，谁都能调就会变成用我们的域名发垃圾邮件：

1. **60 秒冷却**：连点两次不会发两封（还能挡住误触）。
2. **每人每天 5 次**：正常用户最多用 2 次（首次 + 换一个），5 次够到离谱。
3. **错 5 次作废**：6 位数字只有一百万种，不设上限的话在线爆破是可行的。
   作废后必须重新发码，而重发受前两条限制 —— 三个闸门是乘起来的。

## 为什么不存明文验证码

库里存的是 `sha256(owner|address|code)`。不是为了防「数据库被拖走」（6 位数字
哈希本身挡不住穷举），而是为了让**日志、备份、误发的截图**里不出现一个
能直接用的验证码。真正的防线是上面的第 3 条。
"""

from __future__ import annotations

import hashlib
import re
import secrets
import time
from typing import Any

from core import session as store

_READY: set[str] = set()

# 本地部分允许的字符集比 RFC 5322 窄很多：校园邮箱几乎都是这些。
# 故意放行的都是常见形态（学号、姓名拼音、点、下划线、加号）。
_ADDR_RE = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")

CODE_TTL = 10 * 60  # 验证码 10 分钟
RESEND_COOLDOWN = 60  # 两次发码间隔
DAILY_LIMIT = 5  # 每人每天最多发几封
MAX_ATTEMPTS = 5  # 一个码最多试几次

_SCHEMA = """
-- 一人一行。换地址走 upsert，旧地址当场作废（verified_at 清零重新验）。
CREATE TABLE IF NOT EXISTS notify_email (
    owner_id     TEXT PRIMARY KEY,
    address      TEXT NOT NULL,
    enabled      INTEGER NOT NULL DEFAULT 1,  -- 用户手动开关；验证通过才真正生效
    verified_at  INTEGER,                     -- NULL = 没验证过，派发时一律跳过
    code_hash    TEXT,                        -- 待验证的码（哈希后）
    code_sent_at INTEGER,
    attempts     INTEGER NOT NULL DEFAULT 0,  -- 当前这个码被试了几次
    sent_day     TEXT NOT NULL DEFAULT '',    -- 发码计数按天重置
    sent_count   INTEGER NOT NULL DEFAULT 0,
    updated_at   INTEGER NOT NULL
);
"""


class MailError(Exception):
    """带用户可见中文说明的校验失败。"""


class MailCooldown(MailError):
    """太频繁。消息里带还要等几秒。"""


class MailQuota(MailError):
    """今天的发信次数用完了。"""


def _db_path() -> str:
    return store._db_path()  # noqa: SLF001


def _conn():
    return store._get_conn()  # noqa: SLF001


def ensure() -> None:
    path = _db_path()
    if path in _READY:
        return
    with _conn() as conn:
        conn.executescript(_SCHEMA)
    _READY.add(path)


def reset_cache() -> None:
    """测试用：换库之后清掉「已建表」的记忆。"""
    _READY.clear()


# ---------- 地址 ----------


def normalize(address: Any) -> str:
    """归一化并校验。非法直接抛 MailError（消息直接给用户看）。"""
    addr = str(address or "").strip()
    if not addr:
        raise MailError("邮箱地址不能为空")
    if len(addr) > 254:
        raise MailError("这个邮箱地址太长了")
    if any(ch in addr for ch in ("\r", "\n", " ", ",", ";", "\x00")):
        raise MailError("邮箱地址里不能有空格或逗号")
    if not _ADDR_RE.match(addr):
        raise MailError("邮箱格式不对，检查一下 @ 前后")
    local, _, domain = addr.partition("@")
    if ".." in domain or domain.startswith(("-", ".")) or domain.endswith(("-", ".")):
        raise MailError("邮箱域名不对")
    # 域名统一小写（QQ.COM 和 qq.com 是同一家）；本地部分保持原样 ——
    # 它在 RFC 里区分大小写，虽然几乎没人这么用，改了可能投不出去。
    return f"{local}@{domain.lower()}"


def _hash(owner_id: str, address: str, code: str) -> str:
    raw = f"{owner_id}|{address}|{code}".encode()
    return hashlib.sha256(raw).hexdigest()


def _day(ts: int | None = None) -> str:
    from core import timeutil

    return timeutil.day_str(ts or int(time.time()))


# ---------- 读 ----------


def get(owner_id: str) -> dict[str, Any] | None:
    """当前状态。没有记录返回 None。

    返回 `verified` 而不是只返回地址：界面要能区分「填了但没验」
    和「验过了」，否则用户会以为已经生效。
    """
    ensure()
    with _conn() as conn:
        row = conn.execute(
            "SELECT address, enabled, verified_at, code_sent_at, attempts "
            "FROM notify_email WHERE owner_id=?",
            (owner_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "address": row["address"],
        "enabled": bool(row["enabled"]),
        "verified": row["verified_at"] is not None,
        "verified_at": row["verified_at"],
        "pending": row["code_sent_at"] is not None and row["verified_at"] is None,
        "code_expires_in": max(
            0, CODE_TTL - (int(time.time()) - int(row["code_sent_at"] or 0))
        )
        if row["code_sent_at"]
        else 0,
        "attempts_left": max(0, MAX_ATTEMPTS - int(row["attempts"] or 0)),
    }


def verified_address(owner_id: str) -> str | None:
    """派发时用：只有「验过 + 开着」才返回地址。"""
    state = get(owner_id)
    if not state or not state["verified"] or not state["enabled"]:
        return None
    return state["address"]


# ---------- 写 ----------


def start_verification(owner_id: str, address: Any) -> str:
    """登记地址并返回明文验证码（调用方负责发信，发信失败要回滚）。

    故意把「生成码」和「发信」分开：发信是网络操作，可能失败；
    如果先落库再发信、发信又失败了，用户手里就没有码，但他被冷却挡住了，
    只能干等 60 秒。所以调用方发信成功后再调 `commit_sent`。
    """
    addr = normalize(address)
    ensure()
    now = int(time.time())
    day = _day(now)

    with _conn() as conn:
        row = conn.execute(
            "SELECT address, verified_at, code_sent_at, sent_day, sent_count "
            "FROM notify_email WHERE owner_id=?",
            (owner_id,),
        ).fetchone()

        if row and row["code_sent_at"]:
            wait = RESEND_COOLDOWN - (now - int(row["code_sent_at"]))
            if wait > 0:
                raise MailCooldown(f"刚发过一封，{wait} 秒后再试")

        used = int(row["sent_count"] or 0) if row and row["sent_day"] == day else 0
        if used >= DAILY_LIMIT:
            raise MailQuota("今天发得太多了，明天再试")

        code = f"{secrets.randbelow(1_000_000):06d}"
        # 换地址必须重验：verified_at 清零。同一地址重发则保留已验证状态，
        # 方便「我不小心点了换一个又填回原地址」的人不至于掉回未验证。
        verified_at = row["verified_at"] if (row and row["address"] == addr) else None
        conn.execute(
            """INSERT INTO notify_email
                   (owner_id, address, enabled, verified_at, code_hash, code_sent_at,
                    attempts, sent_day, sent_count, updated_at)
               VALUES (?,?,1,?,?,?,0,?,?,?)
               ON CONFLICT(owner_id) DO UPDATE SET
                   address=excluded.address,
                   verified_at=excluded.verified_at,
                   code_hash=excluded.code_hash,
                   code_sent_at=excluded.code_sent_at,
                   attempts=0,
                   sent_day=excluded.sent_day,
                   sent_count=excluded.sent_count,
                   updated_at=excluded.updated_at""",
            (owner_id, addr, verified_at, _hash(owner_id, addr, code), now, day, used + 1, now),
        )
    return code


def commit_sent(owner_id: str, address: str) -> None:
    """发信成功后调。目前只做归一化落库（码已在 start_verification 里写好）。

    留着这个函数是为了让调用顺序显式：**先发信、后计数**。
    发信失败时调用 `rollback` 把计数退回去，不让用户为我们的网络抖动买单。
    """
    ensure()
    with _conn() as conn:
        conn.execute(
            "UPDATE notify_email SET updated_at=? WHERE owner_id=? AND address=?",
            (int(time.time()), owner_id, address),
        )


def rollback(owner_id: str) -> None:
    """发信失败时回滚：清掉待验证码、退回一次计数、清掉冷却。"""
    ensure()
    with _conn() as conn:
        conn.execute(
            """UPDATE notify_email
                  SET code_hash=NULL, code_sent_at=NULL, attempts=0,
                      sent_count=MAX(0, sent_count - 1), updated_at=?
                WHERE owner_id=?""",
            (int(time.time()), owner_id),
        )


def verify(owner_id: str, code: Any) -> dict[str, Any]:
    """校验验证码。成功返回最新状态，失败抛 MailError。"""
    ensure()
    digits = str(code or "").strip()
    if not digits.isdigit() or len(digits) != 6:
        raise MailError("验证码是 6 位数字")

    now = int(time.time())
    # ★ 这里的 `error` 变量不是为了好看：`session._get_conn()` 是
    #   「正常退出 commit / 抛异常 rollback」的上下文管理器。如果在块内
    #   「先 UPDATE attempts+1 再 raise」，那次 +1 会被回滚掉 ——
    #   表现是错多少次的计数永远是 0，MAX_ATTEMPTS 形同虚设，6 位验证码
    #   可以被在线穷举。所以先记下要报的错，退出 with（提交），再抛。
    error: str | None = None
    with _conn() as conn:
        row = conn.execute(
            "SELECT address, code_hash, code_sent_at, attempts FROM notify_email WHERE owner_id=?",
            (owner_id,),
        ).fetchone()
        if not row or not row["code_hash"] or not row["code_sent_at"]:
            raise MailError("没有待验证的邮箱，请先发送验证码")
        if now - int(row["code_sent_at"]) > CODE_TTL:
            raise MailError("验证码过期了，重新发一封")
        if int(row["attempts"] or 0) >= MAX_ATTEMPTS:
            raise MailError("试错次数用完了，重新发一封")

        expect = _hash(owner_id, row["address"], digits)
        if not secrets.compare_digest(expect, row["code_hash"]):
            conn.execute(
                "UPDATE notify_email SET attempts=attempts+1, updated_at=? WHERE owner_id=?",
                (now, owner_id),
            )
            left = MAX_ATTEMPTS - (int(row["attempts"] or 0) + 1)
            error = f"验证码不对，还能试 {max(0, left)} 次"
        else:
            conn.execute(
                """UPDATE notify_email
                      SET verified_at=?, code_hash=NULL, code_sent_at=NULL,
                          attempts=0, enabled=1, updated_at=?
                    WHERE owner_id=?""",
                (now, now, owner_id),
            )
    if error:
        raise MailError(error)
    return get(owner_id) or {}


def set_enabled(owner_id: str, enabled: bool) -> dict[str, Any]:
    ensure()
    with _conn() as conn:
        conn.execute(
            "UPDATE notify_email SET enabled=?, updated_at=? WHERE owner_id=?",
            (1 if enabled else 0, int(time.time()), owner_id),
        )
    state = get(owner_id)
    if state is None:
        raise MailError("还没设置邮箱")
    return state


def clear(owner_id: str) -> None:
    """彻底删掉（用户要求「不要这个邮箱」时用）。"""
    ensure()
    with _conn() as conn:
        conn.execute("DELETE FROM notify_email WHERE owner_id=?", (owner_id,))
