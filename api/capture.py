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
import uuid

import websockets
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from core import asr, asr_stream, auth, capture_parse, config, llm_gate, quota, timeutil
from core.auth import get_current_user

router = APIRouter(dependencies=[Depends(get_current_user)])

# WebSocket 必须挂在**不带全局依赖**的 router 上。
# `get_current_user` 的参数类型是 `Request`，放进 WebSocket 路由里 FastAPI
# 会把它当成别的东西注入（拿不到 cookie），鉴权就形同虚设并且报 500。
# 所以这个端点的鉴权是在函数体里手动做的 —— 见 `_ws_uid`。
ws_router = APIRouter()

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
    """前端要不要渲染麦克风、走不走流式，都由这里说了算。

    没有密钥时**不渲染**，而不是渲染了再弹"语音还没接"—— 后者是半成品感
    最典型的来源：按钮摆在那儿，点下去告诉你这功能没做。

    `stream` 是另一回事：它只决定**边说边出字**走哪条路，不开也照样能说话。
    所以前端拿到 `voice=true, stream=false` 时仍然要显示麦克风，只是内部
    退回一句话识别 —— 把流式当成语音功能的开关会让"没填 APPID"看起来
    像"语音坏了"。
    """

    voice: bool
    stream: bool = False


@router.get("/capabilities")
async def get_capabilities() -> dict:
    return {"voice": config.ASR_ENABLED, "stream": asr_stream.enabled()}


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


# ===========================================================================
# 实时语音识别（WebSocket）
#
# 前端 ──WSS──> 我们（nginx → FastAPI） ──WSS──> 腾讯云
#
# 帧协议（这是本文件和前端之间唯一的约定，不暴露腾讯云的 slice_type）：
#   上游 → 前端  {"type":"ready"}                          握手成功，可以开始说话
#                {"type":"partial","text":"明天三点"}        正在说的这句（会变，整句替换）
#                {"type":"segment","text":"明天三点交报告。"} 一句定型（追加）
#                {"type":"done","text":"<全文>"}              全部结束，拿这个去整理
#                {"type":"error","message":"...","retryable":bool}
#   前端 → 上游  二进制帧 = 裸 PCM（16k / 单声道 / 16bit）
#                {"type":"end"} 文本帧 = 说完了
#
# 为什么音频收裸 PCM 而不是 WAV：WAV 头只对"整段文件"有意义，流式下每包都是
# 纯采样数据，塞头反而要上游用 voice_format=12 去解析容器，多一层出错可能。
# ===========================================================================


def _ws_uid(websocket: WebSocket) -> str:
    """WebSocket 上的登录态。

    复用 `auth.current_user_id` 而不是重写一遍 cookie → token → uid 的链路：
    那条链路里有"令牌是否已吊销"这类容易漏的分支，复制一份必然漂移。
    它要的只是 `request.cookies`，而 Starlette 的 WebSocket 也有这个属性，
    鸭子类型能直接过 —— 类型标注对不上，但行为是对的。
    """
    try:
        return auth.current_user_id(websocket)  # type: ignore[arg-type]
    except HTTPException:
        return ""
    except Exception:  # noqa: BLE001 - 令牌格式坏了也是"没登录"，不该 500
        return ""


async def _send(websocket: WebSocket, payload: dict) -> bool:
    """发一帧；对端已经走了就返回 False，不抛。

    这个函数存在的唯一理由：整个转发循环里断连是**常态**（用户点取消、
    切后台、网络掉了），每一次都 try/except 会让主逻辑读不出层次。
    """
    try:
        await websocket.send_json(payload)
        return True
    except Exception:  # noqa: BLE001
        return False


