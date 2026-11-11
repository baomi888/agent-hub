"""邮件兜底通道：发信、验证码、派发接入、HTTP 契约。

## 为什么这组测试值得写这么细

邮件是这个项目里**唯一一条会把内容发到外部服务**的通道，而且它存在的理由是
「另一条通道在国内根本不通」。这类代码有三个特点是单元测试最讨厌的：

1. 真正的错误全在**协议对话**里（AUTH 机制、多行 250、DATA 的 `.` 终止符），
   mock 掉 `smtplib` 就等于什么都没验；
2. 失败模式**大多是静默的**（地址打错、授权码错、服务端卡住），
   用户那边看到的是"没收到"，而不是报错；
3. 一旦阻塞，会把**整个 worker** 拖住（它是单线程串行跑 job 的）。

所以这里用 `tests/fake_smtp.py` 起一个真 socket 服务器，字节真的走一遍协议；
每条断言都配过反证（把对应的判断改掉，测试必须变红）。
"""

from __future__ import annotations

import smtplib
import time

import pytest
from fake_smtp import FakeSMTP
from fastapi.testclient import TestClient

import main
from core import config, mail_store, mailer, reminder_core, session
from core.auth import get_current_user
from service import push as push_svc
from service import reminder_tick

_ORIG_DB_PATH = session._db_path


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "sessions.db"
    monkeypatch.setattr(session, "_db_path", lambda: path)
    session._init_db()
    mail_store.reset_cache()
    yield path
    monkeypatch.setattr(session, "_db_path", _ORIG_DB_PATH)


@pytest.fixture()
def smtp(monkeypatch):
    """起一个假 SMTP 并把配置指过去。`security=none` 是为了跳过 TLS 握手
    （自签证书会让测试变成在验 ssl 模块，而不是验我们的代码）。"""
    server = FakeSMTP().start()
    monkeypatch.setattr(config, "SMTP_HOST", server.host)
    monkeypatch.setattr(config, "SMTP_PORT", server.port)
    monkeypatch.setattr(config, "SMTP_USER", "bot@qq.com")
    monkeypatch.setattr(config, "SMTP_PASSWORD", "authcode")
    monkeypatch.setattr(config, "SMTP_FROM", "bot@qq.com")
    monkeypatch.setattr(config, "SMTP_FROM_NAME", "苞米")
    monkeypatch.setattr(config, "SMTP_SECURITY", "none")
    monkeypatch.setattr(config, "SMTP_TIMEOUT", 5)
    monkeypatch.setattr(config, "MAIL_ENABLED", True)
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://myzhisui.cn")
    yield server
    server.stop()


@pytest.fixture()
def client(db):
    main.app.dependency_overrides[get_current_user] = lambda: "u1"
    yield TestClient(main.app, raise_server_exceptions=False, follow_redirects=False)
    main.app.dependency_overrides.clear()


# ==================== 一、发信真的发出去了 ====================


