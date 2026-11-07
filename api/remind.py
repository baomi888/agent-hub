"""提醒时刻的 HTTP 端点。

  GET  /api/remind   取「我几点要被提醒」
  POST /api/remind   改它（同时影响 Web Push 与日历里那条事件）

## 为什么单独一组端点，不挂在 /api/calendar 下

这个时刻管的是**两个通道**：worker 到点发的 Web Push，和日历里那条 RRULE 事件的
起点。把它塞进日历那组，会让「只用推送、没订阅日历」的人找不到入口；
反过来日历那组也会变成「改了链接顺带改了推送时间」这种说不清的地方。

## 一个真话：改了时间，已经导入的日历不会立刻变

- **订阅（复制 URL 加进日历）**：服务端下次被拉取时返回新的 DTSTART，
  但客户端刷新周期不可控（Google 8–24 小时、国产日历更随意），可能明天才生效。
- **下载 .ics 导入**：那是一次性快照，**永远不会变**，得删掉重新导入。

这两句必须让用户在设置页看到（前端文案里写了），否则「我改了 7 点怎么还 20 点响」
会被当成 bug 报回来。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from core import config, quota, reminder_store
from core.auth import get_current_user

router = APIRouter(dependencies=[Depends(get_current_user)])

# 一天改 30 次已经远超正常使用（正常 1–3 次），只挡脚本刷库
_MAX_SET_PER_DAY = 30


def _payload(uid: str) -> dict:
    hour, minute = reminder_store.get(uid)
    dhour, dminute = reminder_store.default_time()
    return {
        "hour": hour,
        "minute": minute,
        "time": f"{hour:02d}:{minute:02d}",
        # 默认值单独给：前端要区分「你没设过，这是全站默认」和「你设成了这个点」
        "default_hour": dhour,
        "default_minute": dminute,
        # 客户端不要自己写死档位：调度是 */15，改了这里就得同步改 cron
        "steps": list(reminder_store.MINUTE_STEPS),
        # 推送通道是否可用（没配 VAPID 时前端文案要退化成"只影响日历"）
        "push_enabled": config.push_ready(),
    }


def _as_int(raw: object, field: str) -> int:
    """只接受能转成整数的值。给浮点/字符串数字放行，因为前端 select 传的是字符串。"""
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{field} 必须是整数") from None


@router.get("/remind")
async def get_remind(uid: str = Depends(get_current_user)) -> dict:
    return await asyncio.to_thread(_payload, uid)


@router.post("/remind")
async def set_remind(body: dict, uid: str = Depends(get_current_user)) -> dict:
    """设置提醒时刻。越界的值就近夹取（不报错），返回实际生效的值。

    为什么不直接 400：前端是下拉选择，正常不会越界；真越界了大概率是
    老版本前端或手搓请求，让用户看到「设置失败」不如让它落到一个合法值上。
    """
    hour = _as_int(body.get("hour"), "hour")
    minute = _as_int(body.get("minute"), "minute")

    await asyncio.to_thread(
        quota.consume_key, f"remind:{uid}", "remind_set", _MAX_SET_PER_DAY
    )
    await asyncio.to_thread(reminder_store.set_time, uid, hour, minute)

    # ★ 这里**不补发、也不清 push_log**：今天已经提醒过的话，改时间不该让人今晚
    #   被通知两次；今天还没提醒过的话，调度下一次 tick 自然会按新时间判断。
    return await asyncio.to_thread(_payload, uid)
