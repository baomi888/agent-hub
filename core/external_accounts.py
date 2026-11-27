"""第三方身份与内部账号的关联；只接收后端平台适配器验证后的身份。

平台适配器不创建用户。本模块在同一事务内创建内部用户和绑定，
避免并发扫码生成两个账号；未设置密码使用不可用标记，绝不设置默认密码。
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager

from core import account, config, session
from core.auth import has_usable_password, make_unusable_password
from core.external_identity import ExternalAuthError, ExternalIdentity

# 这里只放本站展示和开户规则；平台接口、密钥和身份核验仍归各自适配器。
# 支持支付宝身份模型不等于已开放支付宝登录入口。
_PROVIDERS = {"wechat": ("微信", "wx_"), "alipay": ("支付宝", "ali_")}


def _provider_settings(provider: str) -> tuple[str, str]:
    if provider not in _PROVIDERS:
        raise ExternalAuthError("provider_invalid", "不支持的登录平台")
    return _PROVIDERS[provider]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS external_identities (
    provider TEXT NOT NULL, app_id TEXT NOT NULL, subject TEXT NOT NULL,
    user_id TEXT NOT NULL, created_at INTEGER NOT NULL,
    PRIMARY KEY (provider, app_id, subject),
    UNIQUE (user_id, provider, app_id)
);
CREATE INDEX IF NOT EXISTS idx_external_user ON external_identities(user_id);
CREATE TABLE IF NOT EXISTS external_auth_flows (
    digest TEXT PRIMARY KEY, browser_digest TEXT NOT NULL, app_id TEXT NOT NULL,
    action TEXT NOT NULL, user_id TEXT NOT NULL, auth_version INTEGER NOT NULL,
    expires INTEGER NOT NULL, provider TEXT NOT NULL DEFAULT 'wechat'
);
-- App 唤起授权的结果交接：回调发生在支付宝 App 内置 WebView，会话 Cookie
-- 只落在那个 WebView 里。这里把结果暂存，由原应用轮询领走并在自己的环境
-- 里签发会话，实现“在哪里发起登录，就在哪里登录进去”。一次领取即删除。
CREATE TABLE IF NOT EXISTS external_auth_handoffs (
    digest TEXT PRIMARY KEY, user_id TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL, result TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL, created_at INTEGER NOT NULL, expires INTEGER NOT NULL
);
"""


def ensure_tables() -> None:
    account._ensure_tables()
    with session._get_conn() as db:
        db.executescript(_SCHEMA)
        if not any(row[1] == "provider" for row in db.execute("PRAGMA table_info(external_auth_flows)")):
            # 旧版本只有微信流程。升级保留有效 state，并明确补成微信来源。
            # 写锁内复查，避免两个进程同时 ALTER；不改动现有账号和绑定。
            db.execute("BEGIN IMMEDIATE")
            if not any(row[1] == "provider" for row in db.execute("PRAGMA table_info(external_auth_flows)")):
                db.execute("ALTER TABLE external_auth_flows ADD COLUMN provider TEXT NOT NULL DEFAULT 'wechat'")


@contextmanager
def transaction():
    # 独立连接、显式写锁：不在事务中调用会自行 commit 的 account helper。
    ensure_tables()
    db = sqlite3.connect(session._db_path(), timeout=10)
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


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _user(db, uid: str, version: int | None = None):
    user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not user or user["status"] != account.STATUS_ENABLE:
        raise ExternalAuthError("account_unavailable", "账号不可用，请联系管理员", 403)
    if version is not None and user["token_valid_after"] != version:
        raise ExternalAuthError("flow_invalid", "账号状态已变化，请重新发起操作")
    return user


def begin_flow(browser: str, app_id: str, action: str, uid: str = "", *, provider: str) -> str:
    _provider_settings(provider)
    if action not in ("login", "bind", "unlink") or (action != "login" and not uid):
        raise ExternalAuthError("flow_invalid", "请登录后再操作")
    state = secrets.token_urlsafe(32)
    now = int(time.time())
    with transaction() as db:
        version = _user(db, uid)["token_valid_after"] if uid else 0
        db.execute("DELETE FROM external_auth_flows WHERE expires<=?", (now,))
        db.execute(
            "INSERT INTO external_auth_flows "
            "(digest,browser_digest,app_id,action,user_id,auth_version,expires,provider) VALUES (?,?,?,?,?,?,?,?)",
            (_digest(state), _digest(browser), app_id, action, uid, version, now + 600, provider),
        )
    return state


def put_handoff(
    state: str,
    *,
    provider: str,
    action: str,
    user_id: str = "",
    result: str = "ok",
    reason: str = "",
    ttl: int = 300,
) -> None:
    """登记一次 App 唤起授权的结果，供原应用轮询领走。

    只在回调成功/失败落定后调用，state 用未编码的真实 state，digest 作主键；
    用户从未领取也会在 ttl 后自然失效。
    """
    _provider_settings(provider)
    now = int(time.time())
    with transaction() as db:
        db.execute("DELETE FROM external_auth_handoffs WHERE expires<=?", (now,))
        db.execute(
            "INSERT OR REPLACE INTO external_auth_handoffs "
            "(digest,user_id,action,result,reason,provider,created_at,expires) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                _digest(state), user_id, action, result, reason,
                provider, now, now + ttl,
            ),
        )


