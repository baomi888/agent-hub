"""腾讯云实时语音识别（流式）· 冒烟验证。

## 它回答的是哪三个问题

真流式出问题时只有一句 "连不上"，看不出卡在哪。这个脚本按握手顺序拆成三段，
每一段各自有独立的结论：

1. **签名对不对** —— 连上去第一帧是不是 `code=0`。
   签名错会回 4001，APPID 错回 4002/4003，都发生在**第一帧**，和音频无关。
2. **能不能送音频** —— 发 1.5 秒静音 PCM 期间连接有没有被上游关掉。
   4007（包过长）/4008（分片等待超时）在这一段暴露，属于前端发包节奏问题。
3. **收尾能不能拿到 done** —— 发 `{"type":"end"}` 后是否等到 `final=1`。
   拿不到就是结束帧没发对，用户会感觉"说完了界面一直转圈"。

## 为什么用静音而不是真话

要验的是**链路**，不是**字准**。静音的预期结果是"`code=0` 握手成功，然后识别
结果为空或直接 done" —— 零结果反而是通过的标志。真话识别的准确率得拿手机录，
那是另一件事（`tools/asr_smoke.py` 验的是另一条链路：一句话识别）。

## 三种退出码

    0  握手成功（有或没有字都算）
    2  上游拒了：签名 / APPID / 权限问题
    3  连不上或超时：网络、代理、域名被拦

用法：
    venv\\Scripts\\python.exe tools/asr_stream_probe.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import asr_stream, config  # noqa: E402

# 200ms 一包：官方建议 100-200ms。太密会被 4008 打断，太稀会有明显延迟感。
CHUNK_MS = 200
# 静音时长。1.5 秒足够让 VAD 走一遍，又不至于让脚本跑太久。
SILENCE_SECONDS = 1.5
# 等第一帧的上限。握手本身 <1 秒，留 8 秒是给跨境网络抖动。
READY_TIMEOUT = 8.0


def _mask(value: str) -> str:
    if not value:
        return "(空)"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:6]}…{value[-4:]}"


def silence_pcm(seconds: float = SILENCE_SECONDS, rate: int = 16000) -> bytes:
    """裸 PCM：16k / 单声道 / 16bit。全 0 就是静音。"""
    return b"\x00\x00" * int(rate * seconds)


def _print_config() -> None:
    key_state = f"已填({len(config.ASR_SECRET_KEY)} 字符)" if config.ASR_SECRET_KEY else "(空)"
    print("=" * 56)
    print(f"SecretId      : {_mask(config.ASR_SECRET_ID)}")
    print(f"SecretKey     : {key_state}")
    print(f"APPID         : {config.ASR_APPID or '(空) ← 流式必须填，缺了只能走一句话识别'}")
    print(f"Region        : {config.ASR_REGION}")
    print(f"引擎(流式)    : {config.ASR_STREAM_ENGINE}")
    print(f"VAD 静音判定  : {config.ASR_VAD_SILENCE_MS} ms")
    print(f"流式开关      : {config.ASR_STREAM_ENABLED}")
    print("=" * 56)


async def _probe() -> int:
    _print_config()

    if not config.ASR_STREAM_ENABLED:
        print("\n结论：没配齐。ASR_STREAM_ENABLED 为 False，前端会退回一句话识别。")
        if not config.ASR_APPID:
            print("      → 缺 TENCENT_ASR_APPID（10 位数字，不是 SecretId）。")
        return 2

    url = asr_stream.signed_url(uuid.uuid4().hex)
    # 只打印结构，不打印 signature —— 它 5 分钟内可以当入场券用
    head = url.split("&signature=")[0]
    print(f"\n握手地址：{head[:96]}…")

    started = time.monotonic()
    try:
        upstream = await websockets.connect(
            url, ping_interval=None, open_timeout=10, max_size=2**22
        )
    except Exception as exc:  # noqa: BLE001
        print(f"\n结论：连不上上游（{type(exc).__name__}: {exc}）")
        print("      排查顺序：本机能不能出网 → 443 是否被拦 → 域名是否被代理改写。")
        return 3

    async with upstream:
        # ---- 第 1 段：等 ready ----
        try:
            first = await asyncio.wait_for(upstream.recv(), timeout=READY_TIMEOUT)
        except asyncio.TimeoutError:
            print(f"\n结论：连上了但 {READY_TIMEOUT:.0f} 秒没回第一帧。多半是上游半开。")
            return 3

        print(f"第一帧（{int((time.monotonic() - started) * 1000)} ms）：{first}")
        msg = asr_stream.decode(first if isinstance(first, str) else first.decode())
        if msg is None:
            print("\n结论：第一帧不是 JSON，链路中间有东西在改写响应。")
            return 3

        code = int(msg.get("code") or 0)
        if code:
            # 4001 签名错 / 4002 鉴权失败 / 4003 APPID 无效，这三个都是配置问题
            hints = {
                4001: "签名不匹配。检查 SecretKey 是否复制完整（前后空格/换行最常见）。",
                4002: f"APPID 与实际访问的不一致。看签名里用的是 {config.ASR_APPID} 吗。",
                4003: "APPID 无效。确认这个 APPID 属于当前 SecretId 所在的主账号。",
                4004: "并发超限。同一 APPID 同时连得太多，等几秒再跑。",
            }
            print(f"\n结论：上游拒了，code={code}。")
            print(f"      → {hints.get(code, '见腾讯云实时语音识别错误码文档。')}")
            return 2

        print("✅ 握手成功（code=0）：签名、APPID、引擎名三处都对。")

        # ---- 第 2 段：送音频 ----
        pcm = silence_pcm()
        step = 16000 * 2 * CHUNK_MS // 1000  # 200ms 的字节数 = 6400
        frames = [pcm[i : i + step] for i in range(0, len(pcm), step)]
        print(f"\n发送 {len(pcm)} 字节静音 PCM，分 {len(frames)} 包（每包 {CHUNK_MS}ms）…")

        sent = 0
        try:
            for chunk in frames:
                await upstream.send(chunk)
                sent += 1
                await asyncio.sleep(CHUNK_MS / 1000)  # 按实时速率送，别灌满
        except Exception as exc:  # noqa: BLE001
            print(f"\n结论：送到第 {sent}/{len(frames)} 包时断了（{type(exc).__name__}）。")
            print("      发包太快会触发 4008，太慢会触发超时 —— 前端也必须是实时速率。")
            return 3

        # ---- 第 3 段：收尾 ----
        await upstream.send(asr_stream.END_FRAME)
        print(f"音频发完，已送 {asr_stream.END_FRAME}，等 final …")

        segmenter = asr_stream.Segmenter()
        events: list[dict] = []
        got_done = False
        deadline = time.monotonic() + 15
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            try:
                raw = await asyncio.wait_for(upstream.recv(), timeout=left)
            except asyncio.TimeoutError:
                break
            except websockets.ConnectionClosed:
                print("上游提前关连接（没等到 final）。")
                break

            data = asr_stream.decode(raw if isinstance(raw, str) else raw.decode())
            if data is None:
                continue
            event = segmenter.feed(data)
            if event:
                events.append(event)
                print(f"  → {json.dumps(event, ensure_ascii=False)}")
            if event and event["type"] in ("done", "error"):
                got_done = True
                break

        print("\n" + "-" * 56)
        if got_done:
            print(f"✅ 结束帧正常，全文：「{segmenter.full_text}」")
            print("   静音没出字是**预期行为**，链路已经整条通了。")
            print("   下一步可以拿手机真说一句验字准。")
            return 0

        if events:
            print("⚠️ 收到部分结果但没等到 done：结束帧可能没被上游接受。")
            return 3

        print("⚠️ 全程零结果。静音出零结果是正常的，但也没等到 done ——")
        print("   要么上游把空音频直接丢了，要么 end 帧格式不对。先看前面有没有 error 帧。")
        return 3


def main() -> int:
    try:
        return asyncio.run(_probe())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
