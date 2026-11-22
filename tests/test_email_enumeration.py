"""账号枚举反证：verify 阶段对已注册/未注册邮箱必须逐字同形。

send 阶段已把响应字段和文案抹平；若 verify 还拿 request_id 反查账号，
"验证请求不存在"和"验证码不正确"两句话就够了 —— 无需登录即可批量判断
某个邮箱有没有知穗账号。这里用真实接口 + 临时库 + 假发信实测两条路径。
"""

import sqlite3

from test_email_registration import env as env

import main
from api.security_email import get_service
from core import account


def _probe(cli, send_path, verify_path, address):
    sent = cli.post(send_path, json={"address": address})
    assert sent.status_code == 200, sent.text
    checked = cli.post(
        verify_path, json={"request_id": sent.json()["request_id"], "code": "000000"}
    )
    return checked.status_code, checked.json().get("detail")


def test_verify_does_not_reveal_account_existence(env):
    service, cli, messages, clock = env
    user = account.create_user("enum_target", "Enum_target_9")
    with sqlite3.connect(service.store.path) as db:
        db.execute(
            "INSERT INTO security_emails VALUES (?,?,?,1)",
            (user["id"], "known@example.com", clock[0]),
        )
    main.app.dependency_overrides[get_service] = lambda: service
    try:
        paths = (
            ("/api/auth/password/reset/email/send", "/api/auth/password/reset/email/verify"),
            ("/api/auth/login/email/send", "/api/auth/login/email"),
        )
        for send_path, verify_path in paths:
            # 同一地址 60s 冷却跨路径共用：换路径前清掉隔离库里的冷却记录。
            with sqlite3.connect(service.store.path) as db:
                db.execute("DELETE FROM verification_cooldowns")
                db.execute("DELETE FROM verification_rate_limits")
            known = _probe(cli, send_path, verify_path, "known@example.com")
            unknown = _probe(cli, send_path, verify_path, "unknown@example.com")
            # 防止 None == None 这种"绿着守空气"：必须真的回了 400 + 一句文案。
            assert known[0] == 400 and known[1], (send_path, known)
            assert known == unknown, (send_path, known, unknown)
    finally:
        main.app.dependency_overrides.pop(get_service, None)