def take_handoff(state: str, *, provider: str) -> dict | None:
    """原子领取并删除交接结果；没有或过期返回 None。"""
    _provider_settings(provider)
    now = int(time.time())
    with transaction() as db:
        row = db.execute(
            "SELECT * FROM external_auth_handoffs WHERE digest=? AND provider=?",
            (_digest(state), provider),
        ).fetchone()
        if not row or row["expires"] <= now:
            return None
        db.execute("DELETE FROM external_auth_handoffs WHERE digest=?", (row["digest"],))
        return dict(row)


def consume_flow(state: str, browser: str, app_id: str, current_uid: str, *, provider: str) -> dict:
    """核对回调所属平台后原子消费 state，再请求平台；不重放授权码。"""
    _provider_settings(provider)
    with transaction() as db:
        row = db.execute("SELECT * FROM external_auth_flows WHERE digest=?", (_digest(state),)).fetchone()
        if (
            not row
            or row["expires"] <= int(time.time())
            or row["provider"] != provider
            or row["app_id"] != app_id
            or not secrets.compare_digest(row["browser_digest"], _digest(browser))
            or row["user_id"] != current_uid
        ):
            raise ExternalAuthError("flow_invalid", "登录流程已失效或账号已切换，请重新扫码")
        if row["user_id"]:
            _user(db, row["user_id"], row["auth_version"])
        db.execute("DELETE FROM external_auth_flows WHERE digest=?", (row["digest"],))
        return dict(row)


def _has_password_for_unlink(db, uid: str) -> bool:
    # 保留账号时必须先设置本站密码；仅有安全邮箱还不够。注销不受此限制。
    return has_usable_password(_user(db, uid)["password_hash"])


def complete(identity: ExternalIdentity, flow: dict) -> dict:
    """创建账号/绑定/解绑；平台身份只允许来自服务端适配器，不公开 JSON 入参。"""
    if (
        identity.provider not in _PROVIDERS
        or identity.provider != flow.get("provider")
        or identity.app_id != flow["app_id"]
        or not identity.subject
        or len(identity.subject) > 256
    ):
        raise ExternalAuthError("identity_invalid", "第三方身份验证失败")
    label, username_prefix = _provider_settings(identity.provider)
    key = (identity.provider, identity.app_id, identity.subject)
    now = int(time.time())
    with transaction() as db:
        bound = db.execute(
            "SELECT user_id FROM external_identities WHERE provider=? AND app_id=? AND subject=?", key
        ).fetchone()
        uid, action = flow["user_id"], flow["action"]
        if action == "login":
            if bound:
                uid = bound["user_id"]
                _user(db, uid)  # 孤儿绑定、停用账号不能被误当成新用户重新创建。
            else:
                if not config.ALLOW_SIGNUP:
                    raise ExternalAuthError("signup_closed", f"暂未开放新账号注册，请先登录已有账号再绑定{label}", 403)
                uid = uuid.uuid4().hex
                # 独立生成登录名，不用昵称或第三方标识做登录名，也不赋予管理员名。
                while True:
                    username = username_prefix + secrets.token_hex(12)
                    if (
                        not account.is_reserved_admin_name(username)
                        and not db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone()
                    ):
                        break
                nickname = (
                    "".join(c for c in identity.nickname if c.isprintable()).strip()[: account.NICKNAME_MAX]
                    or label + "用户"
                )
                db.execute(
                    "INSERT INTO users (id,username,password_hash,nickname,created_at,last_login_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (uid, username, make_unusable_password(), nickname, now, now),
                )
                db.execute("INSERT INTO external_identities VALUES (?,?,?,?,?)", (*key, uid, now))
        else:
            _user(db, uid, flow["auth_version"])
            if bound and bound["user_id"] != uid:
                raise ExternalAuthError("already_bound", f"这个{label}已绑定其他账号，不能覆盖", 409)
            if action == "bind":
                other = db.execute(
                    "SELECT subject FROM external_identities WHERE user_id=? AND provider=? AND app_id=?",
                    (uid, identity.provider, identity.app_id),
                ).fetchone()
                if other and other["subject"] != identity.subject:
                    raise ExternalAuthError("already_bound", f"当前账号已经绑定另一个{label}，请先解绑", 409)
                if not bound:
                    db.execute("INSERT INTO external_identities VALUES (?,?,?,?,?)", (*key, uid, now))
            elif action == "unlink":
                if not bound:
                    raise ExternalAuthError("not_bound", f"请使用当前账号已绑定的{label}确认解绑")
                if not _has_password_for_unlink(db, uid):
                    raise ExternalAuthError("last_login_method", f"请先设置本站密码，再解绑{label}", 409)
                db.execute("DELETE FROM external_identities WHERE provider=? AND app_id=? AND subject=?", key)
                # 解绑会撤销旧会话；路由为正在操作的当前浏览器重签凭证。
                db.execute("UPDATE users SET token_valid_after=token_valid_after+1 WHERE id=?", (uid,))
            else:
                raise ExternalAuthError("flow_invalid", "操作用途不正确")
        db.execute("UPDATE users SET last_login_at=? WHERE id=?", (now, uid))
        return dict(_user(db, uid))


def is_bound(uid: str, app_id: str, *, provider: str) -> bool:
    _provider_settings(provider)
    with transaction() as db:
        return bool(
            db.execute(
                "SELECT 1 FROM external_identities WHERE user_id=? AND provider=? AND app_id=?", (uid, provider, app_id)
            ).fetchone()
        )


def identity_exists(identity: ExternalIdentity) -> bool:
    _provider_settings(identity.provider)
    with transaction() as db:
        return bool(
            db.execute(
                "SELECT 1 FROM external_identities WHERE provider=? AND app_id=? AND subject=?",
                (identity.provider, identity.app_id, identity.subject),
            ).fetchone()
        )