class TestTransport:
    def test_real_bytes_reach_the_recipient(self, smtp):
        """端到端：真 socket、真 SMTP 对话、真字节。

        反证：把 `_build` 里的 `msg["To"]` 去掉 → 收件人为空，本用例变红。
        """
        res = mailer.send("me@stu.edu.cn", "交高数作业", "15:00 截止\n第三章习题")
        assert res["ok"] is True, res
        assert smtp.wait_for(1), "服务器一封都没收到"

        msg = smtp.messages[0]
        assert msg["rcpt"] == ["me@stu.edu.cn"]
        assert msg["subject"] == "交高数作业", "中文标题在传输里被改了"
        assert "15:00 截止" in msg["text"]
        # 显示名 + 地址：用户要在邮箱里认出这是「苞米」发的，不是陌生人
        assert "苞米" in msg["from"] and "bot@qq.com" in msg["from"]
        # 缺 Date 的邮件在部分客户端显示 1970 年，用户会直接当垃圾邮件
        assert msg["date"], "Date 头没了"
        assert smtp.credentials == [("bot@qq.com", "authcode")], "登录凭据没送到"

    def test_html_alternative_is_attached(self, smtp):
        res = mailer.send("a@b.com", "主题", "纯文本版", html="<p>富文本版</p>")
        assert res["ok"] and smtp.wait_for(1)
        raw = smtp.messages[0]["raw"].decode("utf-8", "replace")
        assert "multipart/alternative" in raw
        assert "富文本版" in raw

    def test_ssl_is_the_default_transport(self, smtp, monkeypatch):
        """`SMTP_SECURITY=ssl` 必须真的走 SMTP_SSL。

        这条不连真服务器（要证书），而是断言**选对了传输类并传了 timeout**。
        反证：把分支改成永远走 `smtplib.SMTP` → 变红。
        """
        used: dict[str, object] = {}

        class Recorder:
            def __init__(self, host, port, timeout=None, context=None):
                used.update(host=host, port=port, timeout=timeout, context=context)
                raise smtplib.SMTPConnectError(421, b"stop here")

        monkeypatch.setattr(config, "SMTP_SECURITY", "ssl")
        monkeypatch.setattr(smtplib, "SMTP_SSL", Recorder)
        res = mailer.send("a@b.com", "t", "x")

        assert res["ok"] is False and res["retryable"] is True
        assert used["host"] == smtp.host and used["port"] == smtp.port
        # ★ 超时没传 = worker 会被卡住的 SMTP 拖死
        assert used["timeout"] == 5
        assert used["context"] is not None, "没给 TLS 上下文，等于不校验证书"


class TestFailureClassification:
    """三类失败必须分开：改配置能好的、改地址能好的、等一会儿能好的。"""

    def test_auth_failure_is_permanent(self, monkeypatch, smtp):
        monkeypatch.setattr(smtp, "behavior", "auth")
        res = mailer.send("a@b.com", "t", "x")
        assert res["ok"] is False
        assert res["error"] == "auth_failed"
        # 授权码错时重试一万次也一样，标成可重试会让调用方白等
        assert res["retryable"] is False

    def test_recipient_refused_is_permanent(self, smtp, monkeypatch):
        monkeypatch.setattr(smtp, "behavior", "rcpt")
        res = mailer.send("nobody@b.com", "t", "x")
        assert res["error"] == "recipient_refused" and res["retryable"] is False

    def test_network_error_is_retryable(self, smtp, monkeypatch):
        monkeypatch.setattr(smtp, "behavior", "greet")
        res = mailer.send("a@b.com", "t", "x")
        assert res["error"] == "network" and res["retryable"] is True

    def test_timeout_actually_fires(self, smtp, monkeypatch):
        """服务器接受连接但永不应答 —— 这是真实世界里最难查的一种卡死。

        反证：把 `smtplib.SMTP_SSL(..., timeout=...)` 的 timeout 去掉 →
        本用例会挂到 pytest 超时（从"2 秒内返回"变成好几分钟）。
        """
        monkeypatch.setattr(smtp, "behavior", "slow")
        monkeypatch.setattr(config, "SMTP_TIMEOUT", 1)
        t0 = time.monotonic()
        res = mailer.send("a@b.com", "t", "x")
        elapsed = time.monotonic() - t0
        assert res["ok"] is False and res["retryable"] is True
        assert elapsed < 5, f"超时没生效，等了 {elapsed:.1f} 秒"

    def test_unconfigured_returns_quietly(self, monkeypatch):
        monkeypatch.setattr(config, "SMTP_USER", "")
        res = mailer.send("a@b.com", "t", "x")
        assert res == {
            "ok": False,
            "error": "not_configured",
            "retryable": False,
            "detail": "SMTP 未配置",
        }

    def test_full_address_never_reaches_the_log(self, smtp, monkeypatch, caplog):
        """日志会被贴出去排障，地址是个人信息。"""
        monkeypatch.setattr(smtp, "behavior", "rcpt")
        with caplog.at_level("WARNING"):
            res = mailer.send("zhangsan2024@stu.edu.cn", "t", "x")
        assert "zhangsan2024@stu.edu.cn" not in res["detail"]
        assert "zhangsan2024@stu.edu.cn" not in caplog.text
        assert "zh***@stu.edu.cn" in res["detail"]