async def _relay(client: WebSocket) -> None:
    """一条会话的完整生命周期：连上游 → 双向转发 → 收尾。"""
    segmenter = asr_stream.Segmenter()

    try:
        upstream = await websockets.connect(
            asr_stream.signed_url(uuid.uuid4().hex),
            # 不放心跳：会话最长 60 秒且音频一直在发，连接本来就是活跃的。
            # 反过来开着 ping 有风险 —— 如果中间设备吞掉 pong，20 秒后
            # websockets 会主动断开，症状是"说到一半突然结束"。
            ping_interval=None,
            open_timeout=10,
            close_timeout=3,
            max_size=2 * 1024 * 1024,
        )
    except asyncio.TimeoutError:
        await _send(client, {"type": "error", "message": "语音服务没响应，先打字吧", "retryable": True})
        await client.close(code=1011)
        return
    except Exception:  # noqa: BLE001
        await _send(client, {"type": "error", "message": "语音服务连不上，先打字吧", "retryable": True})
        await client.close(code=1011)
        return

    try:
        # 握手回包：code=0 才算连上。这里必须**先读掉第一帧**，
        # 它是唯一能区分"签名/APPID 配错"和"网络不通"的地方。
        try:
            raw = await asyncio.wait_for(upstream.recv(), timeout=10)
        except (asyncio.TimeoutError, websockets.ConnectionClosed):
            await _send(client, {"type": "error", "message": "语音服务没响应，先打字吧", "retryable": True})
            await client.close(code=1011)
            return

        hello = asr_stream.decode(raw if isinstance(raw, str) else raw.decode("utf-8", "ignore"))
        code = int((hello or {}).get("code") or 0)
        if hello is None or code:
            # 4001/4002/4003 都是"配置问题"，重试没有意义 —— 前端据此不要显示"再说一遍"
            message, retryable = asr_stream.explain(code)
            await _send(client, {"type": "error", "message": message, "retryable": retryable, "code": code})
            await client.close(code=1011)
            return

        if not await _send(client, {"type": "ready"}):
            return

        async def uplink() -> None:
            """前端 → 上游。收到 end 就转发 end 并返回。"""
            try:
                while True:
                    frame = await client.receive()
                    if frame.get("type") == "websocket.disconnect":
                        return
                    audio = frame.get("bytes")
                    if audio:
                        await upstream.send(audio)
                        continue
                    text = frame.get("text") or ""
                    if "end" in text:
                        # 不解析 JSON 判等：前端只会发这一种文本帧，
                        # 而解析失败会把"用户点了完成"变成"什么都没发生"。
                        await upstream.send(asr_stream.END_FRAME)
                        return
            except Exception:  # noqa: BLE001 - 断连 / 上游关闭都从这里退出
                return

        async def downlink() -> None:
            """上游 → 前端。收到 final / 出错就返回。"""
            try:
                while True:
                    raw = await upstream.recv()
                    msg = asr_stream.decode(raw if isinstance(raw, str) else raw.decode("utf-8", "ignore"))
                    if msg is None:
                        continue  # 坏包丢掉，不让一条脏帧杀掉整次会话
                    frame = segmenter.feed(msg)
                    if frame is None:
                        continue
                    if not await _send(client, frame):
                        return
                    if frame["type"] in ("done", "error"):
                        return
            except websockets.ConnectionClosed:
                return
            except Exception:  # noqa: BLE001
                return

        up_task = asyncio.create_task(uplink())
        down_task = asyncio.create_task(downlink())
        tasks = [up_task, down_task]
        try:
            done, _ = await asyncio.wait(
                tasks,
                timeout=config.ASR_STREAM_MAX_SECONDS + 15,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if down_task not in done:
                # 用户先说完了（uplink 已返回），上游还要把最后一句吐完，最多再等 6 秒。
                # 立刻取消的话，最后半句会**只在屏幕上出现过、没进最终文本** ——
                # 用户看到的是"我明明说了但没整理进去"，是这一版最容易出的错。
                await asyncio.wait({down_task}, timeout=6)
        finally:
            for task in tasks:
                task.cancel()
            # 收掉取消，避免 "Task was destroyed but it is pending" 刷日志
            await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        try:
            await upstream.close()
        except Exception:  # noqa: BLE001
            pass


@ws_router.websocket("/ws")
async def ws_transcribe(websocket: WebSocket) -> None:
    """语音流式转写。

    顺序是**先鉴权、再连上游**：反过来的话，未登录的请求也会消耗一次
    到腾讯云的长连接（并发是有配额的），等于给了一个免费的放大入口。
    """
    await websocket.accept()

    if not asr_stream.enabled():
        # 没开流式不是错误。前端正常情况下不会连到这里（capabilities 已经说了），
        # 落到这里说明是版本错配 —— 给一句能指导下一步的话，不要吐配置细节。
        await _send(websocket, {"type": "error", "message": "语音服务没配好，先打字吧", "retryable": False})
        await websocket.close(code=1011)
        return

    uid = _ws_uid(websocket)
    if not uid:
        await _send(websocket, {"type": "error", "message": "登录已过期，重新登录再试", "retryable": False})
        await websocket.close(code=4401)
        return

    # 和一句话转写共用一个桶：两条路径都是"用户按了一次麦克风"，
    # 分开计数只会让同一分钟内切着用的用户同时撞两个限流。
    try:
        await asyncio.to_thread(quota.hit, f"asr:{uid}", config.CAPTURE_PARSE_PER_MINUTE)
    except Exception as exc:  # noqa: BLE001 - RateLimited 或存储异常，都不能 500
        retry_after = getattr(exc, "retry_after", 0)
        await _send(
            websocket,
            {
                "type": "error",
                "message": (
                    f"现在用的人有点多，约 {retry_after} 秒后再试"
                    if retry_after
                    else "现在用的人有点多，等几秒再试"
                ),
                "retryable": True,
            },
        )
        await websocket.close(code=4029)
        return

    await _relay(websocket)
