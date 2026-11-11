"""实时语音识别（腾讯云 WebSocket 流式）。

## 它和 `core/asr.py` 的关系

`asr.py` 是**一句话识别**：说完 → 整段上传 → 一次性返回全文。它现在仍然在，
不是被替代，而是当**降级路径**：APPID 没配、上游连不上、浏览器不支持 WS，
任何一条挂了都自动退回它。用户感觉到的差别只是"字不是边说边出来"，功能不丢。

## 为什么签名和 `asr.py` 完全不一样

一句话识别走云 API 3.0，签名是 TC3-HMAC-SHA256 放在 HTTP header 里；
实时识别是独立网关，签名是 **HMAC-SHA1 → base64 → 塞进 URL query**。
两套算法没有复用可能，抄错的表现都是握手阶段 `code=4001 SignatureError`，
本地看不出是排序、是编码、还是 SecretKey 用错了。

## 签名原文的三个必须

官方文档给的原文形如：

    asr.cloud.tencent.com/asr/v2/125922***?engine_model_type=16k_zh&expired=...&nonce=...
    &secretid=***&timestamp=...&voice_format=1&voice_id=...

1. **不含 `wss://`** —— 带了必然 4001。
2. **参数按字典序**，`signature` 自己不算在内。
3. **值是未编码的原文**。最终 URL 才要 urlencode，而且是**必须**编码：
   base64 里的 `+` `/` `=` 不编码会偶发 4001（文档原话："否则将导致鉴权失败偶发"）。

## 为什么是后端代理而不是让浏览器直连

浏览器能直连（腾讯云官方 JS SDK 就这么干），但要把签名 URL 发给前端，等于把
**这次会话的入场券**交出去，且绕过我们的登录态和限流。更实际的问题是可靠性：
用户在校园网/热点下连 `asr.cloud.tencent.com` 的 WSS 我们无法干预，
而连自己的域名（nginx 已经通了）是可控的。多一跳的代价在广州→上海约 30-60ms，
对一个说完才整理的工具没有感知。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import random
import time
from urllib.parse import quote, urlencode

from core import asr, config

_HOST = "asr.cloud.tencent.com"
_PATH = "/asr/v2"  # 后面拼 /<appid>

# 一次会话的签名有效期。**不是**会话本身的上限，只是"这张票能进门"的时限。
# 设 300 秒是因为握手一定发生在建连那一刻；设长了只是让泄露的票能用更久。
_SIGN_TTL = 300


def enabled() -> bool:
    return config.ASR_STREAM_ENABLED


def _clean_hotwords() -> str:
    """给流式用的热词表。

    在 `asr.hotword_list()` 的基础上再剔掉含 `&` `=` 空格的词：
    那些字符会把签名原文的 `k=v&k=v` 结构拆坏，服务端解出来的参数个数
    和签名时不一致 —— 症状同样是 4001，但原因和排序、编码都不一样。
    内置表里没有这类词，这一层是防运营从环境变量补进来的脏词。
    """
    words = asr.hotword_list()
    if not words:
        return ""
    keep = [
        item
        for item in words.split(",")
        if item and not any(ch in item for ch in ("&", "=", " ", "#"))
    ]
    return ",".join(keep)


def build_params(voice_id: str) -> dict[str, str]:
    """握手 URL 的全部 query 参数（不含 signature）。

    `voice_format=1` 是 pcm：前端采的就是裸 PCM，不必再封 WAV 头。
    `needvad=1` 让服务端按静音切句 —— 这是"说完一句立刻出稳态结果"的来源，
    关掉的话要等整段结束才有一段很长的结果，就不叫流式了。
    `filter_punc` 保持 0（保留句末句号）：整理阶段靠这个句号判断"这是两件事"。
    """
    now = int(time.time())
    params: dict[str, str] = {
        "secretid": config.ASR_SECRET_ID,
        "timestamp": str(now),
        "expired": str(now + _SIGN_TTL),
        "nonce": str(random.randint(1000000, 9999999999)),
        "engine_model_type": config.ASR_STREAM_ENGINE,
        "voice_id": voice_id,
        "voice_format": "1",
        "needvad": "1",
        "vad_silence_time": str(config.ASR_VAD_SILENCE_MS),
        "convert_num_mode": "1",
        "filter_punc": "0",
    }
    if 0 < config.ASR_FILTER_MODAL <= 2:
        params["filter_modal"] = str(config.ASR_FILTER_MODAL)
    words = _clean_hotwords()
    if words:
        params["hotword_list"] = words
    return params


def signing_origin(params: dict[str, str], appid: str | None = None) -> str:
    """签名原文。**单独抽出来是为了能被断言**。

    如果把它藏在 `signature()` 里，测试只能验证"同一段代码算两次结果一样"，
    等于什么都没证明。抽出来之后至少能钉住三件真正会错的事：
    带了 `wss://`、参数没排序、signature 混进了原文。

    注意值是**未 URL 编码的原文** —— 编码发生在拼最终 URL 那一步。
    """
    appid = appid or config.ASR_APPID
    # 字典序：`sorted(dict)` 排的是 key，和文档"按字典序排序"一致
    query = "&".join(f"{key}={params[key]}" for key in sorted(params))
    return f"{_HOST}{_PATH}/{appid}?{query}"


def signature(params: dict[str, str], appid: str | None = None) -> str:
    """HMAC-SHA1 → base64。原文见 `signing_origin`。

    单独抽出来是因为它是最难自查的一步：错了只有 4001，没有更细的提示。

    **已验证（2026-11-11）**：拿真实 SecretId/SecretKey 配一个不存在的 APPID
    连了一次腾讯云，返回 `4002 AppID 与实际访问的 AppID 不一致` 而不是 4001 ——
    说明服务端已经把签名验过了，卡在 APPID 那一关。排序、未编码的值、
    HMAC-SHA1、base64、最终 URL 的 quote 五处**同时**正确才能走到 4002。
    """
    origin = signing_origin(params, appid)
    digest = hmac.new(
        config.ASR_SECRET_KEY.encode("utf-8"), origin.encode("utf-8"), hashlib.sha1
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def signed_url(voice_id: str, *, appid: str | None = None) -> str:
    """完整可连的 wss 地址。

    `quote(..., safe="")` 不能省：base64 的 `/` 会落进 URL path，
    服务端按 path 解析就少了一段 query（表现为参数个数不对 → 4001）。
    """
    params = build_params(voice_id)
    sig = signature(params, appid)
    # urlencode 处理中文热词；signature 单独 quote 因为 safe="" 的语义更明确
    query = urlencode(sorted(params.items())) + "&signature=" + quote(sig, safe="")
    return f"wss://{_HOST}{_PATH}/{appid or config.ASR_APPID}?{query}"


# ---------------------------------------------------------------------------
# 上游错误码 → 用户看得懂的一句话
#
# 实时接口的错误码和云 API 是两套编号体系（4001/4002/4003…），
# 不能拿 `asr._CODES` 那套 `AuthFailure.*` 去匹配，匹配不上会全部落到兜底。
# ---------------------------------------------------------------------------
_CODES = {
    4001: "语音服务没配好，先打字吧",  # 签名/参数错误
    4002: "语音服务没配好，先打字吧",  # 鉴权失败
    4003: "语音服务没配好，先打字吧",  # appid 无效
    4004: "现在用的人有点多，等几秒再试",  # 并发超限
    4005: "语音服务没响应，先打字吧",  # 后端识别超时
    4006: "没听清，再说一遍",  # 音频数据为空
    4007: "没听清，再说一遍",  # 音频包过长
    4008: "网络卡了一下，再说一遍",  # 分片等待超时（发得太快或卡住）
    4009: "说得太长了，一次说一句",
    4010: "语音服务没响应，先打字吧",
}


def explain(code: int) -> tuple[str, bool]:
    """返回 (人话, 能不能重试)。重试指的是"再说一遍可能就好了"。"""
    text = _CODES.get(code, "语音没转成，先打字吧")
    retryable = code in (4004, 4006, 4007, 4008, 4009, 4010)
    return text, retryable


class Segmenter:
    """把腾讯云的消息流归一化成前端要的三帧。

    ## 为什么要这一层

    腾讯云的 `slice_type` 是 0/1/2，含义是"句首/句子中/句尾"，但真正决定
    界面怎么画的是另外两件事：**这段还会不会变**、**是不是全部结束了**。
    把这套映射放在前端，等于让 UI 组件去理解 slice_type —— 下次换引擎就得改 UI。

    输出：
      {"type": "partial", "text": ...}    正在说的这句（可能变），整句替换
      {"type": "segment", "text": ...}    一句定型了，追加
      {"type": "done",    "text": ...}    全部结束，给完整全文

    ## 为什么 partial 要整句替换而不是增量拼

    `slice_type=1` 返回的是**这一句到目前为止的完整文本**，不是新增的字。
    当成增量去 append 会得到"我我我要要去去图书馆"这种重复堆叠。
    """

    def __init__(self) -> None:
        self._done: list[str] = []
        self._live = ""

    def feed(self, msg: dict) -> dict | None:
        code = int(msg.get("code") or 0)
        if code:
            text, retryable = explain(code)
            return {"type": "error", "message": text, "retryable": retryable, "code": code}

        # final=1 是"音频流全部识别结束"，它**不带 result**，只有一个标记。
        if int(msg.get("final") or 0) == 1:
            return {"type": "done", "text": self.full_text}

        result = msg.get("result") or {}
        text = str(result.get("voice_text_str") or "").strip()
        slice_type = int(result.get("slice_type") or 0)

        if slice_type == 2:
            # 稳态：定下来了。空稳态不记 —— 纯静音也会回调 slice_type=2。
            self._live = ""
            if text:
                self._done.append(text)
                return {"type": "segment", "text": text}
            return None

        # 0 和 1 都是"还在说"。空结果直接丢：`filter_empty_result=1` 时
        # 服务端本来就不回空，但 0 偶尔会带一帧空串，画出来是一个空行闪一下。
        if not text:
            return None
        self._live = text
        return {"type": "partial", "text": text}

    @property
    def full_text(self) -> str:
        """定型的句子 + 正在说的那句。整理时用这个全文。

        中文不加空格拼接：句号已经在每段末尾（filter_punc=0），
        加空格反而会在"…交作业。明天…"中间塞进一个多余的空格。
        """
        parts = list(self._done)
        if self._live:
            parts.append(self._live)
        return "".join(parts)

    @property
    def has_text(self) -> bool:
        return bool(self._done or self._live)


# 结束包。腾讯云要求发一条 text message 通知"音频传完了"。
END_FRAME = json.dumps({"type": "end"})


def decode(msg: str) -> dict | None:
    """上游 text message → dict。解析不了就返回 None（不抛：
    一条坏包不该让整次会话失败，最多少一句）。"""
    try:
        data = json.loads(msg)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None
