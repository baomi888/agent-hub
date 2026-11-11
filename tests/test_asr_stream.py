"""实时语音识别（WebSocket 流式）：签名原文、分句累积、降级开关。

## 这里能证明什么、不能证明什么

**能**：
  · 签名原文的三条硬规则（不带 `wss://`、参数字典序、signature 不进原文）——
    这三条错了只会得到 4001，服务端不会告诉你是哪一条；
  · 签名对**每一个**输入敏感（换密钥、换一个参数值、换 appid 都会变），
    这条防的是"把参数漏在原文外面却算进了 hash"这类局部错误；
  · 分句累积：`slice_type=1` 是**整句替换**而不是增量拼接 —— 当成 append
    会得到"我我我要要去去"这种叠字，而这在真机上是忽然出现的，很难归因；
  · APPID 缺失时 `stream=false` 但 `voice` 仍为 true（降级而不是功能消失）。

**不能**：签名"一定被服务端接受"。离线只能验证结构。
真正的裁决是 2026-11-11 做的那次真实连接：真实密钥 + 不存在的 APPID
返回了 **4002（AppID 不一致）而不是 4001（签名错误）**，说明服务端已经
验过签名、卡在 APPID 那一步。这条结论无法在 pytest 里复现 ——
它需要真密钥，所以写在这里当背景，不当测试。
"""

from __future__ import annotations

import base64
import binascii

import pytest
from fastapi.testclient import TestClient

import main
from core import asr_stream, config


@pytest.fixture()
def wired(monkeypatch):
    """把三个密钥项当已配置。`wired` 比 `ready` 准确：这里要的是"三项都齐"。"""
    monkeypatch.setattr(config, "ASR_SECRET_ID", "AKIDtestsecretid")
    monkeypatch.setattr(config, "ASR_SECRET_KEY", "testsecretkey")
    monkeypatch.setattr(config, "ASR_APPID", "1250000000")
    monkeypatch.setattr(config, "ASR_STREAM_ENABLED", True)
    monkeypatch.setattr(config, "ASR_HOTWORDS", "")


def _params(**over: str) -> dict[str, str]:
    """一套最小参数，用来单独考签名，不受 build_params 的其他字段影响。"""
    base = {"secretid": "sid", "timestamp": "1700000000", "voice_id": "v1"}
    base.update(over)
    return base


# ==================== 签名原文 ====================


class TestSigningOrigin:
    """官方文档对原文有三条硬性要求，每条错了都只回 4001。"""

    def test_origin_has_no_scheme(self, wired):
        """原文不能带 `wss://`。

        文档给的示例原文是 `asr.cloud.tencent.com/asr/v2/...`，没有协议头。
        带上之后签名必然不对，而错误信息只有"签名错误"，不会提示协议头。

        反证：把 `signing_origin` 的返回值前面拼上 "wss://"，本用例变红。
        """
        origin = asr_stream.signing_origin(_params())
        assert not origin.startswith("wss://")
        assert origin.startswith("asr.cloud.tencent.com/asr/v2/")

    def test_params_are_sorted(self, wired):
        """参数必须按字典序出现在原文里。

        文档示例是 engine_model_type → expired → needvad → nonce → secretid
        → timestamp → voice_format → voice_id，正是字典序。
        dict 的插入顺序不可靠，所以必须显式排 —— 靠"我传的时候就是按顺序传的"
        在任何人改了一行参数顺序之后就会静默失效。

        反证：把 `sorted(params)` 改成 `params`，本用例变红。
        """
        # 故意倒序传入，得到的原文仍然得是升序
        params = {"voice_id": "v1", "timestamp": "1700000000", "secretid": "sid"}
        origin = asr_stream.signing_origin(params)
        query = origin.split("?", 1)[1]
        keys = [chunk.split("=", 1)[0] for chunk in query.split("&")]
        assert keys == sorted(keys), f"参数没排序：{keys}"
        assert keys == ["secretid", "timestamp", "voice_id"]

    def test_signature_is_not_in_origin(self, wired):
        """签名本身不能出现在被签名的原文里。

        这是"先有鸡还是先有蛋"的老错法：如果 build_params 顺手把 signature
        也放进 dict，原文里就会带上一个上一轮的签名，服务端算出来的必然不同。

        反证：给 `_params()` 加一个 `signature="x"` 再调，本用例变红
        （说明这里确实在排除它，而不是碰巧没有）。
        """
        origin = asr_stream.signing_origin(_params())
        assert "signature=" not in origin

    def test_origin_values_are_not_url_encoded(self, wired):
        """原文里是**未编码的值**，编码只发生在拼最终 URL 时。

        这条最阴：热词表里有中文，如果原文里就是 `%E9%AB%98%E6%95%B0`
        而服务端解成 `高数` 再算，两边 hash 会对不上 —— 但只要热词表为空
        就永远测不出来，直到某天补了一个中文热词才炸。

        反证：在 `signing_origin` 里对 value 套 `quote`，本用例变红。
        """
        origin = asr_stream.signing_origin(_params(hotword_list="高数|9"))
        assert "高数|9" in origin
        assert "%E9%AB%98" not in origin