class TestInputHygiene:
    def test_header_injection_is_rejected(self, smtp):
        """标题里塞换行 = 让用户决定这封信的收件人。必须报错而不是清洗后照发。"""
        res = mailer.send("a@b.com", "正常标题\r\nBcc: evil@x.com", "x")
        assert res["ok"] is False and res["error"] == "invalid"
        assert smtp.messages == [], "注入的邮件居然发出去了"

    def test_multiple_recipients_are_rejected(self, smtp):
        """一次只能发给一个地址，否则任何能设地址的地方都变成群发口子。"""
        for bad in ("a@b.com,c@d.com", "a@b.com; c@d.com", ["a@b.com", "c@d.com"]):
            res = mailer.send(bad, "t", "x")
            assert res["ok"] is False and res["error"] == "invalid", bad
        assert smtp.messages == []

    def test_malformed_address_is_rejected(self, smtp):
        for bad in ("not-an-email", "a@b", "@b.com", "a@.com", "a b@c.com"):
            res = mailer.send(bad, "t", "x")
            assert res["ok"] is False and res["error"] == "invalid", bad


# ==================== 二、地址与验证码 ====================


class TestMailStore:
    def test_normalize_keeps_local_part_case(self):
        """域名统一小写，本地部分保持原样：后者在 RFC 里区分大小写，
        改了可能投不出去（虽然几乎没人这么用）。"""
        assert mail_store.normalize("  Me@QQ.COM ") == "Me@qq.com"
        assert mail_store.normalize("zhang.san+1@stu.edu.cn") == "zhang.san+1@stu.edu.cn"

    def test_normalize_rejects_junk(self):
        for bad in ("", "a", "a@b", "a@b..com", "a@b.com,c@d.com", "a b@c.com"):
            with pytest.raises(mail_store.MailError):
                mail_store.normalize(bad)

    def test_verify_flow(self, db):
        assert mail_store.get("u1") is None
        code = mail_store.start_verification("u1", "me@qq.com")
        state = mail_store.get("u1")
        assert state["verified"] is False and state["pending"] is True
        # 没验证之前派发拿不到地址 —— 这条是「填了不等于生效」的落点
        assert mail_store.verified_address("u1") is None

        assert mail_store.verify("u1", code)["verified"] is True
        assert mail_store.verified_address("u1") == "me@qq.com"

    def test_wrong_code_costs_an_attempt(self, db):
        code = mail_store.start_verification("u1", "me@qq.com")
        wrong = "000000" if code != "000000" else "111111"
        with pytest.raises(mail_store.MailError, match="还能试 4 次"):
            mail_store.verify("u1", wrong)
        assert mail_store.get("u1")["attempts_left"] == 4

    def test_attempts_run_out(self, db):
        """6 位数字只有一百万种，不封顶的话在线爆破是可行的。"""
        code = mail_store.start_verification("u1", "me@qq.com")
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(mail_store.MAX_ATTEMPTS):
            with pytest.raises(mail_store.MailError):
                mail_store.verify("u1", wrong)
        # ★ 正确的码在封顶之后也不能用，否则封顶毫无意义
        with pytest.raises(mail_store.MailError, match="重新发一封"):
            mail_store.verify("u1", code)

    def test_expired_code_is_rejected(self, db, monkeypatch):
        code = mail_store.start_verification("u1", "me@qq.com")
        real = time.time
        monkeypatch.setattr(mail_store.time, "time", lambda: real() + mail_store.CODE_TTL + 1)
        with pytest.raises(mail_store.MailError, match="过期"):
            mail_store.verify("u1", code)

    def test_resend_cooldown(self, db):
        mail_store.start_verification("u1", "me@qq.com")
        with pytest.raises(mail_store.MailCooldown):
            mail_store.start_verification("u1", "me@qq.com")

    def test_daily_quota(self, db, monkeypatch):
        """正常用户最多用 2 次（首次 + 换一个），5 次够到离谱。"""
        real = time.time
        for i in range(mail_store.DAILY_LIMIT):
            # 每次把时钟推过冷却窗口，但不出当天
            monkeypatch.setattr(
                mail_store.time, "time", lambda offset=i: real() + offset * 61
            )
            mail_store.start_verification("u1", f"a{i}@qq.com")
        monkeypatch.setattr(
            mail_store.time, "time", lambda: real() + mail_store.DAILY_LIMIT * 61
        )
        with pytest.raises(mail_store.MailQuota):
            mail_store.start_verification("u1", "last@qq.com")

    def test_changing_address_requires_reverification(self, db):
        """换地址必须重验 —— 否则能把别人的邮箱填进自己账号，替别人收信。"""
        mail_store.verify("u1", mail_store.start_verification("u1", "a@qq.com"))
        assert mail_store.verified_address("u1") == "a@qq.com"

        mail_store.start_verification("u1", "b@qq.com")
        state = mail_store.get("u1")
        assert state["address"] == "b@qq.com"
        assert state["verified"] is False, "换了地址却还是已验证状态"
        assert mail_store.verified_address("u1") is None

    def test_same_address_keeps_verification(self, db):
        """重发同一地址不该把已验证掉回未验证（用户只是点了下「重发」）。"""
        mail_store.verify("u1", mail_store.start_verification("u1", "a@qq.com"))
        mail_store.start_verification("u1", "a@qq.com")
        assert mail_store.get("u1")["verified"] is True

    def test_disabled_blocks_delivery(self, db):
        mail_store.verify("u1", mail_store.start_verification("u1", "a@qq.com"))
        mail_store.set_enabled("u1", False)
        assert mail_store.verified_address("u1") is None
        mail_store.set_enabled("u1", True)
        assert mail_store.verified_address("u1") == "a@qq.com"

    def test_rollback_returns_the_attempt(self, db):
        """发信失败要退回计数与冷却，不能让我们这边的网络抖动罚用户等 60 秒。"""
        mail_store.start_verification("u1", "a@qq.com")
        mail_store.rollback("u1")
        # 冷却没被退回去的话这一句就会抛 MailCooldown，整轮失败
        code = mail_store.start_verification("u1", "a@qq.com")
        # 退回去的那一次被这次抵消，所以今天仍然只算 1 次
        assert mail_store.get("u1")["pending"] is True
        assert mail_store.verify("u1", code)["verified"] is True


