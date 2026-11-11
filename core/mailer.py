"""邮件发信底层（标准库 smtplib，不引第三方依赖）。

## 为什么是兜底通道

安卓的 Web Push 底层一律走 Google 的 FCM，国内连不通，换浏览器不解决
（`frontend/src/lib/push.ts` 顶部有完整说明）。日历订阅又在 2026-11-11 删掉了，
所以安卓上「提醒」等于只能打开 App 才看得见。邮件是唯一在「没有 App、
没有 GMS、不必点授权」的前提下还能把人叫回来的通道。

语义是**补位不是并行**：调用方（`service/reminder_tick.py`）只在 push
一条都没送出去时才发信。已经能收到通知的人再收一封邮件只是打扰。

## 三个必须挡住的东西

1. **超时。** `smtplib` 默认不设 socket 超时，服务器卡住时会阻塞到 TCP 自己
   放弃（十几分钟）。worker 是单线程串行跑 job 的，一个卡住的 SMTP 连接会把
   后面所有人的提醒一起拖死。所以 `SMTP_TIMEOUT` 不是可选项，
   `smtplib.SMTP(..., timeout=)` 与 `SMTP_SSL(..., timeout=)` 都要显式传。

2. **错误分类。** 「发失败」这一个说法会让排查往错方向走：
   * 授权码填错 → 每次都在登录就被拒，改多少次配置都没用，必须一眼看出来；
   * 收件人打错 → 服务器明确 550，重试一万次也一样；
   * 网络抖动 / 服务器 421 → 过一会儿确实能好。
   三类混在一起，运维只会看到「邮件发不出去」然后去重启服务。

3. **邮件头注入。** 标题里若含 CR/LF，`EmailMessage` 在老版本上会把后半截
   当成新头部，等于让用户决定这封信的收件人。所以所有进头部的值一律先过
   `_clean_header`，发现控制字符直接判为参数错误，不做「清洗后照发」——
   静默改写用户的标题比报错更难查。
"""

from __future__ import annotations

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
            client.login(config.SMTP_USER, config.SMTP_PASSWORD)
            client.send_message(msg)
    except Exception as exc:  # noqa: BLE001 — 这里是唯一的错误收口，必须全接住
        code, retryable = _classify(exc)
        # 只记错误码与第一行，**不记完整异常体**：smtplib 的 str(exc) 里带
        # 服务器原始响应，某些服务器会回显账号，日志会被 grep 到。
        detail = str(exc).splitlines()[0][:200] if str(exc) else code
        # ★ 补一刀：SMTPRecipientsRefused 的 str() 是 `{'收件人': (550, ...)}`，
        #   地址会绕过上面的 to= 打码原样进日志。日志是要被分享出去排障的。
        detail = detail.replace(recipients[0], _mask(recipients[0]))
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