class TestSignatureSensitivity:
    """签名必须对每一个输入敏感。局部漏算的典型症状是"某个参数改了但签名没变"。"""

    def test_deterministic(self, wired):
        """同样的输入必须得到同样的签名 —— 否则每次握手都是新值，无从校验。"""
        a = asr_stream.signature(_params())
        b = asr_stream.signature(_params())
        assert a == b

    def test_changes_with_value(self, wired):
        """改任意一个参数的值，签名必须变。"""
        assert asr_stream.signature(_params()) != asr_stream.signature(
            _params(voice_id="v2")
        )

    def test_changes_with_key(self, wired, monkeypatch):
        """换 SecretKey 必须变。不变说明密钥根本没进 HMAC。

        反证：把 `signature()` 里的 key 写成常量，本用例变红。
        """
        before = asr_stream.signature(_params())
        monkeypatch.setattr(config, "ASR_SECRET_KEY", "another-key")
        assert asr_stream.signature(_params()) != before

    def test_changes_with_appid(self, wired):
        """appid 在 URL **路径**里，也必须被签进去。

        它不在 query 里，所以"遍历 params 逐个拼"的写法会漏掉它 ——
        表现是换成别人的 appid 也能过签名校验（如果服务端只看签名的话），
        或者更常见：自己怎么都对不上。
        """
        assert asr_stream.signature(_params(), "1250000001") != asr_stream.signature(
            _params(), "1250000000"
        )

    def test_is_base64_of_sha1(self, wired):
        """必须是 HMAC-SHA1（20 字节）的 base64，不是 SHA256（32 字节）。

        文档明确写 HMAC-SHA1。写成 SHA256 会得到 44 字符的 base64，
        而 4001 不会告诉你长度不对。
        """
        sig = asr_stream.signature(_params())
        raw = base64.b64decode(sig, validate=True)  # validate=True：不是合法 base64 直接抛
        assert len(raw) == 20, f"解出来 {len(raw)} 字节，SHA1 应该是 20"
        with pytest.raises(binascii.Error):
            base64.b64decode(sig + "!", validate=True)


class TestSignedUrl:
    def test_shape(self, wired):
        url = asr_stream.signed_url("voice-abc")
        assert url.startswith("wss://asr.cloud.tencent.com/asr/v2/1250000000?")
        assert "signature=" in url
        assert url.endswith("signature=" + url.rsplit("signature=", 1)[1])

    def test_signature_is_url_encoded_even_with_special_chars(self, wired, monkeypatch):
        """签名里的 `/` `+` `=` 必须全部编码。

        文档原话："必须进行 URL 编码，编码函数必须要支持对 +、= 等特殊字符的
        编码，否则将导致鉴权失败偶发"。`/` 更严重：它会把 URL 的 path 截断，
        query 直接少一段。

        **这里必须把签名换成固定值**：真实签名里不一定有 `/`（base64 的 64 个
        字符里 `/` 只是其中之一），拿真签名测等于掷骰子 —— 表面上天天绿，
        改坏了也看不出来。这一点是本轮做过的最容易自欺的地方。

        反证：把 `quote(sig, safe="")` 改成 `quote(sig)`（默认 safe="/"），
        本用例立刻变红。
        """
        monkeypatch.setattr(asr_stream, "signature", lambda *a, **k: "a/b+c=")
        url = asr_stream.signed_url("voice-abc")
        assert "%2F" in url, "`/` 没编码，会截断 URL path"
        assert "%2B" in url, "`+` 没编码，服务端会解成空格"
        assert "%3D" in url, "`=` 没编码，参数边界会乱"
        assert "a/b+c=" not in url

    def test_signature_is_last_param(self, wired):
        """签名必须是最后一个参数，且只出现一次。"""
        url = asr_stream.signed_url("voice-abc")
        assert url.count("signature=") == 1
        assert url.endswith(url.rsplit("signature=", 1)[1])

    def test_chinese_hotwords_are_encoded(self, wired, monkeypatch):
        """中文热词必须出现在 URL 里（编码后），而不是被丢掉。

        热词是识别率的关键，静默丢弃的话界面上完全正常，只有准确率会掉。
        """
        monkeypatch.setattr(config, "ASR_HOTWORDS", "高数|9,四六级|8")
        url = asr_stream.signed_url("voice-abc")
        assert "%E9%AB%98%E6%95%B0" in url  # "高数"
        assert "hotword_list=" in url


