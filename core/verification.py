"""安全验证持久层；显式指定库与密钥，显式迁移，不接线现有用户接口。

调用方必须从服务端身份和当前绑定记录构造 Context，禁止直接接受浏览器提交的版本号。
consume 的业务回调共用事务；不要在回调内发网络请求、提交或关闭连接。
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core import security_mail

# 验证码相关的失败对外只说这一句。send 阶段已经把"地址是否存在"抹平，
# verify 阶段若还按"请求不存在"/"账号不存在"/"验证码不对"分几句回答，
# 不登录的攻击者就能拿 request_id 反推邮箱有没有注册过。
INVALID_PROOF = "验证码不正确或尝试次数已达上限"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS security_emails(
 user_id TEXT PRIMARY KEY, address TEXT NOT NULL UNIQUE,
 verified_at INTEGER NOT NULL, binding_version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS verification_requests(
 id TEXT PRIMARY KEY, slot TEXT NOT NULL, context TEXT NOT NULL,
 code_hash TEXT NOT NULL, expires INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 state TEXT NOT NULL CHECK(state IN ('sending','sent','failed','verified','locked')));
CREATE TABLE IF NOT EXISTS verification_slots(
 slot TEXT PRIMARY KEY, latest_id TEXT NOT NULL, active_id TEXT);
CREATE TABLE IF NOT EXISTS verification_grants(
 digest TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, context TEXT NOT NULL,
 expires INTEGER NOT NULL, consumed INTEGER);
CREATE TABLE IF NOT EXISTS verification_rate_limits(
 bucket TEXT NOT NULL, window TEXT NOT NULL, count INTEGER NOT NULL,
 PRIMARY KEY(bucket,window));
CREATE TABLE IF NOT EXISTS verification_cooldowns(
 bucket TEXT PRIMARY KEY, sent_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_verification_expiry ON verification_requests(expires);
CREATE INDEX IF NOT EXISTS idx_grant_expiry ON verification_grants(expires);
"""


class VerificationError(ValueError):
    """只提供不含邮箱、验证码及凭证的错误。"""


@dataclass(frozen=True)
class Context:
    purpose: str
    address: str
    subject: str
    flow: str
    auth_version: int = 0
    binding_version: int = 0


@dataclass(frozen=True)
class Limits:
    cooldown: int = 60
    daily: int = 10
    ip_short: int = 20
    actor_short: int = 20
    global_daily: int = 1000
    attempts: int = 5
    grant_ttl: int = 300

    def __post_init__(self):
        if any(not isinstance(v, int) or v <= 0 for v in asdict(self).values()):
            raise ValueError("限额必须是正整数")


@dataclass(frozen=True)
class Reservation:
    request_id: str
    code: str = field(repr=False)


def normalize_address(address: str) -> str:
    if not isinstance(address, str):
        raise VerificationError("邮箱格式不正确")
    address = address.strip()
    if len(address) > 254 or not security_mail._ADDRESS.fullmatch(address):
        raise VerificationError("邮箱格式不正确")
    local, domain = address.rsplit("@", 1)
    if ".." in domain or domain.startswith(("-", ".")) or domain.endswith(("-", ".")):
        raise VerificationError("邮箱格式不正确")
    return f"{local}@{domain.lower()}"


def canonical(context: Context) -> Context:
    if (
        context.purpose
        not in (
            "register_email",
            "bind_email",
            "change_email_old",
            "change_email_new",
            "reset_password",
            "login_email",
        )
        or not isinstance(context.subject, str)
        or not 0 < len(context.subject) <= 256
        or not isinstance(context.flow, str)
        or not 0 < len(context.flow) <= 256
        or not isinstance(context.auth_version, int)
        or context.auth_version < 0
        or not isinstance(context.binding_version, int)
        or context.binding_version < 0
    ):
        raise VerificationError("验证上下文不正确")
    return replace(context, address=normalize_address(context.address))


def serialize(context: Context) -> str:
    return json.dumps(asdict(context), sort_keys=True, separators=(",", ":"))


