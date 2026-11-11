"""语音转写：签名、请求体、错误分叉。

## 这里能证明什么、不能证明什么

**能**：签名对输入敏感（改 payload / 改时间戳 / 改密钥都会变）、
`DataLen` 是原始字节数、六种上游返回各自落在哪个出口、端点状态码对不对。

**不能**：签名"算对了"。真正的裁决只有一次真实调用（要腾讯云密钥）。
所以下面没有那种"我算一遍给你看"的自证 —— 那只能证明两次写的是同一段代码。
有密钥之后跑 `tools/asr_smoke.py` 才算验证过。
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import main
from core import asr, config, session
from core.auth import get_current_user

_ORIG_DB_PATH = session._db_path


@pytest.fixture()
def ready(monkeypatch):
    """把密钥当已配置，避免每条用例都要 monkeypatch 三个常量。"""
    monkeypatch.setattr(config, "ASR_SECRET_ID", "AKIDtest")
    monkeypatch.setattr(config, "ASR_SECRET_KEY", "secret")
    monkeypatch.setattr(config, "ASR_ENABLED", True)


# ==================== 签名 ====================


class TestSignature:
    def test_shape(self):
        """Authorization 头必须正好是 TC3 的三段式：少一段服务端只会回 AuthFailure。"""
        auth = asr.authorization("AKIDtest", "secret", '{"a":1}', 1794327739)
        assert auth.startswith("TC3-HMAC-SHA256 Credential=AKIDtest/")
        assert "SignedHeaders=content-type;host" in auth
        assert "/asr/tc3_request" in auth
        head, sig = auth.split("Signature=")
        assert len(sig) == 64 and all(c in "0123456789abcdef" for c in sig), sig

    def test_scope_uses_utc_date_not_local(self):
        """日期取 UTC。用本地时区拼 scope 的话，东八区晚上 8 点后签出来的日期
        比服务端早一天，签名永远对不上 —— 而且只在晚上复现。"""
        # 1794327739 = 2026-11-10T16:22:19Z，北京时间已经是 11 日 00:22
        auth = asr.authorization("AKIDtest", "secret", "{}", 1794327739)
        assert "/2026-11-10/asr/tc3_request" in auth

    def test_every_input_changes_the_signature(self):
        base = asr.authorization("AKIDtest", "secret", '{"a":1}', 1794327739)
        assert asr.authorization("AKIDtest", "secret", '{"a":2}', 1794327739) != base
        assert asr.authorization("AKIDtest", "secret", '{"a":1}', 1794327740) != base
        assert asr.authorization("AKIDtest", "other", '{"a":1}', 1794327739) != base
        assert asr.authorization("AKIDother", "secret", '{"a":1}', 1794327739) != base


class TestPayload:
    def test_datalen_is_raw_bytes_not_base64(self):
        """`DataLen` 填成 base64 长度的话，服务端按"数据不完整"回 ErrorRecognize，
        看起来像录音坏了 —— 这个坑只有真调用才会发现，所以在这里钉死。"""
        audio = b"RIFF" + b"\x00" * 100  # 104 字节，base64 后是 140
        body = __import__("json").loads(asr.build_payload(audio))
        assert body["DataLen"] == 104
        assert len(body["Data"]) != 104
        assert body["SourceType"] == 1 and body["VoiceFormat"] == "wav"


# ==================== 失败分叉 ====================


class _Resp:
    def __init__(self, status=200, body=None, boom=None):
        self.status_code = status
        self._body = body
        self._boom = boom

    def json(self):
        if self._boom:
            raise self._boom
        return self._body


class _Client:
    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, *args, **kwargs):
        if self._exc:
            raise self._exc
        return self._resp


def _run(monkeypatch, resp=None, exc=None):
    monkeypatch.setattr(asr.httpx, "AsyncClient", lambda **kw: _Client(resp, exc))
    return asyncio.run(asr.transcribe(b"RIFF" + b"\x00" * 64))


class TestTranscribe:
    def test_ok(self, ready, monkeypatch):
        text = _run(monkeypatch, resp=_Resp(body={"Response": {"Result": " 明天交作业 "}}))
        assert text == "明天交作业"

    def test_silence_is_retryable(self, ready, monkeypatch):
        """录到静音时腾讯云返回 200 + Result=""。这不是配置错误，是"没听见" ——
        必须给重试而不是错误码，否则用户以为麦克风坏了。"""
        with pytest.raises(asr.AsrError) as err:
            _run(monkeypatch, resp=_Resp(body={"Response": {"Result": ""}}))
        assert err.value.retryable is True
        assert "再说一遍" in str(err.value)

    def test_upstream_error_code_is_explained(self, ready, monkeypatch):
        body = {"Response": {"Error": {"Code": "FailedOperation.ErrorRecognize"}}}
        with pytest.raises(asr.AsrError) as err:
            _run(monkeypatch, resp=_Resp(body=body))
        assert "没听清" in str(err.value)
        assert err.value.code == "FailedOperation.ErrorRecognize"

    def test_rate_limited_is_retryable(self, ready, monkeypatch):
        body = {"Response": {"Error": {"Code": "RequestLimitExceeded"}}}
        with pytest.raises(asr.AsrError) as err:
            _run(monkeypatch, resp=_Resp(body=body))
        assert err.value.retryable is True

    def test_auth_failure_is_not_retryable(self, ready, monkeypatch):
        """密钥错的时候重试一万次也没用。文案也不能说"等几秒" —— 那是在骗用户。"""
        body = {"Response": {"Error": {"Code": "AuthFailure.SignatureFailure"}}}
        with pytest.raises(asr.AsrError) as err:
            _run(monkeypatch, resp=_Resp(body=body))
        assert err.value.retryable is False
        assert "没配好" in str(err.value)

    def test_timeout(self, ready, monkeypatch):
        import httpx

        with pytest.raises(asr.AsrError) as err:
            _run(monkeypatch, exc=httpx.TimeoutException("slow"))
        assert err.value.retryable is True

    def test_bad_json(self, ready, monkeypatch):
        with pytest.raises(asr.AsrError):
            _run(monkeypatch, resp=_Resp(body=None, boom=ValueError("no json")))

    def test_not_configured(self, monkeypatch):
        monkeypatch.setattr(config, "ASR_ENABLED", False)
        with pytest.raises(asr.AsrError) as err:
            asyncio.run(asr.transcribe(b"RIFF"))
        assert err.value.code == "NotConfigured"

    def test_too_large(self, ready, monkeypatch):
        monkeypatch.setattr(config, "ASR_MAX_BYTES", 10)
        with pytest.raises(asr.AsrError) as err:
            asyncio.run(asr.transcribe(b"x" * 11))
        assert err.value.code == "TooLong"


# ==================== HTTP 契约 ====================


@pytest.fixture()
def client(tmp_path, monkeypatch):
    path = tmp_path / "sessions.db"
    monkeypatch.setattr(session, "_db_path", lambda: path)
    session._init_db()
    main.app.dependency_overrides[get_current_user] = lambda: "u1"
    yield TestClient(main.app, raise_server_exceptions=False, follow_redirects=False)
    main.app.dependency_overrides.clear()
    monkeypatch.setattr(session, "_db_path", _ORIG_DB_PATH)


class TestEndpoint:
    def test_capabilities_says_whether_mic_should_render(self, client, monkeypatch):
        """前端靠这个决定渲不渲染麦克风。没有密钥时说 true，界面就会出现
        一个点了只弹"还没接"的按钮 —— 那正是"半成品"的来源。"""
        monkeypatch.setattr(config, "ASR_ENABLED", False)
        assert client.get("/api/capture/capabilities").json() == {"voice": False}
        monkeypatch.setattr(config, "ASR_ENABLED", True)
        assert client.get("/api/capture/capabilities").json() == {"voice": True}

    def test_disabled_returns_503(self, client, monkeypatch):
        monkeypatch.setattr(config, "ASR_ENABLED", False)
        r = client.post("/api/capture/transcribe", files={"file": ("a.wav", b"RIFF")})
        assert r.status_code == 503
        assert "先打字" in r.json()["detail"]

    def test_empty_audio_returns_400(self, client, ready, monkeypatch):
        monkeypatch.setattr("api.capture.quota.hit", lambda *a, **k: 1)
        r = client.post("/api/capture/transcribe", files={"file": ("a.wav", b"")})
        assert r.status_code == 400

    def test_oversize_returns_413(self, client, ready, monkeypatch):
        monkeypatch.setattr(config, "ASR_MAX_BYTES", 8)
        r = client.post("/api/capture/transcribe", files={"file": ("a.wav", b"x" * 9)})
        assert r.status_code == 413
        assert "太长" in r.json()["detail"]

    def test_ok_returns_text_only(self, client, ready, monkeypatch):
        """只回 text。不许顺手在这里建待办 —— 转写只是输入层。"""
        monkeypatch.setattr("api.capture.quota.hit", lambda *a, **k: 1)

        async def fake(audio, fmt="wav"):
            return "明天交作业"

        monkeypatch.setattr(asr, "transcribe", fake)
        before = client.get("/api/tasks?scope=all").json()
        r = client.post("/api/capture/transcribe", files={"file": ("a.wav", b"RIFF..")})
        assert r.status_code == 200
        assert r.json() == {"text": "明天交作业"}
        after = client.get("/api/tasks?scope=all").json()
        assert len(after.get("items", after)) == len(before.get("items", before))

    def test_retryable_error_returns_429(self, client, ready, monkeypatch):
        monkeypatch.setattr("api.capture.quota.hit", lambda *a, **k: 1)

        async def fake(audio, fmt="wav"):
            raise asr.AsrError("没听清，再说一遍", retryable=True)

        monkeypatch.setattr(asr, "transcribe", fake)
        r = client.post("/api/capture/transcribe", files={"file": ("a.wav", b"RIFF..")})
        assert r.status_code == 429

    def test_hard_error_returns_502(self, client, ready, monkeypatch):
        monkeypatch.setattr("api.capture.quota.hit", lambda *a, **k: 1)

        async def fake(audio, fmt="wav"):
            raise asr.AsrError("这段录音的格式不支持")

        monkeypatch.setattr(asr, "transcribe", fake)
        r = client.post("/api/capture/transcribe", files={"file": ("a.wav", b"RIFF..")})
        assert r.status_code == 502