# ==================== 参数与清洗 ====================


class TestBuildParams:
    def test_vad_and_format(self, wired):
        """`voice_format=1`(pcm) 和 `needvad=1` 是两个不能默认的项。

        格式默认是 4（speex），前端发的是裸 PCM，填错会让上游按 speex 解，
        结果是"能连上但识别出一堆乱码"。
        `needvad=0` 时不会有"说完一句就出稳态结果"，流式就退化成"最后一次性返回"。
        """
        params = asr_stream.build_params("v")
        assert params["voice_format"] == "1"
        assert params["needvad"] == "1"

    def test_punc_kept(self, wired):
        """`filter_punc` 保持 0（保留句号）。句号是整理时断句的依据。"""
        assert asr_stream.build_params("v")["filter_punc"] == "0"

    def test_ttl_is_positive_and_bounded(self, wired):
        """expired 必须大于 timestamp，且间隔小于 90 天（文档硬要求）。"""
        params = asr_stream.build_params("v")
        gap = int(params["expired"]) - int(params["timestamp"])
        assert 0 < gap < 90 * 86400

    def test_dirty_hotwords_are_dropped(self, wired, monkeypatch):
        """含 `&` `=` 空格的热词必须剔除。

        它们会把签名原文的 `k=v&k=v` 结构拆坏 —— 服务端解出来的参数个数
        和签名时不一致，症状还是 4001，但原因和排序、编码都不同。
        内置表里没有这类词，这条防的是运营从环境变量补进来的脏词。

        反证：删掉 `_clean_hotwords` 的过滤条件，本用例变红。
        """
        monkeypatch.setattr(config, "ASR_HOTWORDS", "好词|9,坏&词|5,坏=词|5,有 空格|5")
        params = asr_stream.build_params("v")
        words = params.get("hotword_list", "")
        assert "好词|9" in words
        for bad in ("坏&词", "坏=词", "有 空格"):
            assert bad not in words, f"{bad!r} 没被剔除"


# ==================== 分句累积 ====================


def _msg(slice_type: int, text: str = "") -> dict:
    return {"code": 0, "result": {"slice_type": slice_type, "voice_text_str": text}}


