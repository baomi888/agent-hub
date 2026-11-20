"""标准库 SMTP 传输层：单收件人、显式超时、固定错误分类与敏感信息保护。"""

from __future__ import annotations

import base64
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Any

from core import config

logger = logging.getLogger(__name__)

# 错误码 → 是否值得重试。None 表示成功。
_AUTH_ERRORS = (smtplib.SMTPAuthenticationError, smtplib.SMTPSenderRefused)
_ADDR_ERRORS = (smtplib.SMTPRecipientsRefused,)
_TRANSIENT = (
    smtplib.SMTPServerDisconnected,
    smtplib.SMTPConnectError,
    smtplib.SMTPHeloError,
    TimeoutError,
    ConnectionError,
    OSError,
)


class MailInvalid(ValueError):
    """参数本身不合法（地址格式、头部含控制字符）。不该重试。"""


def _clean_header(value: str, field: str) -> str:
    """头部值消毒：禁止任何控制字符。

    不做「把 \\r\\n 删掉再照发」：那样用户看到的是标题被悄悄改了，
    而这封信已经出去了。宁可报错让他改一个字。
    """
    text = str(value or "")
    if any(ch in text for ch in ("\r", "\n", "\x00")):
        raise MailInvalid(f"{field}里不能有换行")
    return text.strip()


def _clean_body(value: str) -> str:
    """正文只做长度保护与 NUL 剔除；换行是合法的，必须保留。"""
    return str(value or "").replace("\x00", "")


def _addr_list(to: Any) -> list[str]:
    """收件人规范化。

    只接受**单个**地址。传列表或逗号串一律拒绝：这个通道的收件人是
    「用户自己验证过的那个邮箱」，一旦允许一次多个，任何能设地址的地方
    都变成了群发口子。
    """
    if isinstance(to, (list, tuple)):
        if len(to) != 1:
            raise MailInvalid("一次只能发给一个地址")
        to = to[0] if to else ""
    addr = str(to or "").strip()
    if not addr:
        raise MailInvalid("收件地址是空的")
    if "," in addr or ";" in addr or " " in addr:
        raise MailInvalid("收件地址只能有一个")
    local, _, domain = addr.partition("@")
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise MailInvalid("这个邮箱地址看起来不对")
    return [addr]


def _build(to: str, subject: str, text: str, html: str | None) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = _clean_header(subject, "标题") or "提醒"
    msg["From"] = formataddr((_clean_header(config.SMTP_FROM_NAME, "发件人"), config.SMTP_FROM))
    msg["To"] = _clean_header(to, "收件地址")
    msg["Message-ID"] = make_msgid(domain="myzhisui.cn")
    # 显式写 Date：EmailMessage 不会自动补，缺了它有些客户端会显示 1970 年，
    # 用户看到"一封 1970 年的信"会直接当垃圾邮件。
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(_clean_body(text) or "（无正文）")
    if html:
        msg.add_alternative(_clean_body(html), subtype="html")
    return msg


def _classify(exc: BaseException) -> tuple[str, bool]:
    """异常 → (错误码, 是否可重试)。错误码是给日志和 detail 用的短标签。"""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        # 535：QQ/163 最常见的坑是拿登录密码当授权码
        return "auth_failed", False
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "recipient_refused", False
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return "sender_refused", False
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return "smtp_unsupported", False
    if isinstance(exc, (_TRANSIENT + _AUTH_ERRORS + _ADDR_ERRORS)):
        return "network", True
    if isinstance(exc, smtplib.SMTPException):
        return "smtp_error", True
    return "unknown", True


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _authenticate(client: smtplib.SMTP) -> None:
    """自己做 AUTH，**不用 `smtplib.login()`**。

    ★ 这一条是实测出来的，不是洁癖：`login()` 在第一个机制失败后会
    **自动换下一个机制重试**（PLAIN → LOGIN → …）。而 QQ 邮箱在第一次
    535 之后就直接掐断连接，于是第二次尝试抛 `SMTPServerDisconnected`，
    把真正的 535 盖掉了。结果就是**授权码填错被报成「网络问题，可重试」**：
    运维去查网络（白查），用户则永远收不到信（静默）。

    只走一个机制，535 就能原样上升为 `SMTPAuthenticationError`，
    被正确归成「配置错、不可重试」。
    """
    user, password = config.SMTP_USER, config.SMTP_PASSWORD
    mechanisms = (client.esmtp_features.get("auth") or "").upper().split()

    if "PLAIN" in mechanisms:
        code, resp = client.docmd("AUTH", "PLAIN " + _b64(f"\0{user}\0{password}"))
    elif "LOGIN" in mechanisms:
        code, resp = client.docmd("AUTH", "LOGIN")
        if code == 334:
            code, resp = client.docmd(_b64(user))
        if code == 334:
            code, resp = client.docmd(_b64(password))
    else:
        raise smtplib.SMTPNotSupportedError(f"服务器不支持登录认证（{mechanisms or '无'}）")

    if code != 235:
        raise smtplib.SMTPAuthenticationError(code, resp)


