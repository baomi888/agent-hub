"""PC 网站微信登录适配器：授权码只由本站后端向微信兑换，身份不来自前端 JSON。"""

import logging
import re
from urllib.parse import urlencode, urlsplit

import httpx

from core import config
from core.external_identity import ExternalAuthError, ExternalIdentity


class _RedactWechatCredentials(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        if "api.weixin.qq.com" in message:
            record.msg = re.sub(
                r"https://api\.weixin\.qq\.com[^\s\"]+", "https://api.weixin.qq.com/[redacted]", message
            )
            record.args = ()
        return True


# 微信的 GET 接口在查询串中传递凭证；httpx INFO 日志不能把这些查询串写出。
logging.getLogger("httpx").addFilter(_RedactWechatCredentials())


def enabled() -> bool:
    callback = urlsplit(config.WECHAT_REDIRECT_URI)
    return bool(
        config.WECHAT_LOGIN_ENABLED
        and config.WECHAT_APP_ID
        and config.WECHAT_APP_SECRET
        and callback.scheme == "https"
        and callback.netloc
        and not callback.username
        and not callback.password
        and not callback.query
        and not callback.fragment
        and callback.path == "/api/auth/wechat/callback"
    )


def authorize_url(state: str) -> str:
    return (
        "https://open.weixin.qq.com/connect/qrconnect?"
        + urlencode(
            {
                "appid": config.WECHAT_APP_ID,
                "redirect_uri": config.WECHAT_REDIRECT_URI,
                "response_type": "code",
                "scope": "snsapi_login",
                "state": state,
            }
        )
        + "#wechat_redirect"
    )


def _get(client, path: str, params: dict) -> dict:
    response = client.get("https://api.weixin.qq.com" + path, params=params)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or data.get("errcode") not in (None, 0):
        raise ExternalAuthError("provider_failed", "微信验证失败，请重新扫码")
    return data


def authenticate(code: str) -> ExternalIdentity:
    """不持久保存 access_token；日志和浏览器也不返回微信凭证或接口原始错误。"""
    try:
        with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
            token = _get(
                client,
                "/sns/oauth2/access_token",
                {
                    "appid": config.WECHAT_APP_ID,
                    "secret": config.WECHAT_APP_SECRET,
                    "code": code,
                    "grant_type": "authorization_code",
                },
            )
            subject, access_token = token.get("openid"), token.get("access_token")
            if not isinstance(subject, str) or not subject or not isinstance(access_token, str) or not access_token:
                raise ExternalAuthError("provider_failed", "微信未返回有效身份，请重新扫码")
            profile = _get(
                client,
                "/sns/userinfo",
                {
                    "access_token": access_token,
                    "openid": subject,
                    "lang": "zh_CN",
                },
            )
            if profile.get("openid") != subject:
                raise ExternalAuthError("identity_invalid", "微信身份校验失败")
            nickname = profile.get("nickname")
            return ExternalIdentity(
                "wechat", config.WECHAT_APP_ID, subject, nickname if isinstance(nickname, str) else ""
            )
    except (httpx.HTTPError, ValueError) as exc:
        if isinstance(exc, ExternalAuthError):
            raise
        raise ExternalAuthError("provider_unavailable", "微信服务暂不可用，请稍后重新扫码", 503) from None