# ==================== 三、派发怎么接 ====================


def _due_reminder(owner: str, at: str = "22:30") -> tuple[dict, int]:
    """22:30 是为了避开默认静默时段（23:00–07:00），否则会被闸门拦掉。

    ★ 这里顺手加一道硬闸：**忘了写 `db` 夹具就会往真实的 `data/sessions.db`
    写**，而且表面上一切正常（PASS 照打）。2026-11-11 写这组测试时就踩到了 ——
    四个用例漏写 db，四条假提醒直接进了本地库，事后要去手工删。
    光靠"记得"不行，所以让它在写之前就炸。
    """
    path = str(session._db_path())
    if "pytest" not in path:
        raise AssertionError(f"这个用例漏了 db 夹具，正在往真实库写：{path}")
    item = reminder_core.create(owner, {"title": "交高数作业", "at": at})
    return item, int(item["next_fire_at"])


class TestDispatchFallback:
    def test_no_mail_when_push_works(self, db, smtp, monkeypatch):
        """已经能在手机上看到通知的人，不该再收一封同样的邮件。

        反证：把 `mail_res = None if push_ok else ...` 改成总是调用 → 变红。
        """
        monkeypatch.setattr(
            push_svc,
            "send_to_user",
            lambda uid, p: {"sent": 1, "failed": 0, "gone": 0, "skipped": 0, "items": []},
        )
        mail_store.verify("u1", mail_store.start_verification("u1", "a@qq.com"))
        item, planned = _due_reminder("u1")

        res = reminder_tick.run_one(reminder_tick.JOB_KEY, "u1", planned)
        assert res["mail"] is None
        assert smtp.messages == [], "推送成功了还发了邮件，用户会收到两遍"
        assert reminder_core.fires("u1", item["id"])[0]["detail"] is None

    def test_mail_takes_over_when_push_has_no_device(self, db, smtp, monkeypatch):
        """安卓就是这样：一条订阅都没有（FCM 在国内连不通）。

        反证：把 `ok = push_ok or mail_ok` 改回 `ok = push_ok` → 变红
        （邮件发了，但流水记成 failed）。
        """
        monkeypatch.setattr(
            push_svc,
            "send_to_user",
            lambda uid, p: {"sent": 0, "failed": 0, "gone": 0, "skipped": 0, "items": []},
        )
        mail_store.verify("u1", mail_store.start_verification("u1", "me@qq.com"))
        item, planned = _due_reminder("u1")

        res = reminder_tick.run_one(reminder_tick.JOB_KEY, "u1", planned)
        assert (res["mail"] or {}).get("ok") is True
        assert smtp.wait_for(1)
        msg = smtp.messages[0]
        assert msg["rcpt"] == ["me@qq.com"]
        assert "交高数作业" in msg["subject"]
        assert "交高数作业" in msg["text"]

        fire = reminder_core.fires("u1", item["id"])[0]
        assert fire["status"] == "sent"
        # 记下走的哪条路：否则「他明明收到了信，流水却写着没发」是另一个方向的查不出来
        assert fire["detail"] == "via:mail"

    def test_mail_body_has_time_and_note(self, db, smtp, monkeypatch):
        """邮件不是通知的截断版：通知卡 200 字符是为了锁屏不滚动，邮件没这个约束。"""
        monkeypatch.setattr(
            push_svc,
            "send_to_user",
            lambda uid, p: {"sent": 0, "failed": 0, "gone": 0, "skipped": 0, "items": []},
        )
        mail_store.verify("u1", mail_store.start_verification("u1", "me@qq.com"))
        item = reminder_core.create(
            "u1", {"title": "交高数作业", "note": "第三章习题", "at": "22:30"}
        )
        reminder_tick.run_one(reminder_tick.JOB_KEY, "u1", int(item["next_fire_at"]))
        assert smtp.wait_for(1)
        text = smtp.messages[0]["text"]
        assert "第三章习题" in text, "备注丢了"
        assert "22:30" in text, "少了时刻，用户不知道这是几点的事"
        # 退订路径必须写明，否则这封信在别人眼里就是垃圾邮件
        assert "关掉" in text and "/reminders?tab=settings" in text

    def test_unverified_address_gets_nothing(self, db, smtp, monkeypatch):
        monkeypatch.setattr(
            push_svc,
            "send_to_user",
            lambda uid, p: {"sent": 0, "failed": 0, "gone": 0, "skipped": 0, "items": []},
        )
        mail_store.start_verification("u1", "me@qq.com")  # 只发码，不验证
        item, planned = _due_reminder("u1")

        reminder_tick.run_one(reminder_tick.JOB_KEY, "u1", planned)
        assert smtp.messages == []
        assert reminder_core.fires("u1", item["id"])[0]["detail"] == "no_subscription"

    def test_broken_smtp_does_not_break_the_job(self, db, smtp, monkeypatch):
        """一封邮件发不出去，不能把整轮 job 记成失败 —— 那会触发 worker 重试三次，
        而重试期间这个人的其他提醒照样收不到，日志里却写着「调度失败」。"""
        monkeypatch.setattr(smtp, "behavior", "auth")
        monkeypatch.setattr(
            push_svc,
            "send_to_user",
            lambda uid, p: {"sent": 0, "failed": 0, "gone": 0, "skipped": 0, "items": []},
        )
        mail_store.verify("u1", mail_store.start_verification("u1", "me@qq.com"))
        item, planned = _due_reminder("u1")

        res = reminder_tick.run_one(reminder_tick.JOB_KEY, "u1", planned)
        assert res["fired"] == 1, "异常把整轮吞了"
        # 「两条通道都没成」要和「只有推送失败」区分开：前者要查 SMTP，后者不用
        assert reminder_core.fires("u1", item["id"])[0]["detail"] == (
            "no_subscription+mail:auth_failed"
        )

    def test_unconfigured_smtp_is_not_reported_as_mail_failure(self, db, smtp, monkeypatch):
        """服务端没配发信账号不是"发信失败"，不该混进 detail 让人去查邮箱配置。"""
        monkeypatch.setattr(config, "SMTP_USER", "")
        monkeypatch.setattr(
            push_svc,
            "send_to_user",
            lambda uid, p: {"sent": 0, "failed": 0, "gone": 0, "skipped": 0, "items": []},
        )
        mail_store.verify("u1", mail_store.start_verification("u1", "me@qq.com"))
        item, planned = _due_reminder("u1")
        reminder_tick.run_one(reminder_tick.JOB_KEY, "u1", planned)
        assert reminder_core.fires("u1", item["id"])[0]["detail"] == "no_subscription"