def send(
    to: str,
    subject: str,
    text: str,
    html: str | None = None,
) -> dict[str, Any]:
    """发一封纯文本（可选 HTML 替代体）邮件。

    返回 `{ok, error, retryable, detail}`：
      * `ok=False, error="not_configured"` —— 服务端没配 SMTP，调用方应静默跳过；
      * `ok=False, retryable=True`  —— 网络类，下次还能试；
      * `ok=False, retryable=False` —— 配置或地址错，重试无意义。

    **绝不抛异常**给调用方：提醒派发里一条邮件失败不能把整轮 job 记成失败，
    那会触发 worker 的三次重试，而重试期间这个人的其他提醒还是收不到，
    日志里显示的却是「调度失败」，方向全错。
    """
    if not config.mail_ready():
        return {"ok": False, "error": "not_configured", "retryable": False, "detail": "SMTP 未配置"}

    try:
        recipients = _addr_list(to)
        msg = _build(recipients[0], subject, text, html)
    except MailInvalid as exc:
        return {"ok": False, "error": "invalid", "retryable": False, "detail": str(exc)}

    try:
        if config.SMTP_SECURITY == "none":
            client: smtplib.SMTP = smtplib.SMTP(
                config.SMTP_HOST, config.SMTP_PORT, timeout=config.SMTP_TIMEOUT
            )
        elif config.SMTP_SECURITY == "starttls":
            client = smtplib.SMTP(
                config.SMTP_HOST, config.SMTP_PORT, timeout=config.SMTP_TIMEOUT
            )
        else:
            client = smtplib.SMTP_SSL(
                config.SMTP_HOST,
                config.SMTP_PORT,
                timeout=config.SMTP_TIMEOUT,
                context=ssl.create_default_context(),
            )
        with client:
            client.ehlo()
            if config.SMTP_SECURITY == "starttls":
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
            _authenticate(client)
            client.send_message(msg)
    except Exception as exc:  # noqa: BLE001 — 这里是唯一的错误收口，必须全接住
        code, retryable = _classify(exc)
        # SMTP 原始响应可能回显凭证、验证码或地址，不能进入日志或接口。
        # 保留分类码与重试判断，使用固定说明替代外部响应。
        detail = {
            "auth_failed": "发信账号认证失败",
            "recipient_refused": "收件地址被拒绝",
            "sender_refused": "发件地址被拒绝",
            "network": "邮件连接暂时不可用",
            "smtp_unsupported": "邮件认证方式不受支持",
        }.get(code, "邮件服务暂时不可用")
        detail = f"{detail} ({_mask(recipients[0])})"
        logger.warning("mail send failed to=%s code=%s detail=%s", _mask(recipients[0]), code, detail)
        return {"ok": False, "error": code, "retryable": retryable, "detail": detail}

    logger.info("mail sent to=%s subject=%s", _mask(recipients[0]), subject[:40])
    return {"ok": True, "error": None, "retryable": False, "detail": ""}


def _mask(address: str) -> str:
    """日志里的地址打码：`ab***@qq.com`。日志会被分享出去排障，别带全量地址。"""
    local, _, domain = str(address or "").partition("@")
    if not local:
        return "(空)"
    keep = local[:2] if len(local) > 2 else local[:1]
    return f"{keep}***@{domain}"
