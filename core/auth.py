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
    salt_b64 = base64.b64encode(salt).decode()
    dk_b64 = base64.b64encode(dk).decode()
    return f"pbkdf2_sha256${rounds}${salt_b64}${dk_b64}"


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

# ---- Claims 字段名 ----
# 对应 sky-take-out 的 `JwtClaimsConstant`（那边是 EMP_ID / USER_ID / USERNAME）。
# 这里只保留本项目需要的三个：谁（uid）、叫什么（username）、这一枚令牌的身份（jti）。
CLAIM_UID = "uid"
CLAIM_USERNAME = "username"
CLAIM_JTI = "jti"

# 兼容历史令牌：早期只写了标准字段 sub，没有 uid / jti
_LEGACY_SUB = "sub"


def _secret() -> bytes:
    """取签发密钥。

    优先级：环境变量 AUTH_SECRET > 数据目录里的 .auth_secret 文件 > 现生成并落盘。
    落到文件是为了重启后登录态不失效（否则每次重启所有人都要重新登录）。
    """
    if config.AUTH_SECRET:
        return config.AUTH_SECRET.encode("utf-8")
    path = config._AUTH_SECRET_FILE
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
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


def create_token(user_id: str, ttl_days: int | None = None, username: str | None = None) -> str:
    """签发 HS256 令牌（手工 JWT）。

    载荷：uid（身份）+ username（展示用）+ jti（吊销用）+ iat/exp。
    和 sky-take-out 的区别是它只放 empId；多带 username 是为了省掉"每次渲染
    导航栏都查一次库"，但**权限判定一律以 uid 为准**（username 可以被改名，
    uid 不行）。

    不放进载荷的东西：角色 / 配额 / 过期后的兜底状态。放进去就等于把
    "改权限"这件事推迟到令牌过期，那是 7 天的窗口。
    """
    days = config.AUTH_TTL_DAYS if ttl_days is None else ttl_days
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": user_id,  # 保留标准字段，老令牌 / 老代码都还认
        CLAIM_UID: user_id,
        CLAIM_USERNAME: username or "",
        "iat": now,
        "exp": now + days * 86400,
        CLAIM_JTI: secrets.token_urlsafe(16),
    }
    signing_input = "{}.{}".format(
        _b64e(json.dumps(header, separators=(",", ":")).encode()),
        _b64e(json.dumps(payload, separators=(",", ":")).encode()),
    )
    sig = hmac.new(_secret(), signing_input.encode("utf-8"), hashlib.sha256).digest()
    return f"{signing_input}.{_b64e(sig)}"


# 唯一接受的算法。校验 header.alg 的意义见 decode_claims 的注释。
_ALG = "HS256"
_TYP = "JWT"


def _check_header(header_b64: str) -> bool:
    """校验 JOSE header：alg 必须是 HS256，typ 存在时必须是 JWT。

    现在的实现**不依赖这个校验**——验签是硬编码 `hmac.new(secret, ..., sha256)`，
    不按 header.alg 分派，所以经典的 `alg: none` / `HS256→RS256` 混淆打不进来。
    这一点必须说清楚，否则会误以为这里堵住了一个真实漏洞。

    真正要修的是**迁移风险**：本文件顶部注释写明"将来想换成 PyJWT"。
    PyJWT 的行为恰恰是按 header.alg 分派算法——迁移那天如果没人记得补这个校验，
    今天这个"无害的宽松"会立刻变成真漏洞。而迁移是最容易漏测的时刻。

    typ 缺失时放行：手工构造的令牌常常省略它，且 typ 不参与安全判定；
    存在则必须是 JWT，否则说明这不是我们签的那类令牌。
    """
    if not getattr(config, "JWT_HEADER_CHECK", True):
        return True
    try:
        raw = _b64d(header_b64)
        header = json.loads(raw)
    except Exception:
        return False
    if not isinstance(header, dict):
        return False
    if str(header.get("alg") or "") != _ALG:
        return False
    typ = header.get("typ")
    if typ is not None and str(typ) != _TYP:
        return False
    return True


def decode_claims(token: str) -> dict | None:
    """校验签名与过期时间，通过返回完整载荷，否则 None。

    任何失败（空串 / 段数不对 / header 不合规 / 签名被改 / 过期 / JSON 坏）都返回
    None，调用方不需要区分——对外统一 401，别把"到底哪一步错了"漏给攻击者。

    header 校验放在验签**之前**：header 是不受签名保护的部分（它本身是签名输入），
    先拒掉明显不对的算法声明，可以少做一次 HMAC。
    """
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    if not _check_header(parts[0]):
        return None
    signing_input = f"{parts[0]}.{parts[1]}"
    try:
        expect_sig = hmac.new(_secret(), signing_input.encode("utf-8"), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64e(expect_sig), parts[2]):
            return None
        payload = json.loads(_b64d(parts[1]))
    except Exception:
        return None
    if not isinstance(payload, dict) or int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload


