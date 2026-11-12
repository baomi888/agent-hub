"""上课提醒的 HTTP 端点。

  GET /api/course-reminder   当前设置 + 接下来几节课（含提醒时刻）
  PUT /api/course-reminder   开 / 关，改提前量

## 和 `/api/calendar/*` 的分工

两条路并存，不是新旧替换：

* **iOS**：装到主屏后走苹果 APNs，`/api/push` 发得出真通知，所以用这里的定时提醒
  （`service/course_reminder.py`）。弹窗能点开、能进课程表，这是用户要的。
* **安卓**：网页推送底层走 Google FCM，国内连不通。`courses.ics` 里的 VALARM
  由系统日历自己定时，是唯一不依赖推送通道的路。所以安卓继续用日历。

平台判断放在前端（`lib/pwa.ts::detectPlatform`）—— 服务端从 UA 猜手机型号
容易误伤（iPadOS 的 UA 写着 Macintosh），而「这台设备该看哪个按钮」本来
就是浏览器才知道的事。

## 为什么改设置要立刻同步一次

用户点完开关看到的第一句话通常是「下一次什么时候」。如果只写配置等下一轮 tick，
那一分钟内页面会显示「还没有下一次」，看起来像没生效。所以 PUT 里直接
`sync()`，把结果一起返回。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.auth import get_current_user
from service import course_reminder

router = APIRouter(dependencies=[Depends(get_current_user)])


class PrefsIn(BaseModel):
    enabled: bool | None = None
    lead_minutes: int | None = None


def _state(uid: str, *, sync_now: bool = False) -> dict[str, Any]:
    prefs = course_reminder.get_prefs(uid)
    rows = course_reminder.upcoming(uid, limit=3)
    # `synced` 只在真的写过库时才有值，别让它是 null 还是 0 混在一起 ——
    # 前端只用它判断「这次保存有没有真的重建出下一条」。
    synced = course_reminder.sync(uid) if (sync_now and prefs["enabled"]) else None
    return {
        "enabled": prefs["enabled"],
        "lead_minutes": prefs["lead_minutes"],
        "leads": list(course_reminder.LEADS),
        "default_lead": course_reminder.DEFAULT_LEAD,
        "next": rows,
        "synced": None if synced is None else int(synced.get("written") or 0),
    }


@router.get("")
async def get_state(uid: str = Depends(get_current_user)) -> dict[str, Any]:
    # `upcoming` 要读课表快照（会碰库和 JSON），放线程里，别堵事件循环
    return await asyncio.to_thread(_state, uid)


@router.put("")
async def put_state(body: PrefsIn, uid: str = Depends(get_current_user)) -> dict[str, Any]:
    course_reminder.set_prefs(uid, enabled=body.enabled, lead=body.lead_minutes)
    return await asyncio.to_thread(_state, uid, sync_now=True)
