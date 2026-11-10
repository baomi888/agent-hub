from __future__ import annotations

"""登录注册路由。

这是全站**唯一**的公开路由组（其它业务路由都在 main.py 里挂了
`Depends(get_current_user)`，默认要登录）。豁免面刻意压到最小，
将来新增端点时"忘了鉴权"这件事在结构上就不容易发生——
本项目历史上已经因为"同一规则这处遵守、那处漏了"反复踩坑。

路由清单：
  GET   /api/auth/config/    公开：是否开放注册
  POST  /api/auth/register/  公开：注册并直接登录
  POST  /api/auth/login/     公开：登录
  POST  /api/auth/logout/    公开：退出（未登录时调用也返回 ok）
  GET   /api/auth/me/        需登录：当前身份 + 今日用量
  PATCH /api/auth/profile    需登录：改昵称 / 选内置头像（不验密码）
  POST  /api/auth/password   需登录：改密码（验旧密码，踢掉其它设备）
  POST  /api/auth/username   需登录：改登录名（验密码 + 查重 + 限 3 次）
  POST  /api/auth/avatar     需登录：上传自定义头像（≤5MB，落盘 192×192）
  GET   /api/auth/avatar/<hash>  公开：取头像图（hash 是内容指纹，永久可缓存）
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from core import account, config, quota, token_store
from core.auth import (
    CLAIM_JTI,
    clear_session_cookie,
    create_token,
    decode_claims,
    extract_token,
    get_current_user,
    set_session_cookie,
)
from service import avatar as avatar_store

router = APIRouter()


class Credentials(BaseModel):
    username: str = Field(..., description="登录名")
    password: str = Field(..., description="密码")


class UserOut(BaseModel):
    id: str
    username: str
    # 显示名。空串 = 没设过，前端回落到 username。
    # 默认空串而不是必填：老前端拿到多出来的字段不会崩，新前端拿到老后端的
    # 响应（没有这个字段）也有确定的回落值。
    nickname: str = ""
    # 头像。"" = 首字母 / "icon:<name>" = 内置图标 / "img:<hash>" = 上传（第 2 批）
    avatar: str = ""
    # 登录名还能改几次。默认给"上限"而不是 0：老后端不返回这个字段时，
    # 前端应该按"还能改"显示 —— 默认成 0 会让所有人的改名按钮变灰，
    # 那种"升级后功能消失"比"点了才被告知次数用完"糟得多。
    name_changes_left: int = account.MAX_NAME_CHANGES
    # 只在 AUTH_RETURN_TOKEN=1 时回吐；浏览器侧靠 HttpOnly Cookie，不需要它
    token: str | None = None


class MeOut(BaseModel):
    user: UserOut
    quota: dict
    # 是否开发者（ADMIN_USERNAMES）。前端据此决定「数据看板」入口要不要显示。
    # 默认 False：老前端拿到不带这个字段的响应也不会崩，未知一律当"不是管理员"
    # —— 权限判定的默认值必须是拒绝的那一侧。
    is_admin: bool = False


class ProfilePatch(BaseModel):
    """改昵称 / 头像。两个字段都可选，只改传了的那个。"""

    nickname: str | None = None
    avatar: str | None = None


class PasswordChange(BaseModel):
    old_password: str = Field(..., description="原密码")
    new_password: str = Field(..., description="新密码")


class UsernameChange(BaseModel):
    password: str = Field(..., description="当前密码（改名是凭证变更，必须验证）")
    new_username: str = Field(..., description="新登录名")


def _reject(msg: str, status: int = 400) -> None:
    raise HTTPException(status_code=status, detail=msg)


def _auth_rate_bucket(name: str) -> str:
    """登录/注册单独限流：按用户名分桶，避免整站共用限流把正常问答也挡了。

    **先归一化大小写**：users 表是 `COLLATE NOCASE`，`Admin` / `ADMIN` 是同一个账号；
    若按原始串分桶，攻击者换大小写就能拿到 N 个独立的 10 次/分钟额度，
    等于给撞库开了后门。
    """
    return "auth:%s" % (account.normalize_name(name).casefold() or "-")


def _client_ip(request: Request) -> str:
    """取客户端 IP 用于注册限流。

    优先 X-Forwarded-For 的第一个地址（生产部署在后端前面有 Next/nginx 时，
    真实 IP 只在这里）；回退到直连地址。

    ⚠️ 只有"代理会重写 XFF"时才可信：后端默认只监听回环（main.py），
    公网请求必须先经过代理，所以这个假设成立。若把 8000 直接暴露公网，
    攻击者可以伪造 XFF 绕过 IP 限额——S-08（deploy.sh 不再放行 8000）就是在堵这条。
    """
    raw = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if raw:
        return raw
    return getattr(getattr(request, "client", None), "host", "") or "unknown"


def _is_reserved_admin_name(name: str) -> bool:
    """该用户名是否被配置为管理员（注册时保留，防抢注）。

    实现已移到 `core.account.is_reserved_admin_name`：改登录名那条路也必须过
    同一道闸（否则把名字改成 "苞米呀" 就白拿管理员），规则只能有一份。
    这里保留薄封装只为不改动既有调用点。
    """
    return account.is_reserved_admin_name(name)


def _release_avatar(old: str) -> None:
    """旧头像没人再引用时删掉文件（同步，调用方丢线程）。

    为什么要判断"还有人用吗"：文件名是**内容 hash**，两个人可能选了同一张图，
    只存了一份文件。换头像时无条件删就会把别人的图也删掉，表现为
    "我换完头像，同事的头像变破图了"。
    """
    if not (old or "").startswith(avatar_store.IMG_PREFIX):
        return
    if account.is_avatar_referenced(old):
        return
    avatar_store.remove_hash(old[len(avatar_store.IMG_PREFIX) :])


def _name_changes_left(row: dict) -> int:
    """还能改几次登录名。

    ★ 这里踩过坑：row 有两种形状 ——
      · `account.get_user()` 给的是**原始行**，只有 `name_change_count`；
      · `account._public()` 给的是对外 dict，只有折好的 `name_changes_left`。
    早先写成 `row.get("name_changes_left", MAX)`，第一种形状永远命中默认值，
    表现为"改完名剩余次数不减少"，前端一直显示还能改 3 次（上限形同虚设）。
    所以两种形状都要认，优先级：折好的结果 > 原始计数 > 默认值。
    """
    left = row.get("name_changes_left")
    if left is not None:
        return max(0, int(left))
    return max(
        0, account.MAX_NAME_CHANGES - int(row.get("name_change_count") or 0)
    )


def _user_out(row: dict, token: str | None = None) -> UserOut:
    """把用户行转成对外的 UserOut。

    统一走这个函数，是为了让"哪些字段能出网"只有一处可读 —— 前面
    `account._public()` 已经把 password_hash 摘掉了，这里再统一补上 token。
    """
    return UserOut(
        id=row["id"],
        username=row["username"],
        nickname=row.get("nickname") or "",
        avatar=row.get("avatar") or "",
        name_changes_left=_name_changes_left(row),
        token=token,
    )


async def _me_payload(row: dict, token: str | None = None) -> MeOut:
    """用一行用户记录拼出 MeOut（配额快照走线程，别阻塞事件循环）。"""
    snap, is_admin = await asyncio.gather(
        asyncio.to_thread(quota.snapshot, row["id"]),
        asyncio.to_thread(quota._is_admin, row["id"]),
    )
    return MeOut(user=_user_out(row, token=token), quota=snap, is_admin=is_admin)


async def _load_me(uid: str) -> MeOut:
    row = await asyncio.to_thread(account.get_user, uid)
    if not row:
        # 账号被删了但 token 还有效（7 天内）——别当作系统错误，直接判未登录
        raise HTTPException(status_code=401, detail="账号不存在，请重新登录")
    return await _me_payload(row)


def _issue_session(user: dict, request: Request, response: Response) -> UserOut:
    """签发令牌并落到两个地方：HttpOnly Cookie + （可选）响应体。

    两条通道一起给，是因为客户端形态不同：
      - 浏览器：Cookie 自动带，JS 读不到，XSS 顺不走；
      - App / 小程序 / 压测 / Swagger：没有 Cookie 罐子，用 `X-Auth-Token`
        或 `Authorization: Bearer` 把响应体里的 token 带回来。

    响应体里的 token 默认关闭（AUTH_RETURN_TOKEN=0）：给了 JS 就等于把
    XSS 的收益从"偷不到"变成"直接拿走 7 天会话"。
    """
    # af 必须带上当前水位：不带（=0）的话，改过密码的用户下次登录拿到的令牌
    # 一落地就被"af < 水位"判成失效 —— 表现为登录成功却立刻被踢回登录页。
    token = create_token(
        user["id"],
        username=user["username"],
        auth_version=account.get_token_valid_after(user["id"]),
    )
    set_session_cookie(response, token, request)
    return _user_out(user, token=token if config.AUTH_RETURN_TOKEN else None)


# ==================== 公开端点 ====================


@router.get("/config/")
def auth_config() -> dict:
    """前端登录页要用的开关：这站还开不开自助注册。"""
    return {"allow_signup": config.ALLOW_SIGNUP}


@router.post("/register/", response_model=UserOut)
def register(body: Credentials, request: Request, response: Response):
    if not config.ALLOW_SIGNUP:
        _reject("本站已关闭自助注册，请联系管理员开通账号", 403)

    err = account.check_name(body.username) or account.check_password(
        body.password, body.username
    )
    if err:
        _reject(err)

    # 管理员用户名保留：自助注册不能抢注（2026-10-29 安全加固）。
    # 文案与"重名"保持一致，不确认该名字是否真的是管理员。
    if _is_reserved_admin_name(body.username):
        _reject("该登录名不可用，请更换一个", 403)

    # 注册/登录单独限流：撞库脚本最爱的就是这两个端点
    try:
        quota.hit(_auth_rate_bucket(body.username), limit=10)
        # 注册防滥用（S-03）：自助注册是唯一无需登录就能写库的入口。
        # 三道闸按 IP / 全站维度落库计数，堵"脚本批量注册 → 抢注 + 烧额度"。
        ip = _client_ip(request)
        quota.hit(f"signup:ip:{ip}", limit=config.SIGNUP_IP_PER_MINUTE)
        quota.consume_key(f"signup:ip:{ip}", "signup_ip", config.SIGNUP_IP_PER_DAY)
        quota.consume_key("signup:global", "signup_global", config.SIGNUP_GLOBAL_PER_DAY)
    except (quota.RateLimited, quota.QuotaExceeded) as e:
        _reject(str(e), 429)

    try:
        user = account.create_user(body.username, body.password)
    except Exception:
        # 唯一索引冲突 = 重名。这里吞掉 sqlite 的具体类型，
        # 免得数据库的错误信息一路漏到前端
        _reject("该登录名已被占用", 409)

    return _issue_session(user, request, response)


@router.post("/login/", response_model=UserOut)
def login(body: Credentials, request: Request, response: Response):
    """登录：查账号 → 验口令 → 验状态 → 签令牌（对齐 sky-take-out 的四步）。

    与之的差异只有一处，写在下面 if 里：账号不存在与密码错误对外合并成一句话。
    """
    try:
        quota.hit(_auth_rate_bucket(body.username), limit=10)
        # 按 IP 再限一道：只按用户名分桶时，攻击者换用户名就能从同一台机器
        # 无限撞库（每个用户名 10 次/分钟）。账号锁只惩罚"打同一个号"，
        # IP 闸才拦得住"广撒网试一堆号"。
        quota.hit(
            "login:ip:%s" % _client_ip(request),
            limit=config.LOGIN_IP_PER_MINUTE,
        )
    except quota.RateLimited as e:
        _reject(str(e), 429)

    user, reason = account.login(body.username, body.password)

    if reason == account.LOGIN_LOCKED:
        # 必须与"密码错"区分：用户要知道是号被停了，而不是自己记错密码
        _reject("账号已被停用或锁定，请稍后再试或联系管理员", 403)

    if not user:
        # no_user 与 bad_password 返回同一句话：自助注册的站点里，
        # 区分这两者等于免费提供一个"哪些用户名存在"的枚举接口
        _reject("登录名或密码不正确", 401)

    return _issue_session(user, request, response)


@router.post("/logout/")
def logout(request: Request, response: Response) -> dict:
    """退出登录：吊销当前令牌 + 清 Cookie。

    JWT 是无状态的，光删 Cookie 收不回已签出去的令牌（sky-take-out 的
    logout 就是空实现，令牌到期前一直有效）。这里把 jti 写进黑名单，
    让这枚令牌立刻失效——否则"退出登录"只是让浏览器不再自动带，
    换个客户端照样能进。
    """
    claims = decode_claims(extract_token(request)) or {}
    jti = str(claims.get(CLAIM_JTI) or "")
    if jti:
        token_store.revoke(jti, int(claims.get("exp", 0)))
    clear_session_cookie(response)
    return {"ok": True, "revoked": bool(jti)}


# ==================== 需登录 ====================


@router.get("/me/", response_model=MeOut)
async def me(uid: str = Depends(get_current_user)):
    """当前身份。前端用它判断"要不要弹登录"，以及展示今日用量。"""
    return await _load_me(uid)


# ==================== 账号自助（需登录） ====================
# 三个端点都不带尾斜杠：新端点统一规矩（见 main.py 的 normalize_api_slash）。


@router.patch("/profile", response_model=MeOut)
async def update_profile(body: ProfilePatch, uid: str = Depends(get_current_user)):
    """改昵称 / 选内置头像。

    **不需要密码**：这两样只影响"别人怎么看见我"，改错也不伤账号。
    真正是凭证的东西（密码、登录名）在下面两个端点，必须验旧密码。
    """
    if body.nickname is None and body.avatar is None:
        _reject("没有要改的内容")

    try:
        # 按用户分桶限流：这是唯一能高频写 users 表的入口
        await asyncio.to_thread(quota.hit, f"profile:{uid}", 10)
    except quota.RateLimited as e:
        _reject(str(e), 429)

    if body.nickname is not None:
        err = account.check_nickname(body.nickname)
        if err:
            _reject(err)

    # 头像先校验再落库：normalize_avatar 返回 None = 非法，返回 "" = 清空。
    # 用 is None 判断而不是 falsy —— 空串是合法的"清空"。
    avatar = None
    if body.avatar is not None:
        avatar = account.normalize_avatar(body.avatar)
        if avatar is None:
            _reject("头像参数不合法")
        # img: 还要确认文件真的在（格式合法不代表存在）。不查的话手写一个
        # 不存在的 hash 就能把自己的头像变成破图 —— 而用户会以为网站坏了。
        if avatar and not await asyncio.to_thread(avatar_store.exists, avatar):
            _reject("这张头像已经不在服务器上了，请重新上传")

    old_avatar = ""
    if body.nickname is not None:
        await asyncio.to_thread(account.set_nickname, uid, body.nickname)
    if avatar is not None:
        old_avatar = await asyncio.to_thread(account.set_avatar, uid, avatar)
        # 从"上传的照片"换成图标 / 首字母时，旧文件就没人引用了，顺手清掉
        if old_avatar and old_avatar != avatar:
            await asyncio.to_thread(_release_avatar, old_avatar)

    return await _load_me(uid)


@router.post("/avatar", response_model=MeOut)
async def upload_avatar(
    # 不写 `= File(...)`：与 api/kb.py 的写法保持一致，也避开 ruff 的 B008
    # （函数默认值里做调用）。FastAPI 对 UploadFile 注解本来就按 multipart 文件处理。
    file: UploadFile,
    uid: str = Depends(get_current_user),
):
    """上传自定义头像：收文件 → 处理成 192×192 → 落盘 → 换库里的 avatar。

    处理完**立刻删原图**（finally 里的 remove_file）：原图是唯一能让磁盘爆掉的
    东西（5MB × 1000 人 = 5GB），而成品只有 12~18KB。这条是硬规矩。
    """
    try:
        # 和 profile 同档：这是唯一能写磁盘的入口，比对数据库那条更应该限
        await asyncio.to_thread(quota.hit, f"avatar:{uid}", 10)
    except quota.RateLimited as e:
        _reject(str(e), 429)

    tmp = ""
    try:
        tmp = await avatar_store.save_upload(file)
        new_avatar = await asyncio.to_thread(avatar_store.store, tmp)
    except avatar_store.AvatarError as e:
        # 中文、可操作、不含堆栈：这类错误的读者是用户，不是开发者
        _reject(str(e))
    finally:
        if tmp:
            await asyncio.to_thread(avatar_store.remove_file, tmp)

    old_avatar = await asyncio.to_thread(account.set_avatar, uid, new_avatar)
    if old_avatar and old_avatar != new_avatar:
        await asyncio.to_thread(_release_avatar, old_avatar)
    await asyncio.to_thread(account.audit, "avatar_upload", uid, {"avatar": new_avatar})

    return await _load_me(uid)


@router.get("/avatar/{hash}")
def get_avatar(hash: str):
    """返回头像文件。

    **公开可读**：头像本来就不是秘密，而且 `<img src>` 是最简单的消费方式；
    强制鉴权会让每个头像都多一次 401 判定，还会让浏览器的图片缓存失效。
    hash 是 sha256 的前 64 位，正常流量下猜不出来，也就谈不上枚举。

    可以永久缓存：URL 里带 hash，换了头像 URL 就变（`Cache-Control: immutable`）。
    """
    path = avatar_store.path_of(hash)
    if not path:
        raise HTTPException(status_code=404, detail="头像不存在")
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={
            # nosniff：即使哪天有人往目录里塞了别的文件，浏览器也不许改判类型
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "public, max-age=31536000, immutable",
        },
    )


@router.post("/password", response_model=MeOut)
async def change_password(
    body: PasswordChange,
    request: Request,
    response: Response,
    uid: str = Depends(get_current_user),
):
    """改密码：验旧密码 → 校验新密码 → 落库（含令牌水位）→ 给当前这台重签 Cookie。

    最后一步不能省。写完水位后，**所有**签发早于此刻的令牌都失效，包括正在
    发起这次请求的这一枚；不重签的话用户改完密码自己就被踢回登录页，
    会以为"改密码把号弄坏了"，然后拿旧密码反复试。
    """
    try:
        # 改密码要跑一次 PBKDF2（本身就很慢），限流只是兜底。
        # 定 10/分 而不是更紧：每次 PBKDF2 约 80ms，10 次/分对服务器没有压力，
        # 而更紧的桶会误伤真实用户 —— 他可能先打错旧密码两次、新密码不合规一次，
        # 第四次才成功，然后发现自己"操作过于频繁"等了 60 秒。
        await asyncio.to_thread(quota.hit, f"password:{uid}", 10)
    except quota.RateLimited as e:
        _reject(str(e), 429)

    err = await asyncio.to_thread(
        account.change_password, uid, body.old_password, body.new_password
    )
    if err:
        # 已登录状态下不涉及"账号是否存在"的枚举，直接给具体原因更有用
        _reject(err)

    row = await asyncio.to_thread(account.get_user, uid)
    if not row:
        raise HTTPException(status_code=401, detail="账号不存在，请重新登录")
    # 重签时带上**刚写入的新代数**：af == 代数，因此当前这台不会被自己踢掉。
    token = create_token(
        uid,
        username=row["username"],
        auth_version=int(row.get("token_valid_after") or 0),
    )
    set_session_cookie(response, token, request)
    # 浏览器靠刚种下的 Cookie；Bearer/App 客户端拿不到 Cookie，必须能从响应里
    # 换到新令牌，否则改完密码旧令牌作废、又没新令牌，等于被迫退出账号。
    return await _me_payload(row, token=token if config.AUTH_RETURN_TOKEN else None)


@router.post("/username", response_model=MeOut)
async def change_username(
    body: UsernameChange,
    request: Request,
    response: Response,
    uid: str = Depends(get_current_user),
):
    """改登录名：验密码 → 规则校验 → 查重 → 限次 → 落库 → 重签 Cookie。

    为什么要密码、为什么限 3 次、改完忘了怎么办 —— 见
    docs/账号自助_头像姓名密码_方案_2026-11-03.md §5.2。
    """
    try:
        # 同 password：10/分 只拦脚本，不拦人
        await asyncio.to_thread(quota.hit, f"username:{uid}", 10)
    except quota.RateLimited as e:
        _reject(str(e), 429)

    row, err, status = await asyncio.to_thread(
        account.change_username, uid, body.password, body.new_username
    )
    if err or not row:
        _reject(err or "改名失败", status)

    # JWT 里的 username 只是展示冗余，重发即同步；uid 不变，所以不会掉线。
    # 不重发的话，侧栏会一直显示旧的登录名直到下次登录——用户会以为没改成。
    token = create_token(
        uid,
        username=row["username"],
        auth_version=int(row.get("token_valid_after") or 0),
    )
    set_session_cookie(response, token, request)
    return await _me_payload(row, token=token if config.AUTH_RETURN_TOKEN else None)