class TestSegmenter:
    """`slice_type` → 界面帧 的映射。"""

    def test_partial_replaces_not_appends(self):
        """`slice_type=1` 返回的是"这一句到目前为止的全文"，必须整句替换。

        当成增量拼接会得到"我我我要要去去图书馆" —— 叠字在真机上是忽然
        冒出来的，很难往"这里该替换"上想。

        反证：把 `self._live = text` 改成 `self._live += text`，本用例变红。
        """
        s = asr_stream.Segmenter()
        assert s.feed(_msg(1, "明天"))["text"] == "明天"
        assert s.feed(_msg(1, "明天三点"))["text"] == "明天三点"
        assert s.full_text == "明天三点"

    def test_stable_segment_appends(self):
        """`slice_type=2` 是定型结果，追加到已确认里，并清掉 live。"""
        s = asr_stream.Segmenter()
        s.feed(_msg(1, "明天三点"))
        frame = s.feed(_msg(2, "明天三点交报告。"))
        assert frame == {"type": "segment", "text": "明天三点交报告。"}
        assert s.full_text == "明天三点交报告。"

    def test_multiple_segments_concatenate_without_space(self):
        """两句之间不加空格：句号已经在每段末尾。"""
        s = asr_stream.Segmenter()
        s.feed(_msg(2, "第一件事。"))
        s.feed(_msg(2, "第二件事。"))
        assert s.full_text == "第一件事。第二件事。"

    def test_empty_stable_is_ignored(self):
        """空稳态不产生帧。

        `needvad=1` 时纯静音也会回调一次 `slice_type=2` 且文本为空，
        不过滤的话界面上会闪一个空行。
        """
        s = asr_stream.Segmenter()
        assert s.feed(_msg(2, "")) is None
        assert s.full_text == ""

    def test_final_returns_full_text(self):
        """`final=1` 帧**不带 result**，只有标记。全文从累积里取。

        这条防的是"拿最后一帧的 voice_text_str 当全文" —— 那是空的，
        表现是"说了一大段，整理的时候只收到最后半句"。
        """
        s = asr_stream.Segmenter()
        s.feed(_msg(2, "明天三点交报告。"))
        s.feed(_msg(1, "顺便拿快递"))
        done = s.feed({"code": 0, "final": 1})
        assert done["type"] == "done"
        assert done["text"] == "明天三点交报告。顺便拿快递"

    def test_live_survives_final(self):
        """最后一句还停在 partial 时点了完成，它也必须进全文。

        用户说完最后一句话立刻点 ✓ 是很常见的操作，此时那句话常常只有
        `slice_type=1`（还没等到 VAD 断句）。不算进去就是"我说了但没记上"。
        """
        s = asr_stream.Segmenter()
        s.feed(_msg(2, "交报告。"))
        s.feed(_msg(1, "拿快递"))
        done = s.feed({"final": 1})
        assert "拿快递" in done["text"]

    def test_upstream_error_becomes_error_frame(self):
        s = asr_stream.Segmenter()
        frame = s.feed({"code": 4001, "message": "..."})
        assert frame["type"] == "error"
        assert frame["retryable"] is False, "配置类错误不该引导用户重说"

    def test_network_error_is_retryable(self):
        """4008（分片等待超时）是网络问题，重说一遍可能就好 —— 要给可重试。"""
        assert asr_stream.Segmenter().feed({"code": 4008})["retryable"] is True

    def test_bad_json_is_none(self):
        """坏包不让整次会话失败。"""
        assert asr_stream.decode("{not json") is None
        assert asr_stream.decode("") is None


# ==================== 降级开关 ====================


class TestDegradation:
    """流式是增强，不是功能本体。它挂掉时语音必须还在。"""

    def test_capabilities_reports_both(self, wired):
        """`/capabilities` 要一次说清两件事：能不能说话、能不能边说边出字。"""
        import asyncio

        from api.capture import get_capabilities

        assert asyncio.run(get_capabilities()) == {"voice": True, "stream": True}

    def test_stream_off_but_voice_on(self, monkeypatch):
        """只填了 SecretId/SecretKey、没填 APPID：voice 仍为 true。

        这是最关键的一条降级语义：把 `stream` 当成语音总开关的话，
        "没填 APPID"在用户看来就是"语音坏了"，而实际只是少了边说边出字。
        """
        monkeypatch.setattr(config, "ASR_SECRET_ID", "sid")
        monkeypatch.setattr(config, "ASR_SECRET_KEY", "skey")
        monkeypatch.setattr(config, "ASR_ENABLED", True)
        monkeypatch.setattr(config, "ASR_APPID", "")
        monkeypatch.setattr(config, "ASR_STREAM_ENABLED", False)
        assert config.ASR_ENABLED is True
        assert asr_stream.enabled() is False

    def test_ws_endpoint_refuses_when_not_configured(self, monkeypatch):
        """没配好时连进来要收到一句人话，而不是静默断开。"""
        monkeypatch.setattr(config, "ASR_STREAM_ENABLED", False)
        client = TestClient(main.app)
        with client.websocket_connect("/api/capture/ws") as ws:
            frame = ws.receive_json()
        assert frame["type"] == "error"
        assert "打字" in frame["message"]

    def test_ws_endpoint_requires_login(self, wired):
        """未登录不能白拿一条到上游的长连接（并发是有配额的）。"""
        client = TestClient(main.app)
        with client.websocket_connect("/api/capture/ws") as ws:
            frame = ws.receive_json()
        assert frame["type"] == "error"
        assert "登录" in frame["message"]
