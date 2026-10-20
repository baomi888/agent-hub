"""A7 回归：JWT header 必须校验（2026-10-20 审计修复）。

**先把话说清楚**：当前实现**不可利用**。`decode_claims` 的验签是硬编码
`hmac.new(secret, ..., sha256)`，不按 `header.alg` 分派算法，所以经典的
`alg: none` / `HS256→RS256` 混淆打不进来——伪造 header 也没用，签名仍然是错的。

修的是**迁移风险**：`core/auth.py` 顶部写着"将来想换成 PyJWT"。
PyJWT 的行为恰恰是按 `header.alg` 分派算法。迁移那天如果没人记得补这个校验，
今天这个"无害的宽松"会立刻变成真漏洞——而迁移是最容易漏测的时刻。

所以本测试的每条断言构造的都是"**签名正确、只有 header 不对**"的令牌：
只有 header 校验能拦住它们，签名校验拦不住。这样断言才证明得了新代码确实生效。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

from core import auth, config


@pytest.fixture(autouse=True)
def fixed_secret(monkeypatch):
    """固定密钥，避免测试在 data/ 下生成 .auth_secret 文件。"""
    monkeypatch.setattr(config, "AUTH_SECRET", "unit-test-secret")
    monkeypatch.setattr(config, "JWT_HEADER_CHECK", True)


def _token(header: dict, payload: dict) -> str:
    """按 HS256 正确签名，header 由调用方任意指定。"""
    h = auth._b64e(json.dumps(header, separators=(",", ":")).encode())
    p = auth._b64e(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(auth._secret(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    return f"{h}.{p}.{auth._b64e(sig)}"


def _payload() -> dict:
    return {"uid": "u1", "sub": "u1", "exp": int(time.time()) + 3600, "jti": "j1"}


def test_normal_token_roundtrip():
    tok = auth.create_token("u1", username="alice")
    claims = auth.decode_claims(tok)
    assert claims and claims["uid"] == "u1"
    assert auth.decode_token(tok) == "u1"


def test_alg_none_rejected():
    """签名是对的，只有 alg 声明成 none —— 只有 header 校验能拦住。"""
    assert auth.decode_claims(_token({"alg": "none", "typ": "JWT"}, _payload())) is None


def test_alg_rs256_rejected():
    assert auth.decode_claims(_token({"alg": "RS256", "typ": "JWT"}, _payload())) is None


def test_wrong_typ_rejected():
    assert auth.decode_claims(_token({"alg": "HS256", "typ": "JWE"}, _payload())) is None


def test_missing_typ_allowed():
    """typ 不参与安全判定，手工令牌常常省略它 → 存在才校验，缺失放行。"""
    claims = auth.decode_claims(_token({"alg": "HS256"}, _payload()))
    assert claims and claims["uid"] == "u1"


def test_garbage_header_rejected():
    payload = auth._b64e(json.dumps(_payload(), separators=(",", ":")).encode())
    sig = hmac.new(auth._secret(), f"!!!.{payload}".encode(), hashlib.sha256).digest()
    assert auth.decode_claims(f"!!!.{payload}.{auth._b64e(sig)}") is None


def test_switch_off_restores_permissive_behaviour(monkeypatch):
    """开关关掉 → 退回不校验 header（证明开关是真的，也证明回退路径可用）。"""
    monkeypatch.setattr(config, "JWT_HEADER_CHECK", False)
    claims = auth.decode_claims(_token({"alg": "none", "typ": "JWT"}, _payload()))
    assert claims and claims["uid"] == "u1"


def test_expired_still_rejected():
    p = _payload()
    p["exp"] = int(time.time()) - 10
    assert auth.decode_claims(_token({"alg": "HS256", "typ": "JWT"}, p)) is None


def test_tampered_signature_still_rejected():
    """header 校验不能替代验签：签名错照样拒。"""
    tok = _token({"alg": "HS256", "typ": "JWT"}, _payload())
    head, body, _sig = tok.split(".")
    assert auth.decode_claims(f"{head}.{body}.AAAA") is None
