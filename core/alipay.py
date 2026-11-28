"""支付宝网站身份适配器：官方 SDK 负责 RSA2 签名和响应验签。

只返回统一外部身份，不创建本站用户；不保存或返回支付宝 access_token。
SDK 原始异常可能包含授权码、令牌和响应全文，必须在本边界内转换为安全错误。
"""

import base64
import json
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from core import config
from core.external_identity import ExternalAuthError, ExternalIdentity

_GATEWAY = "https://openapi.alipay.com/gateway.do"


@lru_cache(maxsize=4)
def _load_keys(private_path: str, private_stamp: tuple, public_path: str, public_stamp: tuple) -> tuple[str, str]:
    from Crypto.PublicKey import RSA

    def load(path, private):
        raw = Path(path).read_bytes().strip()
        if not raw or len(raw) > 16384:
            raise ValueError("密钥文件格式不正确")
        if not raw.startswith(b"-----BEGIN "):
            raw = base64.b64decode(b"".join(raw.split()), validate=True)
        key = RSA.import_key(raw)
        if key.has_private() != private or key.size_in_bits() < 2048:
            raise ValueError("密钥角色或长度不正确")
        return key.export_key(format="PEM", pkcs=1).decode("ascii")

    return load(private_path, True), load(public_path, False)


def _keys() -> tuple[str, str]:
    private, public = Path(config.ALIPAY_APP_PRIVATE_KEY_FILE), Path(config.ALIPAY_PUBLIC_KEY_FILE)
    ps, qs = private.stat(), public.stat()
    return _load_keys(
        str(private.resolve()), (ps.st_mtime_ns, ps.st_size), str(public.resolve()), (qs.st_mtime_ns, qs.st_size)
    )


def enabled() -> bool:
    if not config.ALIPAY_LOGIN_ENABLED:
        return False
    callback = urlsplit(config.ALIPAY_REDIRECT_URI)
    if not (
        config.ALIPAY_APP_ID
        and config.ALIPAY_SUBJECT_FIELD in ("open_id", "user_id")
        and callback.scheme == "https"
        and callback.netloc
        and not callback.username
        and not callback.password
        and not callback.query
        and not callback.fragment
        and callback.path == "/api/auth/alipay/callback"
    ):
        return False
    try:
        _keys()
        from alipay.aop.api.DefaultAlipayClient import DefaultAlipayClient  # noqa: F401
    except (ImportError, OSError, ValueError, TypeError):
        return False
    return True


def authorize_url(state: str) -> str:
    return "https://openauth.alipay.com/oauth2/publicAppAuthorize.htm?" + urlencode(
        {
            "app_id": config.ALIPAY_APP_ID,
            "scope": "auth_user",
            "redirect_uri": config.ALIPAY_REDIRECT_URI,
            "state": state,
        }
    )


def _client():
    from alipay.aop.api.AlipayClientConfig import AlipayClientConfig
    from alipay.aop.api.DefaultAlipayClient import DefaultAlipayClient

    private, public = _keys()
    settings = AlipayClientConfig()
    settings.app_id = config.ALIPAY_APP_ID
    settings.app_private_key = private
    settings.alipay_public_key = public
    settings.server_url = _GATEWAY
    settings.sign_type = "RSA2"
    settings.charset = "utf-8"
    settings.timeout = 10
    settings.skip_sign = False
    # SDK logger 会记录带授权码/令牌的请求与响应，登录模块不传入 logger。
    return DefaultAlipayClient(alipay_client_config=settings, logger=None)


def _execute(client, request) -> dict:
    from alipay.aop.api.exception.Exception import ResponseException

    try:
        content = client.execute(request)  # SDK 在返回业务正文之前已经验签。
    except ResponseException:
        raise ExternalAuthError("identity_invalid", "支付宝响应验证失败，请重新授权") from None
    except Exception:
        raise ExternalAuthError("provider_unavailable", "支付宝服务暂不可用，请稍后重试", 503) from None
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        raise ExternalAuthError("provider_failed", "支付宝返回数据无效，请重新授权") from None
    if not isinstance(data, dict) or data.get("code") not in (None, "10000"):
        raise ExternalAuthError("provider_failed", "支付宝授权未完成，请重新授权")
    return data


def authenticate(code: str) -> ExternalIdentity:
    if not enabled():
        raise ExternalAuthError("not_configured", "支付宝登录尚未配置完成", 503)
    if not code or len(code) > 512:
        raise ExternalAuthError("provider_failed", "支付宝授权码无效")
    try:
        from alipay.aop.api.request.AlipaySystemOauthTokenRequest import AlipaySystemOauthTokenRequest
        from alipay.aop.api.request.AlipayUserInfoShareRequest import AlipayUserInfoShareRequest

        client = _client()
        token_request = AlipaySystemOauthTokenRequest()
        token_request.grant_type = "authorization_code"
        token_request.code = code
        token = _execute(client, token_request)
        field = config.ALIPAY_SUBJECT_FIELD
        subject, access_token = token.get(field), token.get("access_token")
        if (
            not isinstance(subject, str)
            or not subject
            or len(subject) > 200
            or subject != subject.strip()
            or not isinstance(access_token, str)
            or not access_token
        ):
            raise ExternalAuthError("identity_invalid", "支付宝未返回有效身份，请核对应用身份字段配置")
        profile_request = AlipayUserInfoShareRequest()
        profile_request.add_other_text_param("auth_token", access_token)
        profile = _execute(client, profile_request)
        # 资料接口明确返回业务成功码；换令牌的成功响应可以省略 code。
        if profile.get("code") != "10000":
            raise ExternalAuthError("provider_failed", "支付宝用户资料请求未成功，请重新授权")
        if profile.get(field) != subject:
            raise ExternalAuthError("identity_invalid", "支付宝身份校验失败")
        nickname = profile.get("nick_name")
        # 显式记录标识种类，避免不同标识恰好相同的文本发生身份碰撞。
        return ExternalIdentity(
            "alipay", config.ALIPAY_APP_ID, field + ":" + subject, nickname if isinstance(nickname, str) else ""
        )
    except ExternalAuthError:
        raise
    except Exception:
        raise ExternalAuthError("provider_unavailable", "支付宝验证暂不可用，请稍后重试", 503) from None
