"""一个能真正跑起来的假 SMTP 服务器（socket 级，不用第三方库）。

为什么不用 mock：`smtplib` 里真正会出错的地方全在**协议对话**上 ——
EHLO 的能力协商、AUTH 的两种机制、DATA 的 `.` 终止符、多行 250 响应。
把这些换成 `MagicMock` 就等于只验了「函数没抛异常」，
而「邮件真的发出去了、收件人对、主题没乱码」一个都没验到。
2026-11-11 接 Web Push 时也用过同样的办法（假上游 ASR 服务器），
结论一致：**协议层的问题只有真跑一遍才现形。**

用法：

    server = FakeSMTP()
    server.start()
    monkeypatch.setenv(...)  # 把 host/port 指过来
    mailer.send(...)
    assert server.messages[0]["rcpt"] == ["a@b.com"]
"""

from __future__ import annotations

import base64
import socket
import threading
import time
from email import message_from_bytes, policy
from typing import Any


class FakeSMTP:
    """最小可用的 SMTP 服务器，只实现 smtplib 会用到的那几条命令。

    `behavior` 用来模拟故障：
      * `"ok"`        正常
      * `"auth"`      登录时回 535（授权码错）
      * `"rcpt"`      收件人回 550（地址不存在）
      * `"greet"`     连上就断（网络类故障）
      * `"slow"`      接受连接但永不响应（验超时是否真的生效）
    """

    def __init__(self, behavior: str = "ok") -> None:
        self.behavior = behavior
        self.messages: list[dict[str, Any]] = []
        self.credentials: list[tuple[str, str]] = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.host, self.port = self._sock.getsockname()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ---------- 生命周期 ----------

    def start(self) -> FakeSMTP:
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass

    def __enter__(self) -> FakeSMTP:
        return self.start()

    def __exit__(self, *_exc: Any) -> None:
        self.stop()

    # ---------- 协议 ----------

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client: socket.socket) -> None:
        reader = client.makefile("rb")
        try:
            if self.behavior == "greet":
                client.close()
                return
            if self.behavior == "slow":
                # 只接受连接、永不应答：用来验证客户端超时是否真的设上了。
                self._stop.wait(30)
                client.close()
                return

            client.sendall(b"220 fake.local ESMTP ready\r\n")
            rcpt: list[str] = []
            mail_from = ""
            while True:
                raw = reader.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").strip()
                upper = line.upper()

                if upper.startswith(("EHLO", "HELO")):
                    client.sendall(
                        b"250-fake.local\r\n"
                        b"250-AUTH PLAIN LOGIN\r\n"
                        b"250 OK\r\n"
                    )
                elif upper.startswith("AUTH PLAIN"):
                    payload = line[10:].strip()
                    if not payload:
                        client.sendall(b"334 \r\n")
                        payload = reader.readline().decode("ascii", "replace").strip()
                    try:
                        _authz, user, pwd = (
                            base64.b64decode(payload).decode("utf-8").split("\x00")
                        )
                    except Exception:  # noqa: BLE001
                        client.sendall(b"535 bad credentials\r\n")
                        continue
                    self.credentials.append((user, pwd))
                    if self.behavior == "auth":
                        client.sendall(b"535 Authentication failed\r\n")
                    else:
                        client.sendall(b"235 2.7.0 Accepted\r\n")
                elif upper.startswith("AUTH LOGIN"):
                    client.sendall(b"334 VXNlcm5hbWU6\r\n")
                    user = base64.b64decode(reader.readline().strip()).decode("utf-8", "replace")
                    client.sendall(b"334 UGFzc3dvcmQ6\r\n")
                    pwd = base64.b64decode(reader.readline().strip()).decode("utf-8", "replace")
                    self.credentials.append((user, pwd))
                    client.sendall(
                        b"535 Authentication failed\r\n"
                        if self.behavior == "auth"
                        else b"235 2.7.0 Accepted\r\n"
                    )
                elif upper.startswith("MAIL FROM"):
                    mail_from = line.split(":", 1)[1].strip().strip("<>")
                    client.sendall(b"250 OK\r\n")
                elif upper.startswith("RCPT TO"):
                    addr = line.split(":", 1)[1].strip().strip("<>")
                    if self.behavior == "rcpt":
                        client.sendall(b"550 5.1.1 No such user\r\n")
                    else:
                        rcpt.append(addr)
                        client.sendall(b"250 OK\r\n")
                elif upper == "DATA":
                    client.sendall(b"354 End data with <CR><LF>.<CR><LF>\r\n")
                    body = b""
                    while True:
                        chunk = reader.readline()
                        if not chunk or chunk in (b".\r\n", b".\n"):
                            break
                        # 拆点：行首的 ".." 是转义出来的一个 "."
                        if chunk.startswith(b".."):
                            chunk = chunk[1:]
                        body += chunk
                    msg = message_from_bytes(body, policy=policy.default)
                    self.messages.append(
                        {
                            "mail_from": mail_from,
                            "rcpt": rcpt[:],
                            "subject": str(msg.get("Subject") or ""),
                            "to": str(msg.get("To") or ""),
                            "from": str(msg.get("From") or ""),
                            "date": str(msg.get("Date") or ""),
                            "text": _body_text(msg),
                            "raw": body,
                        }
                    )
                    rcpt = []
                    client.sendall(b"250 OK queued\r\n")
                elif upper == "RSET":
                    rcpt = []
                    client.sendall(b"250 OK\r\n")
                elif upper == "NOOP":
                    client.sendall(b"250 OK\r\n")
                elif upper == "QUIT":
                    client.sendall(b"221 Bye\r\n")
                    break
                else:
                    client.sendall(b"250 OK\r\n")
        except OSError:
            pass
        finally:
            try:
                client.close()
            except OSError:
                pass

    # ---------- 断言辅助 ----------

    def wait_for(self, count: int = 1, timeout: float = 5.0) -> bool:
        """等到收到 count 封（或超时）。发送是异步落在别的线程里的。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if len(self.messages) >= count:
                return True
            time.sleep(0.02)
        return len(self.messages) >= count


def _body_text(msg: Any) -> str:
    """取出纯文本正文。多部件时优先 text/plain。"""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    return str(part.get_content())
                except Exception:  # noqa: BLE001
                    continue
        return ""
    try:
        return str(msg.get_content())
    except Exception:  # noqa: BLE001
        return ""