# ==================== 四、HTTP 契约 ====================


class TestMailApi:
    def test_status_when_server_has_no_account(self, client, monkeypatch):
        monkeypatch.setattr(config, "SMTP_USER", "")
        body = client.get("/api/mail").json()
        assert body["ready"] is False
        # 没配发信账号时不能报一个地址出来，前端要靠 ready 把输入框收起来
        assert body["address"] is None

    def test_sending_code_requires_a_working_server(self, client, monkeypatch):
        monkeypatch.setattr(config, "SMTP_USER", "")
        res = client.post("/api/mail", json={"address": "a@b.com"})
        assert res.status_code == 400
        assert "服务端还没配发信账号" in res.json()["detail"]

    def test_bad_address_is_a_400_with_chinese(self, client, smtp):
        res = client.post("/api/mail", json={"address": "not-an-email"})
        assert res.status_code == 400
        assert "邮箱格式不对" in res.json()["detail"]

    def test_code_reaches_the_mailbox(self, client, smtp):
        res = client.post("/api/mail", json={"address": "me@qq.com"})
        assert res.status_code == 200, res.text
        assert smtp.wait_for(1)
        assert smtp.messages[0]["rcpt"] == ["me@qq.com"]
        # 验证码必须真在信里，不然用户拿不到
        assert any(ch.isdigit() for ch in smtp.messages[0]["text"])
        assert "10 分钟" in smtp.messages[0]["text"]

    def test_verify_with_the_code_from_the_mail(self, client, smtp):
        client.post("/api/mail", json={"address": "me@qq.com"})
        assert smtp.wait_for(1)
        code = "".join(ch for ch in smtp.messages[0]["text"] if ch.isdigit())[:6]

        res = client.post("/api/mail/verify", json={"code": code})
        assert res.status_code == 200, res.text
        assert res.json()["verified"] is True
        assert res.json()["address"] == "me@qq.com"

    def test_wrong_code_says_how_many_left(self, client, smtp):
        client.post("/api/mail", json={"address": "me@qq.com"})
        res = client.post("/api/mail/verify", json={"code": "000000"})
        assert res.status_code == 400
        assert "还能试" in res.json()["detail"]

    def test_toggle_and_remove(self, client, smtp):
        client.post("/api/mail", json={"address": "me@qq.com"})
        assert smtp.wait_for(1)
        code = "".join(ch for ch in smtp.messages[0]["text"] if ch.isdigit())[:6]
        client.post("/api/mail/verify", json={"code": code})

        res = client.patch("/api/mail", json={"enabled": False})
        assert res.status_code == 200 and res.json()["enabled"] is False
        assert res.json()["verified"] is True, "关掉不该把验证状态一起清了"

        res = client.delete("/api/mail")
        assert res.json()["address"] is None and res.json()["verified"] is False

    def test_requires_login(self, db):
        """去掉依赖覆盖，确认这组端点不是裸奔的。"""
        main.app.dependency_overrides.clear()
        anon = TestClient(main.app, raise_server_exceptions=False)
        assert anon.get("/api/mail").status_code == 401
