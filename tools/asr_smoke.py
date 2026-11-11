"""腾讯云一句话识别 · 冒烟验证（不写库，只打一次真实请求）。

## 为什么用静音音频

这一步要回答的是「**签名算对了没有**」，不是「识别准不准」。
这两件事用静音就能分开：

- 签名/密钥有问题 → 服务端在鉴权阶段就拒，回 `AuthFailure.*`
- 签名正确       → 请求进到识别环节，因为没内容才回 `ErrorRecognize` 或空结果

所以「报了一个非鉴权错误」在这里是**好消息**，说明钥匙是对的。
真识别准不准，得用手机录一句真话测，那是另一回事。

用法：
    venv\\Scripts\\python.exe tools/asr_smoke.py
"""

from __future__ import annotations

import asyncio
import io
import struct
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import asr, config  # noqa: E402

# 鉴权类错误码：出现这些说明密钥或签名有问题，跟音频无关
_AUTH_PREFIXES = ("AuthFailure", "UnauthorizedOperation", "InvalidCredential")


def silent_wav(seconds: float = 1.0, rate: int = 16000) -> bytes:
    """1 秒静音，16k / 单声道 / 16bit —— 一句话识别的标准输入规格。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<h", 0) * int(rate * seconds))
    return buf.getvalue()


def _mask(value: str) -> str:
    """密钥只露头尾。截图/日志里贴出去也不至于报废。"""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:6]}…{value[-4:]}"


async def main() -> int:
    sid = config.ASR_SECRET_ID
    skey = config.ASR_SECRET_KEY

    print(f"SecretId  : {_mask(sid) if sid else '(空)'}")
    print(f"SecretKey : {'已填(' + str(len(skey)) + ' 字符)' if skey else '(空)'}")
    print(f"Region    : {config.ASR_REGION}")
    print(f"Engine    : {config.ASR_ENGINE}")
    print("-" * 52)

    if not config.ASR_ENABLED:
        print("结论：没配齐。TENCENT_ASR_SECRET_ID / SECRET_KEY 至少有一个是空的。")
        print("      .env 改完要**重启后端**才生效（config 在 import 时读一次）。")
        return 1

    audio = silent_wav()
    print(f"发送 1 秒静音 WAV，{len(audio)} 字节 …")

    try:
        text = await asr.transcribe(audio, "wav")
    except asr.AsrError as exc:
        code = exc.code or ""
        if code.startswith(_AUTH_PREFIXES):
            print(f"\n结论：鉴权没过（{code}）。")
            print(f"      用户看到的话会是：「{exc}」")
            if "SecretIdNotFound" in code:
                print("      → SecretId 不存在。多半是复制时少了/多了一位，或密钥已被删除。")
            elif "SignatureFailure" in code:
                print("      → 签名不匹配。Key 复制错了，或前后有空格/换行。")
            else:
                print("      → 子账号可能没被授予 QcloudASRFullAccess，或服务没开通。")
            return 2

        if code in ("EmptyResult", "FailedOperation.ErrorRecognize"):
            print(f"\n结论：**签名通过**。服务端认了这把钥匙，只是静音没内容可识别。")
            print("      到这步就可以用手机录一句真话测端到端了。")
            return 0

        print(f"\n结论：请求发出去了，但服务端回了别的错（{code}）。")
        print(f"      用户看到的话会是：「{exc}」")
        print(f"      retryable={exc.retryable}")
        return 3

    # 静音居然识别出字来了：说明服务端确实在工作，签名必然是对的
    print(f"\n结论：**签名通过**，且返回了文字：「{text}」")
    print("      （静音能出字通常是引擎把底噪幻觉成了字，不影响结论。）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
