"""账号安全邮件发送适配；不生成验证码、不访问账号库，不代表完成绑定。"""

from __future__ import annotations

import re
from typing import Any

from core import config, mailer

_PURPOSES = {
    "register_email": "注册账号",
    "bind_email": "绑定安全邮箱",
    "change_email_old": "验证原安全邮箱",
    "change_email_new": "验证新安全邮箱",
    "reset_password": "找回密码",
}
_ADDRESS = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}", re.ASCII)
CODE_TTL_SECONDS = 600
_DETAILS = {
    "not_configured": "安全邮件服务尚未配置，请联系管理员",
    "invalid": "邮箱、验证码或邮件用途不正确",
    "auth_failed": "发信服务认证失败，请联系管理员",
    "sender_refused": "发信地址被拒绝，请联系管理员",
    "recipient_refused": "收件地址被邮件服务拒绝",
    "network": "邮件服务暂时不可用，请稍后再试",
    "smtp_unsupported": "邮件服务认证方式不受支持，请联系管理员",
    "smtp_error": "邮件服务发送失败，请稍后再试",
    "unknown": "邮件服务暂时不可用，请稍后再试",
}


def status() -> dict[str, bool]:
    """只返回能力状态，不向前端暴露账号、主机或授权凭证。"""
    local = config.SMTP_HOST in ("localhost", "127.0.0.1", "::1")
    transport_ok = config.SMTP_SECURITY in ("ssl", "starttls") or (config.SMTP_SECURITY == "none" and local)
    ready = bool(
        config.SECURITY_EMAIL_ENABLED
        and config.mail_ready()
        and transport_ok
        and 0 < config.SMTP_PORT <= 65535
        and config.SMTP_TIMEOUT > 0
    )
    return {"ready": ready}


def _invalid() -> dict[str, Any]:
    return {"ok": False, "error": "invalid", "retryable": False, "detail": _DETAILS["invalid"]}


def send_code(address: str, code: str, purpose: str) -> dict[str, Any]:
    """向单个邮箱发送已有验证码；上层负责限流、状态和验证码验证。

    这里成功只表示 SMTP 服务已接受邮件，不保证已进入用户收件箱。
    10 分钟有效期须与后续验证码存储统一使用 CODE_TTL_SECONDS。
    """
    if not isinstance(address, str) or not isinstance(code, str):
        return _invalid()
    address = address.strip()
    if (
        len(address) > 254
        or not _ADDRESS.fullmatch(address)
        or not re.fullmatch(r"[0-9]{6}", code)
        or purpose not in _PURPOSES
    ):
        return _invalid()
    local, domain = address.rsplit("@", 1)
    if ".." in domain or domain.startswith(("-", ".")) or domain.endswith(("-", ".")):
        return _invalid()
    address = f"{local}@{domain.lower()}"
    if not status()["ready"]:
        return {"ok": False, "error": "not_configured", "retryable": False, "detail": _DETAILS["not_configured"]}
    label = _PURPOSES[purpose]
    text = (
        f"你正在进行：{label}。\n\n"
        f"你的验证码是：{code}\n"
        f"{CODE_TTL_SECONDS // 60} 分钟内有效，请在知穗 Agent 对应页面输入。\n\n"
        "请勿向他人提供验证码。非本人操作请忽略此邮件。\n"
        "本邮件仅用于账号安全验证，不会自动绑定邮箱或修改密码。\n"
    )
    result = mailer.send(address, f"知穗 Agent · {label}验证码", text)
    error = result.get("error")
    if result.get("ok"):
        return {"ok": True, "error": None, "retryable": False, "detail": ""}
    if error not in _DETAILS:
        error = "unknown"
    # 不把外部 SMTP 的原始异常或响应转交给调用方。
    return {"ok": False, "error": error, "retryable": bool(result.get("retryable")), "detail": _DETAILS[error]}