class VerificationStore:
    def __init__(self, path: str | Path, secret: bytes, *, clock=time.time, limits: Limits | None = None):
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise ValueError("验证专用摘要密钥至少需要 32 字节")
        self.path = Path(path)
        self.secret = secret
        self.clock = clock
        self.limits = limits or Limits()

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def migrate(self):
        """仅对显式指定的数据库新增专用表，不读写既有用户表。"""
        with sqlite3.connect(self.path) as db:
            db.executescript(_SCHEMA)

    def _hash(self, category: str, value: str) -> str:
        return hmac.new(self.secret, f"{category}:{value}".encode(), hashlib.sha256).hexdigest()

    def _slot(self, ctx: Context) -> str:
        return self._hash("slot", json.dumps([ctx.purpose, ctx.subject, ctx.flow]))

    def reserve(self, context: Context, ip: str) -> Reservation:
        ctx = canonical(context)
        try:
            ip = str(ipaddress.ip_address(ip))
        except ValueError as exc:
            raise VerificationError("请求来源不正确") from exc
        now = int(self.clock())
        day = datetime.fromtimestamp(now, timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        target = self._hash("address", ctx.address)
        buckets = [
            (target, day, self.limits.daily),
            (self._hash("ip", ip), str(now // 600), self.limits.ip_short),
            (self._hash("actor", ctx.subject), str(now // 600), self.limits.actor_short),
            (self._hash("global", "mail"), day, self.limits.global_daily),
        ]
        request_id, code = secrets.token_urlsafe(24), f"{secrets.randbelow(1000000):06d}"
        slot, payload = self._slot(ctx), serialize(ctx)
        with self.transaction() as db:
            last = db.execute("SELECT sent_at FROM verification_cooldowns WHERE bucket=?", (target,)).fetchone()
            if last and now - last[0] < self.limits.cooldown:
                raise VerificationError("发送过于频繁，请稍后再试")
            for bucket, window, maximum in buckets:
                row = db.execute(
                    "SELECT count FROM verification_rate_limits WHERE bucket=? AND window=?", (bucket, window)
                ).fetchone()
                if row and row[0] >= maximum:
                    raise VerificationError("发送次数已达上限")
            for bucket, window, _ in buckets:
                db.execute(
                    "INSERT INTO verification_rate_limits VALUES (?,?,1) "
                    "ON CONFLICT(bucket,window) DO UPDATE SET count=count+1",
                    (bucket, window),
                )
            db.execute(
                "INSERT INTO verification_cooldowns VALUES (?,?) "
                "ON CONFLICT(bucket) DO UPDATE SET sent_at=excluded.sent_at",
                (target, now),
            )
            db.execute(
                "INSERT INTO verification_requests(id,slot,context,code_hash,expires,state) "
                "VALUES (?,?,?,?,?,'sending')",
                (
                    request_id,
                    slot,
                    payload,
                    self._hash("code", request_id + payload + code),
                    now + security_mail.CODE_TTL_SECONDS,
                ),
            )
            db.execute(
                "INSERT INTO verification_slots(slot,latest_id) VALUES (?,?) "
                "ON CONFLICT(slot) DO UPDATE SET latest_id=excluded.latest_id",
                (slot, request_id),
            )
        return Reservation(request_id, code)

    def finish(self, request_id: str, accepted: bool) -> bool:
        """晚到或重复回调只返回 False，不覆盖新请求或复活失败的请求。"""
        with self.transaction() as db:
            row = db.execute(
                "SELECT r.* FROM verification_requests r JOIN verification_slots s ON r.slot=s.slot "
                "WHERE r.id=? AND r.state='sending' AND s.latest_id=r.id",
                (request_id,),
            ).fetchone()
            if not row or row["expires"] <= int(self.clock()):
                return False
            db.execute(
                "UPDATE verification_requests SET state=? WHERE id=?", ("sent" if accepted else "failed", request_id)
            )
            if accepted:
                db.execute("UPDATE verification_slots SET active_id=? WHERE slot=?", (request_id, row["slot"]))
        return True

    def send(self, context: Context, ip: str, sender=security_mail.send_code) -> str:
        ctx = canonical(context)
        reservation = self.reserve(ctx, ip)
        try:
            result = sender(ctx.address, reservation.code, ctx.purpose)
            accepted = result.get("ok") is True
        except Exception:
            self.finish(reservation.request_id, False)
            raise VerificationError("邮件服务暂时不可用") from None
        current = self.finish(reservation.request_id, accepted)
        if not accepted or not current:
            raise VerificationError("邮件未发送成功或请求已失效")
        return reservation.request_id

    def verify(self, request_id: str, code: str, context: Context) -> str:
        ctx = canonical(context)
        payload, now = serialize(ctx), int(self.clock())
        error = False
        grant = secrets.token_urlsafe(32)
        with self.transaction() as db:
            row = db.execute(
                "SELECT r.* FROM verification_requests r JOIN verification_slots s ON r.slot=s.slot "
                "WHERE r.id=? AND s.active_id=r.id AND r.state='sent'",
                (request_id,),
            ).fetchone()
            if not row or row["context"] != payload or row["expires"] <= now:
                raise VerificationError(INVALID_PROOF)
            matches = isinstance(code, str) and bool(re.fullmatch(r"[0-9]{6}", code))
            matches = matches and hmac.compare_digest(row["code_hash"], self._hash("code", request_id + payload + code))
            if not matches:
                count = row["attempts"] + 1
                db.execute(
                    "UPDATE verification_requests SET attempts=?,state=? WHERE id=?",
                    (count, "locked" if count >= self.limits.attempts else "sent", request_id),
                )
                error = True
            else:
                db.execute("UPDATE verification_requests SET state='verified' WHERE id=?", (request_id,))
                db.execute(
                    "INSERT INTO verification_grants VALUES (?,?,?,?,NULL)",
                    (self._hash("grant", grant), request_id, payload, now + self.limits.grant_ttl),
                )
        # 必须在事务提交后抛错，否则错误次数会被回滚。
        if error:
            raise VerificationError(INVALID_PROOF)
        return grant

    def consume(self, grant: str, context: Context, effect, *, current_context=None) -> Any:
        """单凭证消费；业务写入和消费共用事务，详见 consume_many。"""
        return self.consume_many([(grant, context)], effect, current_context=current_context)

    def consume_many(self, proofs, effect, *, current_context=None) -> Any:
        """多凭证和业务回调原子提交，用于后续旧/新邮箱共同验证。

        对已登录账号用途，必须提供 current_context(db, expected)，在持有写锁时
        根据真实账号状态和绑定记录构造上下文。不可直接返回客户端版本号。
        注册流程无既有账号，可省略解析器。回调只可使用传入连接，不可自行提交。
        """
        proofs = list(proofs)
        if not proofs or len(proofs) > 2:
            raise VerificationError("验证凭证数量不正确")
        if len(proofs) == 2:
            left, right = (canonical(item[1]) for item in proofs)
            if {left.purpose, right.purpose} != {"change_email_old", "change_email_new"} or (
                left.subject,
                left.flow,
                left.auth_version,
                left.binding_version,
            ) != (right.subject, right.flow, right.auth_version, right.binding_version):
                raise VerificationError("换绑凭证必须属于同一账号和流程")
        now = int(self.clock())
        digests = []
        with self.transaction() as db:
            for grant, context in proofs:
                expected = canonical(context)
                if expected.purpose != "register_email" and current_context is None:
                    raise VerificationError("必须在事务内确认当前账号状态")
                actual = canonical(current_context(db, expected)) if current_context else expected
                payload = serialize(expected)
                if serialize(actual) != payload:
                    raise VerificationError("账号状态或邮箱绑定已变化")
                digest = self._hash("grant", grant)
                if digest in digests:
                    raise VerificationError("不能重复使用同一凭证")
                row = db.execute(
                    "SELECT g.* FROM verification_grants g JOIN verification_requests r ON g.request_id=r.id "
                    "JOIN verification_slots s ON r.slot=s.slot WHERE g.digest=? AND s.active_id=r.id",
                    (digest,),
                ).fetchone()
                if not row or row["consumed"] is not None or row["expires"] <= now or row["context"] != payload:
                    raise VerificationError("验证凭证不存在或已失效")
                digests.append(digest)
            result = effect(db)
            db.executemany(
                "UPDATE verification_grants SET consumed=? WHERE digest=?", [(now, digest) for digest in digests]
            )
            return result
