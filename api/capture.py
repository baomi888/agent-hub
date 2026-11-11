"""一句话整理的 HTTP 端点（Phase B）。

  POST /api/capture/parse   一句话 → proposal 列表（**不写库**）

## 为什么单独开一个 router 而不是塞进 /api/tasks

`/api/tasks` 的语义是"增删改查已经存在的待办"。这个端点调模型、花 token、
失败率高得多（模型抽风、上游 429、JSON 坏了），失败模式完全不同；
混在一起以后想给"整理"单独限流或关掉就没地方下手。

## 三个状态码，前端各有一句话对应

- **200**：`{items, unresolved, model, latency_ms}`，可能 `items: []`（0 条也是成功）
- **429**：要么本用户这一分钟太频（`quota`），要么上游并发满了（`llm_gate.Busy`）。
  都带 `Retry-After`，前端据此说"约 N 秒后再试"，而不是"出错了"。
- **502**：模型没给出能用的 JSON。原文不丢，前端把话填回输入框让用户重说/重试。

400 只给"空内容"和"超过 2000 字"——这两种是请求本身不对，重试也没用。

## 不写库

这个文件里没有任何 `task_core` / `reminder_core` 的引用，这是刻意的：
规格要求 `/api/capture/parse` 只读 + 调模型，写库在 Phase C 由前端逐条调
`POST /api/tasks` / `POST /api/reminders` 完成。加一行 import 就容易加第二行。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from core import asr, capture_parse, config, llm_gate, quota, timeutil
from core.auth import get_current_user

router = APIRouter(dependencies=[Depends(get_current_user)])

# `now` 只用来让截图脚本/测试把"今天"钉死，正常请求不传。
# 传了也不全信：和服务器时间差超过 7 天就忽略（否则一条 now=0 的请求
# 能让所有相对时间落到 1970，用户看到一堆 1970 年的待办）。
_NOW_TRUST_WINDOW = 7 * 86400


class ParseIn(BaseModel):
    """请求体。`text` 上限和前端输入框一致（规格 §5.3）。"""

    text: str = Field(..., min_length=1, max_length=capture_parse.MAX_TEXT)
    now: int | None = Field(default=None, ge=0)


@router.post("/parse")
async def post_parse(body: ParseIn, uid: str = Depends(get_current_user)) -> dict:
    """把一句话整理成待办/提醒 proposal。不改任何表。"""
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="先说一句要整理的话")

    now = timeutil.now_ts()
    if body.now and abs(body.now - now) <= _NOW_TRUST_WINDOW:
        now = body.now

    # 先卡自己人的频率，再去挤上游的闸：
    # 反过来的话，被限流的请求已经在排队名额里占了一会儿，白占。
    await asyncio.to_thread(quota.hit, uid, config.CAPTURE_PARSE_PER_MINUTE)

    try:
        return await capture_parse.parse(text, now)
    except llm_gate.Busy as exc:
        # 上游并发满了。这不是"系统坏了"，是"现在人多"，所以 429 + Retry-After。
        #
        # 时长只写在 `retry_after` 字段里，**不写进 detail**：
        # 前端 `httpError` 会自己拼一句「（约 N 秒后可重试）」，
        # 两边都写就变成「约 15 秒后可重试（约 15 秒后可重试）」—— 真机截图里出现过。
        return JSONResponse(
            status_code=429,
            content={"detail": "现在用的人有点多，稍等一下再试", "retry_after": exc.retry_after},
            headers={"Retry-After": str(exc.retry_after)},
        )
    except capture_parse.ParseFailed as exc:
        raise HTTPException(
            status_code=502, detail=f"AI 没整理出来，请再说一次（{exc}）"
        ) from exc
    except Exception as exc:  # noqa: BLE001 - 上游超时/连接错误统一按 502
        # 不把上游原始报错抛给用户：里面常带 base_url 或 key 的前几位
        raise HTTPException(
            status_code=502, detail="AI 没整理出来，请再说一次"
        ) from exc


class Capabilities(BaseModel):
    """前端要不要渲染麦克风，由这里说了算。

    没有密钥时**不渲染**，而不是渲染了再弹"语音还没接"—— 后者是半成品感
    最典型的来源：按钮摆在那儿，点下去告诉你这功能没做。
    """

    voice: bool


@router.get("/capabilities")
async def get_capabilities() -> dict:
    return {"voice": config.ASR_ENABLED}


@router.post("/transcribe")
async def post_transcribe(
    file: UploadFile = File(...),  # noqa: B008 -- FastAPI 依赖注入惯例
    uid: str = Depends(get_current_user),
) -> dict:
    """录音 → 文字。**不写库、不整理**，只把声音换成字填回输入框。

    为什么和 parse 分开：转写失败（没听清）和整理失败（模型抽风）是两回事，
    出口也不一样 —— 前者重说一遍，后者可以直接把字改一改再提交。
    合成一个端点的话，前端分不清该提示哪句。

    格式只收 WAV：腾讯云一句话识别不认 webm，而 Chrome 的 MediaRecorder
    默认就输出 webm。前端用 Web Audio 采 PCM 再封 WAV，这里按 wav 传。
    """
    if not config.ASR_ENABLED:
        raise HTTPException(status_code=503, detail="语音服务没配好，先打字吧")

    # 多读 1 字节用来判断"有没有超"，比先读全部再判长省一次内存拷贝
    audio = await file.read(config.ASR_MAX_BYTES + 1)
    if not audio:
        raise HTTPException(status_code=400, detail="没录到声音，再说一遍")
    if len(audio) > config.ASR_MAX_BYTES:
        raise HTTPException(status_code=413, detail="这段话太长了，一次说一句")

    # 转写和整理共用一个每分钟额度：两件事都是被用户手动触发的，
    # 分开计数只会让"点了五次才想起来"的用户同时撞两个限流。
    await asyncio.to_thread(quota.hit, f"asr:{uid}", config.CAPTURE_PARSE_PER_MINUTE)

    try:
        text = await asr.transcribe(audio)
    except asr.AsrError as exc:
        # 429 = 等几秒有用（限流/超时/没听清）；502 = 等也没用（格式/配置）
        raise HTTPException(
            status_code=429 if exc.retryable else 502, detail=str(exc)
        ) from exc

    return {"text": text}
