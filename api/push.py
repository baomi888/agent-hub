"""Web Push 的 HTTP 端点。

  GET  /api/push/status      支持性 / 账号下有哪些设备 / 服务端公钥（前端 subscribe 要用）
  POST /api/push/subscribe   登记一台设备的订阅（同 endpoint 换账号会改 owner，不新增行）
  POST /api/push/unsubscribe 退订**本机**（浏览器已撤销订阅，这里清服务端那行）
  POST /api/push/remove      从账号里移除**某一台**设备（设置页的设备列表用）
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
    """前端据此决定：能不能订阅、要不要显示"已开启"、拿哪个公钥去 subscribe。

    ★★ `subscribed` / `devices` 是**账号级**的（任意一台设备订阅了就算 true）。
    设置页绝不能拿它渲染成"这台设备已开启"——电脑上从没开过、只因手机开了，
    界面就会显示"已开启"并给出"发一条测试/关掉"，两个按钮都不做本地的事：
    测试会把通知发到手机（让人以为发错了设备），关掉则因为本机没有订阅而
    什么都不删。所以另给 `device_list`，让前端用 `ep_key` 认出**本机**那一行，
    把"账号有几台"和"这台开没开"分成两个状态。
    """
    ready = config.push_ready()
    count = 0
    device_list: list[dict] = []
    if ready:
        rows = await asyncio.to_thread(push_store.list_for_owner, uid)
        device_list = [
            {
                "id": r["id"],
                "ep_key": r["ep_key"],
                "platform": r["platform"],
                "ua": r["ua"][:200],
                "created_at": r["created_at"],
                "last_ok_at": r["last_ok_at"],
                "fail_count": r["fail_count"],
                "disabled": r["disabled"],
            }
            for r in rows
        ]
        count = sum(1 for r in rows if not r["disabled"])
    hour, minute = await asyncio.to_thread(reminder_store.get, uid)
    return {
        # supported = 服务端具备发送能力（开关 + 密钥）。浏览器侧能力由前端自己探。
        "supported": ready,
        "subscribed": count > 0,
        "devices": count,
        "device_list": device_list,
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


@router.post("/remove")
async def push_remove(body: dict, uid: str = Depends(get_current_user)) -> dict:
    """移除某台设备的订阅（按 sub_id）。

    和 `/unsubscribe` 的分工：`/unsubscribe` 是按 endpoint 退**本机**（浏览器自己
    撤销订阅后顺手清账）；这里是设置页的设备列表里移除**别的设备**——那台设备
    的浏览器订阅撤不掉（我们碰不到它），所以服务端删行就够：endpoint 不再被投放，
    设备端的悬空订阅下次清理时会被推送服务方判 410。

    归属校验在 SQL 里（`owner_id=? AND sub_id=?`），猜中别人的 sub_id 也删不掉。
    """
    sub_id = str(body.get("id") or "").strip()
    if not sub_id or len(sub_id) > 64:
        raise HTTPException(status_code=400, detail="id 缺失或过长")
    removed = await asyncio.to_thread(push_store.remove_by_id, uid, sub_id)
    return {
        "ok": removed,
        "removed": removed,
        "devices": await asyncio.to_thread(push_store.count_active, uid),
    }


# 每天能自测几次。5 次太低——调试三端时几下就用完，然后看到"发送失败"
# 还以为是功能坏了（实际是 429，用户完全看不出来）。10 次够把三端各验两轮，
# 又不至于被当成免费闹钟（每次都会真弹一条通知）。
_TEST_PER_DAY = 10


@router.post("/test")
async def push_test(uid: str = Depends(get_current_user)) -> dict:
    """给自己发一条。**只发给自己**，所以没有"滥发"风险；仍限每天若干次，
    免得把推送服务方的配额和用户的耐心一起耗光（每次都会真弹一个通知）。"""
    if not config.push_ready():
        raise HTTPException(status_code=503, detail="服务端未开启推送")
    from core import reminder_store  # 只在这个端点用到，放在函数里少一层 import 噪音
    from service import push as push_svc  # 延迟导入：service 层依赖 core，这里避免循环

    try:
        await asyncio.to_thread(quota.consume_key, f"pushtest:{uid}", "pushtest", _TEST_PER_DAY)
    except quota.QuotaExceeded as e:
        # 不让它走全局处理器：那边会拼出「pushtest已达上限（10），明天再试或
        # 联系管理员调整配额」——英文 kind 泄漏给用户，而且"联系管理员"对一个
        # 自测按钮纯属废话。这里直说，并强调**真提醒不受影响**——否则用户会
        # 以为整个提醒功能坏了。
        raise HTTPException(
            status_code=429,
            detail=f"今天的测试次数用完了（每天 {_TEST_PER_DAY} 次）。真提醒照常发，明天就能再测。",
        ) from e
    hour, minute = await asyncio.to_thread(reminder_store.get, uid)
    payload = {
        "title": "知穗的提醒长这样",
        # 时刻必须跟着他自己设的值走：写死 20:00 会让改成 7:30 的人以为没生效
        "body": f"以后每天 {hour:02d}:{minute:02d} 只有在你还有事情没做完时才发一条",
        "url": "/",
        "tag": "test",
        # ★ force：SW 平时会在"用户正盯着页面"时把通知压掉（只递给页面）。
        #   但测试按钮的全部意义就是证明"这台设备收得到"——压掉就等于什么都没
        #   发生，用户会以为电脑没订阅。见 frontend/public/sw.js 的 push 处理。
        "force": True,
    }
    # ★ 用 send_detailed 而不是 send_to_user：多台设备时，光有 sent 计数答不了
    #   「我电脑为什么没弹」。明细让前端能说清"已投递到 2 台"，把排查方向从
    #   服务端挪到系统通知设置上。
    res = await asyncio.to_thread(push_svc.send_detailed, uid, payload)
    return {"ok": res.get("sent", 0) > 0, **res}
