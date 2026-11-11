"""一句话语音转写（腾讯云 SentenceRecognition）。

为什么是「一句话」而不是实时流：用户对着麦克风说的就是一句话
（"明天三点交报告"），60 秒上限正好覆盖。实时识别要维护 WebSocket 长连接、
断线重连、心跳、流控，换来的「边说边出字」在这个场景里没人看 ——
用户是说完才需要文字，不是边录边读。

## 为什么自己签 TC3，不装 tencentcloud-sdk-python

这里只要一次 HTTPS POST。官方 SDK 会拖进 requests 和一整套协议类，
线上是 2 vCPU / 1.7G，多一个依赖就多一份内存和一次升级风险；
而签名算法只有几十行、版本号写死，比跟着 SDK 升级可控。

## 音频格式

腾讯云一句话识别**不认 webm**（Chrome 的 MediaRecorder 默认就输出它）。
所以前端用 Web Audio 采 PCM 再封成 16k 单声道 WAV 上传，
不要指望后端转码 —— 线上没装 ffmpeg，装了也是给 1.7G 的机器加负担。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import httpx

from core import config

_HOST = "asr.tencentcloudapi.com"
_SERVICE = "asr"
_VERSION = "2019-06-14"
_ACTION = "SentenceRecognition"
_TIMEOUT = 20.0

# ---------------------------------------------------------------------------
# 临时热词表（HotwordList）
#
# 为什么需要：一句话识别是通用中文模型，`高数 / 四六级 / 教务系统 / 大创`
# 这类校园高频词不在它的通用语料里，识别结果经常同音替换（"四六级"→"四六吉"、
# "高数"→"高树"）。热词表是腾讯云给的唯一不改模型就能纠这些词的手段。
#
# 权重规则（官方文档）：
#   · 1-11，11 是"超级热词"；**设多了会拉低整体字准率**，所以这里最高 9。
#   · 100 是"同音强制替换"，只在必须纠正的音近词上用，本表不用。
#   · 单个热词 ≤10 个汉字，整表 ≤128 个。
#
# 只放"说错了会明显看不懂"的词。像"复习""作业"这种通用词不放：
# 它们本来就是模型的强项，设成热词只会把相邻的词挤掉。
# ---------------------------------------------------------------------------
_DEFAULT_HOTWORDS: tuple[tuple[str, int], ...] = (
    # 产品自身
    ("知穗", 9),
    ("每日委托", 7),
    ("学习路径", 6),
    # 课程与学业
    ("高数", 9),
    ("线性代数", 8),
    ("概率论", 8),
    ("大物", 7),
    ("形势与政策", 9),
    ("英语听力", 6),
    ("绩点", 7),
    ("综测", 7),
    ("学分", 6),
    ("期末", 6),
    ("补考", 7),
    ("缓考", 7),
    # 考试
    ("四六级", 9),
    ("四级", 8),
    ("六级", 8),
    ("考研", 7),
    ("保研", 7),
    ("背单词", 6),
    ("真题", 6),
    # 校园事务
    ("选课", 7),
    ("抢课", 7),
    ("教务系统", 8),
    ("体测", 7),
    ("志愿时长", 7),
    ("辅导员", 7),
    ("社团", 5),
    ("班群", 5),
    # 交付物（这些是"说错一个字就看不懂"的词）
    ("实验报告", 7),
    ("课程设计", 7),
    ("文献综述", 6),
    ("开题报告", 7),
    ("实习报告", 6),
    ("答辩", 6),
    ("组会", 6),
    ("大创", 6),
    ("挑战杯", 6),
    # 生活
    ("快递", 5),
    ("食堂", 4),
    ("宿舍", 5),
    # 动作词：整句话的意图全靠它们，听错就等于没听懂
    ("提醒我", 6),
    ("待办", 6),
    ("打卡", 5),
    ("番茄钟", 6),
    # 中英混说
    ("DDL", 6),
    ("PPT", 5),
)

_MAX_HOTWORDS = 128
_MAX_HOTWORD_CHARS = 10


def _hotword_ok(word: str, weight: int) -> bool:
    """单条热词是否合法。非法条目直接跳过 —— 一个脏词不该让整次转写失败。"""
    if not word or len(word) > _MAX_HOTWORD_CHARS:
        return False
    # 逗号和竖线是分隔符，出现在词里会把整张表拆坏
    return "," not in word and "|" not in word and 1 <= weight <= 11


def hotword_list() -> str:
    """拼成腾讯云要的 `词|权重,词|权重`。

    `config.ASR_HOTWORDS` 里的词排在内置表**之后**并覆盖同名项：
    运营补词不用改代码，而内置表保证开箱就有校园词。
    """
    merged: dict[str, int] = {}
    for word, weight in _DEFAULT_HOTWORDS:
        if _hotword_ok(word, weight):
            merged[word] = weight

    for chunk in (config.ASR_HOTWORDS or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        word, _, weight_text = chunk.partition("|")
        try:
            weight = int(weight_text) if weight_text else 5
        except ValueError:
            continue
        word = word.strip()
        if _hotword_ok(word, weight):
            merged[word] = weight

    items = list(merged.items())[:_MAX_HOTWORDS]
    return ",".join(f"{word}|{weight}" for word, weight in items)


# 腾讯云的错误码 → 用户能懂、且指向下一步的话。
# 不把 code 直接抛给用户：`FailedOperation.ErrorRecognize` 对一个想记待办的人没有意义。
_CODES = {
    "AuthFailure": "语音服务没配好，先打字吧",
    "AuthFailure.SignatureFailure": "语音服务没配好，先打字吧",
    "AuthFailure.SecretIdNotFound": "语音服务没配好，先打字吧",
    "RequestLimitExceeded": "现在用的人有点多，等几秒再试",
    "LimitExceeded": "现在用的人有点多，等几秒再试",
    "FailedOperation.ErrorRecognize": "没听清，再说一遍",
    "InvalidParameterValue.ErrorVoicedataTooLong": "说得太长了，一次说一句",
    "InvalidParameterValue.VoiceFormat": "这段录音的格式不支持",
}


class AsrError(Exception):
    """转写失败。`retryable` 决定前端是"再试一次"还是"改成打字"。"""

    def __init__(self, message: str, *, retryable: bool = False, code: str = "") -> None:
        super().__init__(message)
        self.retryable = retryable
        self.code = code


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _sha256_hex(msg: str) -> str:
    return hashlib.sha256(msg.encode("utf-8")).hexdigest()


def authorization(secret_id: str, secret_key: str, payload: str, timestamp: int) -> str:
    """腾讯云 API 3.0 的 TC3-HMAC-SHA256 签名。

    单独抽出来是因为它会算错而且**错得很安静**：签名不对时服务端只回
    AuthFailure，本地看不出是日期格式、还是 header 顺序、还是 payload 哈希。

    `timestamp` 必须是秒。传毫秒的症状是签名过期（服务端按 5 分钟窗口判）。
    """
    date = time.strftime("%Y-%m-%d", time.gmtime(timestamp))
    content_type = "application/json; charset=utf-8"
    canonical_headers = f"content-type:{content_type}\nhost:{_HOST}\n"
    signed_headers = "content-type;host"

    canonical_request = "\n".join(
        ["POST", "/", "", canonical_headers, signed_headers, _sha256_hex(payload)]
    )
    scope = f"{date}/{_SERVICE}/tc3_request"
    string_to_sign = "\n".join(
        ["TC3-HMAC-SHA256", str(timestamp), scope, _sha256_hex(canonical_request)]
    )

    k_date = _hmac_sha256(f"TC3{secret_key}".encode(), date)
    k_service = _hmac_sha256(k_date, _SERVICE)
    k_signing = _hmac_sha256(k_service, "tc3_request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    return (
        f"TC3-HMAC-SHA256 Credential={secret_id}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )


def build_payload(audio: bytes, fmt: str = "wav") -> str:
    """请求体。`DataLen` 是**原始字节数**，不是 base64 之后的长度 ——
    填错的话服务端按"数据不完整"回 ErrorRecognize，看起来像录音有问题。

    除了必填项，只传两个"非默认"参数：
    - `HotwordList`：校园热词，纠同音错字（见 `_DEFAULT_HOTWORDS`）
    - `FilterModal`：过滤"嗯/啊/那个"，避免语气词被当成正文

    `FilterPunc` 保持默认 0（**保留标点**）：整理待办时"明天下午三点。交高数作业"
    这个句号是模型断句的依据，滤掉反而会让它把两件事合成一条。
    `ConvertNumMode` 默认就是 1（中文数字转阿拉伯），不重复传。
    """
    body: dict[str, object] = {
        "EngSerViceType": config.ASR_ENGINE,
        "SourceType": 1,
        "VoiceFormat": fmt,
        "Data": base64.b64encode(audio).decode("ascii"),
        "DataLen": len(audio),
        "ProjectId": 0,
    }

    words = hotword_list()
    if words:
        body["HotwordList"] = words
    if 0 < config.ASR_FILTER_MODAL <= 2:
        body["FilterModal"] = config.ASR_FILTER_MODAL

    return json.dumps(body, ensure_ascii=False)


def _explain(code: str) -> str:
    for prefix, text in _CODES.items():
        if code == prefix or code.startswith(prefix + "."):
            return text
    return "语音没转成，先打字吧"


async def transcribe(audio: bytes, fmt: str = "wav") -> str:
    """音频 → 文字。空音频、超长、上游故障都走 AsrError，不返回空串。

    返回空串是最坏的结果：前端会把它当成"什么都没说"，用户以为麦克风坏了。
    """
    if not config.ASR_ENABLED:
        # 正常路径下压根不该走到这（没有密钥时前端不渲染麦克风），
        # 但接口是公开的，直接打过来也不该拿到 500。
        raise AsrError("语音服务没配好，先打字吧", code="NotConfigured")

    if not audio:
        raise AsrError("没录到声音，再说一遍", retryable=True, code="EmptyAudio")
    if len(audio) > config.ASR_MAX_BYTES:
        raise AsrError("这段话太长了，一次说一句", code="TooLong")

    payload = build_payload(audio, fmt)
    ts = int(time.time())
    headers = {
        "Authorization": authorization(
            config.ASR_SECRET_ID, config.ASR_SECRET_KEY, payload, ts
        ),
        "Content-Type": "application/json; charset=utf-8",
        "Host": _HOST,
        "X-TC-Action": _ACTION,
        "X-TC-Version": _VERSION,
        "X-TC-Timestamp": str(ts),
        "X-TC-Region": config.ASR_REGION,
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(f"https://{_HOST}/", content=payload, headers=headers)
    except httpx.TimeoutException as exc:
        raise AsrError("语音服务没响应，先打字吧", retryable=True, code="Timeout") from exc
    except httpx.HTTPError as exc:
        raise AsrError("语音服务连不上，先打字吧", retryable=True, code="Network") from exc

    if resp.status_code != 200:
        raise AsrError("语音服务没响应，先打字吧", retryable=True, code=f"HTTP{resp.status_code}")

    try:
        body = resp.json()
    except ValueError as exc:
        raise AsrError("语音服务返回了看不懂的东西", retryable=True, code="BadJson") from exc

    data = body.get("Response") or {}
    err = data.get("Error")
    if err:
        code = str(err.get("Code") or "")
        raise AsrError(
            _explain(code),
            retryable=code.startswith(("RequestLimitExceeded", "LimitExceeded", "InternalError")),
            code=code,
        )

    text = str(data.get("Result") or "").strip()
    if not text:
        # 录到的是静音 / 只有噪声。这不是错误，是"没听见"——给重试，别给错误码。
        raise AsrError("没听清，再说一遍", retryable=True, code="EmptyResult")
    return text