def decode_token(token: str) -> str | None:
    """校验令牌，返回 user_id；任何失败都返回 None。

    保留这个函数名与返回值形状，是因为历史测试与调用点都按"只取 id"在用；
    需要更多字段（username / jti）的调用方用 decode_claims。
    """
    payload = decode_claims(token)
    if not payload:
        return None
    uid = payload.get(CLAIM_UID) or payload.get(_LEGACY_SUB)
    return str(uid) if uid else None



# ==================== Cookie 读写 ====================


def _is_secure_request(request: Request | None) -> bool:
    """判断当前请求是否走 https。

    反向代理（nginx）会把真实协议放在 ``X-Forwarded-Proto``；而 uvicorn 默认
    不信任该头（未开 --proxy-headers），``request.url.scheme`` 会在 TLS 终结后
    仍显示 http。所以这里显式优先读该头，没有再退回 scheme；两条都取不到就按
    http 处理（保守，宁可不加 Secure 也不让本地 http 调试丢 Cookie）。
    """
    if request is None:
        return False
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if proto:
        return proto == "https"
    try:
        return request.url.scheme == "https"
    except Exception:
        return False


def set_session_cookie(resp: Response, token: str, request: Request | None = None) -> None:
    """把登录态写进 HttpOnly Cookie。

    SameSite=Lax：本站跳转带着，跨站表单不带，够用且不会把 CSRF 面再开大。
    Secure 只在 https 下加，否则本地 http 调试会直接丢 Cookie。

    注意：此前注释这么写、但 kwargs 里漏了 secure，等于**永远不加**——
    上 HTTPS 后会话 Cookie 仍可能被浏览器在 http 下带出/拒存。现已按请求协议补齐。
    """
    kwargs = {
        "key": COOKIE_NAME,
        "value": token,
        "max_age": config.AUTH_TTL_DAYS * 86400,
        "httponly": True,
        "samesite": "lax",
        "path": "/",
        "secure": _is_secure_request(request),
    }
    resp.set_cookie(**kwargs)


def clear_session_cookie(resp: Response) -> None:
    resp.delete_cookie(COOKIE_NAME, path="/")


# ==================== FastAPI 依赖 ====================


_BEARER = "bearer "


def extract_token(request: Request) -> str:
    """从请求里取出令牌，返回空串表示没带。

    读取顺序（对应 sky-take-out 拦截器"从请求头拿令牌"，外加本项目原有的 Cookie）：

      1. 自定义头 ``X-Auth-Token``（名字可用 AUTH_TOKEN_HEADER 改）
         —— 对应 sky 的 admin-token-name / user-token-name；
      2. ``Authorization: Bearer <token>``
         —— 给移动端 / 小程序 / Swagger / 压测脚本用，它们没有 Cookie 罐子；
      3. Cookie ``baomi_session``
         —— 浏览器主路径，HttpOnly，JS 读不到。

    头部优先于 Cookie：显式带 token 的调用方意图明确，不该被浏览器残留的
    旧 Cookie 覆盖掉。
    """
    raw = (request.headers.get(config.AUTH_TOKEN_HEADER) or "").strip()
    if not raw:
        authz = (request.headers.get("authorization") or "").strip()
        if authz.lower().startswith(_BEARER):
            raw = authz[len(_BEARER) :].strip()
    if not raw:
        raw = (request.cookies.get(COOKIE_NAME) or "").strip()
    # 自定义头里也允许直接写 "Bearer xxx"
    if raw.lower().startswith(_BEARER):
        raw = raw[len(_BEARER) :].strip()
    return raw


def current_user_id(request: Request) -> str:
    """从请求中取出令牌并校验，返回 uid；失败直接 401。

    这是全站唯一的身份入口。业务路由通过
    `APIRouter(dependencies=[Depends(get_current_user)])` 挂上去，
    做成"默认要登录"，避免将来新增端点时漏掉鉴权——本项目历史上多次因为
    "同一个规则在这一处遵守、在那一处漏了"而踩坑，这里从机制上堵住。
    """
    token = extract_token(request)
    claims = decode_claims(token)
    if not claims:
        raise HTTPException(status_code=401, detail="未登录或登录已过期，请重新登录")

    jti = str(claims.get(CLAIM_JTI) or "")
    if jti:
        from core import token_store  # 延迟导入，避免 auth ←→ storage 的循环导入

        if token_store.is_revoked(jti):
            raise HTTPException(status_code=401, detail="登录已退出，请重新登录")

    uid = claims.get(CLAIM_UID) or claims.get(_LEGACY_SUB)
    if not uid:
        raise HTTPException(status_code=401, detail="未登录或登录已过期，请重新登录")

    uid = str(uid)
    request.state.uid = uid
    request.state.username = str(claims.get(CLAIM_USERNAME) or "")
    request.state.token_claims = claims
    return uid


# 供 Depends() 使用（FastAPI 会把 Request 自动注入）
def get_current_user(request: Request) -> str:
    return current_user_id(request)
