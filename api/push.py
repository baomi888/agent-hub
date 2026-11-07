"""Web Push 的 HTTP 端点。

  GET  /api/push/status      支持性 / 是否已订阅 / 服务端公钥（前端 subscribe 要用）
  POST /api/push/subscribe   登记一台设备的订阅（同 endpoint 换账号会改 owner，不新增行）
  POST /api/push/unsubscribe 退订本设备
  POST /api/push/test        给**自己**发一条测试通知（前端"验证一下能不能收到"用）

为什么公钥要从接口下发而不是写进前端常量：换密钥时不想重新发版；
而且前端 `NEXT_PUBLIC_*` 是构建期内联的，本地与服务端用的若是同一份构建产物，
写死公钥会让本地调试的订阅在线上签名失败（报 403 VapidPkHashMismatch，很难看出原因）。

★★ 安全要点：`endpoint` 是**客户端给的 URL，服务端会向它发 POST**。
不校验就是一个 SSRF（内网探测、打内网服务）。这里的要求是：
  · 必须 https；
  · 不允许 IP 字面量（正规推送服务全用域名，而绕过 DNS 直指内网正是靠 IP）；
  · 不允许 localhost / *.local / *.internal。
不解域名、不做白名单，是因为白名单会把以后的国产推送服务一起挡掉；
拒绝 IP 字面量已经堵掉绝大多数 SSRF 利用路径（剩下的要靠 DNS 解析到内网，
那需要域名本身被攻击者控制且解析到私网——对一个只能发加密空载荷的请求，
代价高于收益）。
"""

from __future__ import annotations

import asyncio
import ipaddress
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException

from core import config, push_store, quota, reminder_store
from core.auth import get_current_user

router = APIRouter(dependencies=[Depends(get_current_user)])

# 端点的合理长度上限。真实的 FCM endpoint 约 150–300 字符，
# 留到 1024 是给带查询串的服务方余量，再长就是异常输入。
_MAX_ENDPOINT = 1024
# 浏览器给的密钥长度：p256dh 是 65 字节的 base64url（87 字符），auth 是 16 字节（22 字符）。
# 这里只做宽松区间判断——不同浏览器有无填充，长度会差 2 个字符。
_MAX_KEY = 200


def _validate_endpoint(raw: object) -> str:
    url = str(raw or "").strip()
    if not url or len(url) > _MAX_ENDPOINT:
        raise HTTPException(status_code=400, detail="endpoint 缺失或过长")
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise HTTPException(status_code=400, detail="endpoint 必须是 https")
    host = (parts.hostname or "").lower()
    if not host:
        raise HTTPException(status_code=400, detail="endpoint 缺少主机名")
    if host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
        raise HTTPException(status_code=400, detail="endpoint 主机名不被接受")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return url  # 不是 IP 字面量 → 放行
    raise HTTPException(status_code=400, detail="endpoint 不允许直接使用 IP")


def _validate_keys(body: dict) -> tuple[str, str]:
    keys = body.get("keys") or {}
    p256dh = str(keys.get("p256dh") or "").strip()
    auth = str(keys.get("auth") or "").strip()
    if not p256dh or not auth:
        raise HTTPException(status_code=400, detail="缺少 p256dh / auth 密钥")
    if len(p256dh) > _MAX_KEY or len(auth) > _MAX_KEY:
        raise HTTPException(status_code=400, detail="密钥过长")
    return p256dh, auth


@router.get("/status")
async def push_status(uid: str = Depends(get_current_user)) -> dict:
    """前端据此决定：能不能订阅、要不要显示"已开启"、拿哪个公钥去 subscribe。"""
    ready = config.push_ready()
    count = 0
    if ready:
        count = await asyncio.to_thread(push_store.count_active, uid)
    hour, minute = await asyncio.to_thread(reminder_store.get, uid)
    return {
        # supported = 服务端具备发送能力（开关 + 密钥）。浏览器侧能力由前端自己探。
        "supported": ready,
        "subscribed": count > 0,
        "devices": count,
        # 没配密钥时返回空串，前端据此不弹授权框
        "key": config.VAPID_PUBLIC_KEY if ready else "",
        # 提醒时刻带上：首页那条提示和设置页都要显示"每天几点"，
        # 让它们各自再请求一次 /api/remind 不值得，这里顺手给出。
        "time": f"{hour:02d}:{minute:02d}",
        "hour": hour,
        "minute": minute,
    }


@router.post("/subscribe")
async def push_subscribe(body: dict, uid: str = Depends(get_current_user)) -> dict:
    if not config.push_ready():
        raise HTTPException(status_code=503, detail="服务端未开启推送")
    endpoint = _validate_endpoint(body.get("endpoint"))
    p256dh, auth = _validate_keys(body)
    res = await asyncio.to_thread(
        push_store.upsert,
        uid,
        endpoint,
        p256dh,
        auth,
        str(body.get("ua") or "")[:300],
        str(body.get("platform") or "")[:20],
    )
    return {"ok": True, **res, "devices": await asyncio.to_thread(push_store.count_active, uid)}


@router.post("/unsubscribe")
async def push_unsubscribe(body: dict, uid: str = Depends(get_current_user)) -> dict:
    endpoint = str(body.get("endpoint") or "").strip()
    if not endpoint:
        raise HTTPException(status_code=400, detail="endpoint 缺失")
    removed = await asyncio.to_thread(push_store.remove, uid, endpoint)
    return {"ok": True, "removed": removed, "devices": await asyncio.to_thread(push_store.count_active, uid)}


@router.post("/test")
async def push_test(uid: str = Depends(get_current_user)) -> dict:
    """给自己发一条。**只发给自己**，所以没有"滥发"风险；仍限每天 5 次，
    免得把推送服务方的配额和用户的耐心一起耗光（每次都会真弹一个通知）。"""
    if not config.push_ready():
        raise HTTPException(status_code=503, detail="服务端未开启推送")
    from core import reminder_store  # 只在这个端点用到，放在函数里少一层 import 噪音
    from service import push as push_svc  # 延迟导入：service 层依赖 core，这里避免循环

    await asyncio.to_thread(quota.consume_key, f"pushtest:{uid}", "pushtest", 5)
    hour, minute = await asyncio.to_thread(reminder_store.get, uid)
    payload = {
        "title": "知穗的提醒长这样",
        # 时刻必须跟着他自己设的值走：写死 20:00 会让改成 7:30 的人以为没生效
        "body": f"以后每天 {hour:02d}:{minute:02d} 只有在你还有事情没做完时才发一条",
        "url": "/",
        "tag": "test",
    }
    res = await asyncio.to_thread(push_svc.send_to_user, uid, payload)
    return {"ok": res.get("sent", 0) > 0, **res}
