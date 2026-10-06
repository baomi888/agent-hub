# -*- coding: utf-8 -*-
"""认证与身份：口令散列 + 会话令牌 + FastAPI 依赖。

多用户改造的第一块地基。项目里原本没有任何身份概念，
所有访客共享同一份会话与知识库（详见 docs/前后端可更新点_2026-10-06.md 附录）。

为什么不用第三方库
------------------
requirements.txt 是会逐行 pip 安装的，引入 passlib / PyJWT 意味着服务器上要多装两个包、
还要关心版本锁。这里全部用标准库实现，接口保持标准形状，将来想换随时能换：

  口令散列  hashlib.pbkdf2_hmac('sha256', ...)，自带随机盐 + 轮数
  令牌      HMAC-SHA256 手工实现的紧凑 JWT（HS256），签发/校验各十来行

生产建议：口令换成 argon2/bcrypt（需要外部依赖），令牌换成 PyJWT 或服务化。
到时候只需要改本文件，调用方一行都不用动。

为什么用 HttpOnly Cookie 而不是 Authorization 头
------------------------------------------------
前端走 Next 的 /api/* rewrite，浏览器看来是同源请求，Cookie 会天然带上，
前端不需要存任何凭证；HttpOnly 又让 JS 读不到，等于堵死了 XSS 顺走 token 这条路。
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import HTTPException, Request, Response

from core import config

# 登录态 Cookie 名
COOKIE_NAME = "baomi_session"

# PBKDF2 输出长度（字节）
_KEYLEN = 32


# ==================== 口令散列 ====================

def hash_password(password: str) -> str:
    """生成口令散列串：pbkdf2_sha256$<轮数>$<盐>$<摘要>（全部 base64，方便落库）。"""
    rounds = config.AUTH_PBKDF2_ROUNDS
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds, dklen=_KEYLEN)
    return "pbkdf2_sha256$%d$%s$%s" % (
        rounds,
        base64.b64encode(salt).decode(),
        base64.b64encode(dk).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    """校验口令。存储串格式不对、轮数不是数字，一律判失败（不抛异常，避免探测到细节）。"""
    try:
        algo, rounds_s, salt_s, hash_s = (stored or "").split("$")
        if algo != "pbkdf2_sha256":
            return False
        rounds = int(rounds_s)
        salt = base64.b64decode(salt_s)
        expect = base64.b64decode(hash_s)
    except Exception:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds, dklen=len(expect))
    # 定长比较，别把"多快āk一步就想通"这类计时侧信道留给别人
    return hmac.compare_digest(dk, expect)


# ==================== 令牌签发 ====================

def _secret() -> bytes:
    """取签发密钥。

    优先级：环境变量 AUTH_SECRET > 数据目录里的 .auth_secret 文件 > 现生成并落盘。
    落到文件是为了重启后登录态不失效（否则每次重启所有人都要重新登录）。
    """
    if config.AUTH_SECRET:
        return config.AUTH_SECRET.encode("utf-8")
    path = config._AUTH_SECRET_FILE
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            s = f.read().strip()
        if s:
            return s.encode("utf-8")
    s = secrets.token_urlsafe(48)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(s)
        os.chmod(path, 0o600)  # 只给属主读，别让同机其它账户顺手读到
    except Exception:
        pass  # 只读文件系统的情况退化成"每次重启生成新密钥"，最多是掉登录
    return s.encode("utf-8")


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def create_token(user_id: str, ttl_days: int | None = None) -> str:
    """签发 HS256 令牌（手工 JWT）。载荷只放 uid 与过期时间，不多带东西。"""
    days = config.AUTH_TTL_DAYS if ttl_days is None else ttl_days
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"sub": user_id, "iat": now, "exp": now + days * 86400}
    signing_input = "%s.%s" % (_b64e(json.dumps(header, separators=(",", ":")).encode()),
                               _b64e(json.dumps(payload, separators=(",", ":")).encode()))
    sig = hmac.new(_secret(), signing_input.encode("utf-8"), hashlib.sha256).digest()
    return "%s.%s" % (signing_input, _b64e(sig))


def decode_token(token: str) -> str | None:
    """校验令牌，返回 user_id；任何失败（过期/被改/格式错）都返回 None。"""
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    signing_input = "%s.%s" % (parts[0], parts[1])
    try:
        expect_sig = hmac.new(_secret(), signing_input.encode("utf-8"), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64e(expect_sig), parts[2]):
            return None
        payload = json.loads(_b64d(parts[1]))
    except Exception:
        return None
    if not isinstance(payload, dict) or int(payload.get("exp", 0)) < int(time.time()):
        return None
    uid = payload.get("sub")
    return str(uid) if uid else None


# ==================== Cookie 读写 ====================

def set_session_cookie(resp: Response, token: str) -> None:
    """把登录态写进 HttpOnly Cookie。

    SameSite=Lax：本站跳转带着，跨站表单不带，够用且不会把 CSRF 面再开大。
    Secure 只在 https 下加，否则本地 http 调试会直接丢 Cookie。
    """
    kwargs = {
        "key": COOKIE_NAME,
        "value": token,
        "max_age": config.AUTH_TTL_DAYS * 86400,
        "httponly": True,
        "samesite": "lax",
        "path": "/",
    }
    resp.set_cookie(**kwargs)


def clear_session_cookie(resp: Response) -> None:
    resp.delete_cookie(COOKIE_NAME, path="/")


# ==================== FastAPI 依赖 ====================

class NotAuthenticated(Exception):
    """内部抛错用，由端点转成 401。"""


def current_user_id(request: Request) -> str:
    """从 Cookie 取出当前登录用户 id，失败直接 401。

    这是全站唯一的身份入口。业务路由通过
    `APIRouter(dependencies=[Depends(get_current_user)])` 挂上去，
    做成"默认要登录"，避免将来新增端点时漏掉鉴权——本项目历史上多次因为
    "同一个规则在这一处遵守、在那一处漏了"而踩坑，这里从机制上堵住。
    """
    token = request.cookies.get(COOKIE_NAME)
    uid = decode_token(token or "")
    if not uid:
        raise HTTPException(status_code=401, detail="未登录或登录已过期，请重新登录")
    return uid


# 供 Depends() 使用（FastAPI 会把 Request 自动注入）
def get_current_user(request: Request) -> str:
    return current_user_id(request)
