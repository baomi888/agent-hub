"""日历订阅的 HTTP 端点。

  GET  /api/calendar/link                 取自己的订阅链接（登录）
  POST /api/calendar/link/reset           换一个新链接，旧的立刻失效（登录）
  GET  /api/calendar/{token}.ics          日历本体（**公开**，token 即凭证）

为什么这一个 router 不整体挂 `Depends(get_current_user)`：`.ics` 那个端点必须匿名可访问
——安卓/iOS 的日历客户端来拉文件时**不会带 Cookie，也不会走登录流程**，它只有一个 URL。
所以鉴权下沉到 token，两个管理端点各自单独挂依赖。

token 的 128 bit 空间不可枚举，因此不需要防爆破；但每次拉取会写一次 `last_fetch_at`，
所以给每个 token 加了个很宽松的日额度（100 次），只用来挡住"被当成免费写库接口刷"，
正常日历一天来 2–4 次，碰不到。

`Cache-Control: no-store`：这个 URL 本身是凭证，不该被中间缓存留档。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from core import calendar_store, quota, reminder_store
from core.auth import get_current_user
from service import calendar as cal

router = APIRouter()

# 每个 token 每天最多被拉这么多次。正常日历 2–4 次/天，100 是"绝不误伤"的量级。
_MAX_FETCH_PER_DAY = 100

_ICS_TYPE = "text/calendar; charset=utf-8"


def _public_base(request: Request) -> str:
    """拼对外可用的绝对地址。

    ★ 不能直接用 `request.base_url`：线上 nginx 把请求转给 uvicorn 之后，
       后端看到的是 `http://127.0.0.1:8000/`，拼出来的链接用户打不开。
       真实域名要靠 nginx 传的 `X-Forwarded-*`——所以这里优先读它们。
    """
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    host = host.split(",")[0].strip()
    if not host:
        return str(request.base_url).rstrip("/")
    return f"{proto.split(',')[0].strip()}://{host}"


def _payload(request: Request, token: str, uid: str) -> dict:
    """两个端点返回同一个结构，抽出来避免两处各改一半。

    `time` 用的是**这个人自己设的**时刻（core/reminder_store.py），
    不是全站默认值——设置页那一行显示的就是它，和 ICS 里的 DTSTART 必须同源。
    """
    hour, minute = reminder_store.get(uid)
    info = cal.describe(token, hour, minute)
    base = _public_base(request)
    sub = base + info["subscribe"]
    # webcal:// 在 iOS/macOS 上点一下就能直接订阅；安卓多数日历不认这个 scheme，
    # 所以同时给 https 的下载地址（两份都用得上）。
    # ★ 按 "://" 切而不是 replace("https://", ...)：本地开发是 http，
    #   字面替换会原样返回一个 http 链接，点下去是个下载而不是订阅。
    _, _, rest = sub.partition("://")
    return {
        "token": token,
        "time": info["time"],
        "subscribe_url": sub,
        "webcal_url": f"webcal://{rest}" if rest else sub,
        "download_url": base + info["download"],
        "hour": hour,
        "minute": minute,
    }


@router.get("/link", dependencies=[Depends(get_current_user)])
async def get_link(request: Request, uid: str = Depends(get_current_user)) -> dict:
    """取（或第一次生成）这个人自己的订阅链接。"""
    token = await asyncio.to_thread(calendar_store.get_or_create, uid)
    return _payload(request, token, uid)


@router.post("/link/reset", dependencies=[Depends(get_current_user)])
async def reset_link(request: Request, uid: str = Depends(get_current_user)) -> dict:
    """换一个新链接。用于「链接不小心发出去了」——旧 URL 从这一刻起 404。"""
    token = await asyncio.to_thread(calendar_store.reset, uid)
    return _payload(request, token, uid)


@router.get("/{token}.ics")
async def get_ics(token: str, request: Request, dl: int = 0) -> Response:
    """生成日历本体。

    `?dl=1` 改成附件下载（"导入"这条老路）；不带则 inline（"订阅"）。
    不少客户端靠这个头决定是弹保存框还是直接加进日历。
    """
    uid = calendar_store.owner_of(token)
    if not uid:
        # 不给"这个 token 曾经存在过"的信息，一律 404
        raise HTTPException(status_code=404, detail="日历不存在或链接已失效")

    # 宽松日额度：只挡刷库，不挡正常拉取
    try:
        await asyncio.to_thread(quota.consume_key, token, "cal_fetch", _MAX_FETCH_PER_DAY)
    except quota.QuotaExceeded as e:
        raise HTTPException(status_code=429, detail="拉取过于频繁") from e

    hour, minute = await asyncio.to_thread(reminder_store.get, uid)
    body = cal.build_ics(token, hour, minute)
    await asyncio.to_thread(calendar_store.touch_fetch, token)

    disposition = "attachment" if dl else "inline"
    return Response(
        # 显式给 bytes：给 str 时 Starlette 的编码行为依赖 content-type 里有没有 charset，
        # 不够直观，而这里必须确保是 UTF-8 无 BOM。
        content=body.encode("utf-8"),
        media_type=_ICS_TYPE,
        headers={
            "Cache-Control": "no-store",
            "X-Robots-Tag": "noindex",
            "Content-Disposition": f'{disposition}; filename="zhisui-daily.ics"',
        },
    )
