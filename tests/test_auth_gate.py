# -*- coding: utf-8 -*-
"""离线自测：认证门是否真的关上了。

不联网、不碰真实数据库。做法是在 import 业务模块之前，
把 SQLite 路径和密钥文件挪到临时目录，跑完即弃。

验的是三件事（对应"多用户改造"P1 的验收点）：
  1. 不带 Cookie 访问任何业务端点 → 401（默认拒绝，而不是默认放行）
  2. 注册/登录能拿到 HttpOnly Cookie，且 JS 读不到（httponly=True）
  3. 伪造 / 过期 / 被篡改的 token 一律无效
"""

import os
import sys
import tempfile
import time

# ---- 必须在 import 业务模块之前把落盘位置挪走 ----
_TMP = tempfile.mkdtemp(prefix="baomi_auth_test_")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.config  # noqa: E402  先让 chdir / load_dotenv 跑完
import core.session  # noqa: E402

core.session._db_path = lambda: os.path.join(_TMP, "sessions.db")
core.session._init_db()  # 在临时库里把 conversations / messages 建出来
core.config._AUTH_SECRET_FILE = os.path.join(_TMP, ".auth_secret")
core.config.ALLOW_SIGNUP = True

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from core import account  # noqa: E402
from core.auth import create_token, decode_token  # noqa: E402

PASS = []
FAIL = []


def check(name: str, cond: bool, extra: str = ""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  <- " + extra) if extra and not cond else ""))


client = TestClient(main.app)  # 不用 with：跳过 lifespan，避免预热 Chroma

print("\n[1] 默认拒绝：未登录访问业务端点")
for path in ["/api/sessions/", "/api/kb/", "/api/feedback/x/", "/api/geo/locate"]:
    r = client.get(path)
    check(f"{path} 未登录 -> 401", r.status_code == 401, f"实际 {r.status_code}")

print("\n[2] 公开端点仍然可访问")
check("/api/auth/config/ -> 200", client.get("/api/auth/config/").status_code == 200)
check("/health -> 200", client.get("/health").status_code == 200)
check("/api/config/defaults -> 200", client.get("/api/config/defaults").status_code == 200)

print("\n[3] 注册 + 登录")
r = client.post("/api/auth/register/", json={"username": "alice", "password": "secret123"})
check("注册成功 200", r.status_code == 200, r.text[:120])
sc = r.headers.get("set-cookie", "")
check("下发了 Cookie", "baomi_session=" in sc, sc[:80])
check("Cookie 带 HttpOnly", "httponly" in sc.lower(), sc[:120])
check("Cookie 带 SameSite", "samesite" in sc.lower(), sc[:120])

r2 = client.post("/api/auth/register/", json={"username": "alice", "password": "secret123"})
check("重名注册 -> 409", r2.status_code == 409, f"实际 {r2.status_code}")

r3 = client.post("/api/auth/register/", json={"username": "ALICE", "password": "secret123"})
check("大小写重名 -> 409（COLLATE NOCASE）", r3.status_code == 409, f"实际 {r3.status_code}")

r4 = client.post("/api/auth/register/", json={"username": "ab", "password": "secret123"})
check("用户名过短 -> 400", r4.status_code == 400, f"实际 {r4.status_code}")

r5 = client.post("/api/auth/register/", json={"username": "bob", "password": "123"})
check("密码过短 -> 400", r5.status_code == 400, f"实际 {r5.status_code}")

r6 = client.post("/api/auth/login/", json={"username": "alice", "password": "wrongpass"})
check("密码错 -> 401", r6.status_code == 401, f"实际 {r6.status_code}")
r7 = client.post("/api/auth/login/", json={"username": "nobody", "password": "wrongpass"})
check("用户不存在 -> 同 401（不泄露是否存在）", r7.status_code == 401, f"实际 {r7.status_code}")

print("\n[4] 登录后能进业务端点")
r8 = client.post("/api/auth/login/", json={"username": "alice", "password": "secret123"})
check("登录成功 200", r8.status_code == 200, r8.text[:120])
r9 = client.get("/api/sessions/")
check("带 Cookie 访问会话列表 -> 200", r9.status_code == 200, f"实际 {r9.status_code}")
r10 = client.get("/api/auth/me/")
check("me 返回用户名", r10.status_code == 200 and r10.json().get("user", {}).get("username") == "alice",
      r10.text[:120])
check("me 返回配额概览", isinstance(r10.json().get("quota"), dict), r10.text[:120])

print("\n[5] 令牌防伪造")
uid = account.get_user_by_username("alice")["id"]
check("正常 token 可解出 uid", decode_token(create_token(uid)) == uid)
check("空 token -> None", decode_token("") is None)
check("瞎编 token -> None", decode_token("a.b.c") is None)
good = create_token(uid)
check("篡改载荷 -> None", decode_token(good[:-3] + "AAA") is None)
check("过期 token -> None", decode_token(create_token(uid, ttl_days=-1)) is None)

# 自签一个"不存在的用户"：签名是自洽的，所以能吃过令牌校验，
# 但 /me 会查库发现没这个人 —— 过期未清理的账号也走这条路径，正好一并验掉
forged = create_token("someone-else")
c2 = TestClient(main.app)
c2.cookies.set("baomi_session", forged)
rm = c2.get("/api/auth/me/")
check("签名自洽但用户不存在 -> 401", rm.status_code == 401, f"实际 {rm.status_code}")
check("拿不到 alice 的用户名", "alice" not in rm.text, rm.text[:120])

print("\n[6] 退出登录")
r11 = client.post("/api/auth/logout/")
check("logout -> 200", r11.status_code == 200)
check("logout 后 Cookie 被清", "baomi_session=;" in r11.headers.get("set-cookie", "").replace('"', ''),
      r11.headers.get("set-cookie", "")[:80])
r12 = client.get("/api/sessions/")
check("logout 后再访问 -> 401", r12.status_code == 401, f"实际 {r12.status_code}")

print("\n[7] 配额与限流")
from core import quota  # noqa: E402

quota.bump_usage(uid, "ask", 3)
check("每日用量累加", quota.usage_today(uid, "ask") == 3, str(quota.usage_today(uid, "ask")))
cfg_before = core.config.DAILY_ASK_LIMIT
core.config.DAILY_ASK_LIMIT = 5
try:
    quota.consume(uid, "ask", 10)
    check("超日限额抛 QuotaExceeded", False, "没抛异常")
except quota.QuotaExceeded:
    check("超日限额抛 QuotaExceeded", True)
check("超限不计入（仍为 3）", quota.usage_today(uid, "ask") == 3, str(quota.usage_today(uid, "ask")))
core.config.DAILY_ASK_LIMIT = cfg_before

core.config.RATE_PER_MINUTE = 3
try:
    for _ in range(5):
        quota.hit("tester")
    check("超每分钟上限抛 RateLimited", False, "没抛异常")
except quota.RateLimited as e:
    check("超每分钟上限抛 RateLimited", True)
    check("给出重试秒数", e.retry_after > 0, str(e.retry_after))

print("\n" + "=" * 56)
print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
if FAIL:
    for f in FAIL:
        print("  ✗ " + f)
    sys.exit(1)
print("ALL PASS")
