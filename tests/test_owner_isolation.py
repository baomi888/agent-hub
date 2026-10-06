# -*- coding: utf-8 -*-
"""离线自测：会话归属隔离（多用户改造 P2 的验收点）。

验的是"两个账号互相看不见"这件事在**数据层**成立，而不是靠前端藏起来：
直接 SQLite 插入 / 直接调 core.session，绕开所有 HTTP 层的偶然正确。

不联网、不碰真实数据库——SQLite 与密钥文件都在临时目录里。
"""

import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="baomi_owner_test_")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.config  # noqa: E402
import core.session  # noqa: E402

core.session._db_path = lambda: os.path.join(_TMP, "sessions.db")
core.session._init_db()
core.config._AUTH_SECRET_FILE = os.path.join(_TMP, ".auth_secret")

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from core import session as store  # noqa: E402
from core.session import NotOwned  # noqa: E402

PASS = []
FAIL = []


def check(name: str, cond: bool, extra: str = ""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  <- " + extra) if extra and not cond else ""))


def _login(username: str, password: str = "pw123456") -> TestClient:
    c = TestClient(main.app)
    r = c.post("/api/auth/register/", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c


print("\n[1] 两个账号各自建会话")
alice = _login("alice")
bob = _login("bob")
uid_a = alice.post("/api/auth/login/", json={"username": "alice", "password": "pw123456"}).json()["id"]
uid_b = bob.post("/api/auth/login/", json={"username": "bob", "password": "pw123456"}).json()["id"]

sa = alice.post("/api/sessions/", json={"title": "A 的会话"}).json()
sb = bob.post("/api/sessions/", json={"title": "B 的会话"}).json()
check("两人各自建成功", sa["id"] and sb["id"] and sa["id"] != sb["id"])

la = alice.get("/api/sessions/").json()["sessions"]
lb = bob.get("/api/sessions/").json()["sessions"]
check("A 只看得到自己 1 条", len(la) == 1 and la[0]["id"] == sa["id"], str([s["id"] for s in la]))
check("B 只看得到自己 1 条", len(lb) == 1 and lb[0]["id"] == sb["id"], str([s["id"] for s in lb]))

print("\n[2] 跨界读取")
check("B 读 A 的会话 -> 404", bob.get(f"/api/sessions/{sa['id']}/").status_code == 404)
check("B 读 A 的消息列表 -> 404", bob.get(f"/api/feedback/{sa['id']}/").status_code == 404)
check("B 改 A 的标题 -> 404", bob.patch(f"/api/sessions/{sa['id']}/", json={"title": "被改了"}).status_code == 404)
check("B 删 A 的会话 -> 404", bob.delete(f"/api/sessions/{sa['id']}/").status_code == 404)
still = alice.get("/api/sessions/").json()["sessions"]
check("A 的会话没被删掉", len(still) == 1 and still[0]["title"] == "A 的会话", str(still))

print("\n[3] 跨会话写消息 / 反馈")
store.add_message(uid_a, sa["id"], "user", "hello")
check("A 能写自己的会话", store.count_messages(uid_a, sa["id"]) == 1)
try:
    store.add_message(uid_b, sa["id"], "user", "越权写入")
    check("B 往 A 的会话写消息被拒", False, "没抛异常")
except NotOwned:
    check("B 往 A 的会话写消息被拒", True)
check("A 的消息数没变", store.count_messages(uid_a, sa["id"]) == 1)

check("B 给 A 的消息点赞被拒", store.add_feedback(uid_b, sa["id"], 1, "up") is False)
check("B 读 A 的会话条文 -> 空", store.get_messages(uid_b, sa["id"]) == [])
check("B 查 A 的消息 id -> 空", store.find_turn_ids(uid_b, sa["id"], 1) == [])
check("B 删 A 的消息 -> False", store.delete_messages(uid_b, sa["id"], [1]) is False)

print("\n[4] 历史遗留数据：默认谁都看不见")
with core.session._get_conn() as conn:
    conn.execute(
        "INSERT INTO conversations (id, owner_id, title, created_at, updated_at) "
        "VALUES (?, NULL, ?, ?, ?)",
        ("legacy01", "改造前的旧会话", 1, 1),
    )
check("旧会话对 A 不可见", store.get_conversation(uid_a, "legacy01") is None)
check("旧会话对 B 不可见", store.get_conversation(uid_b, "legacy01") is None)
check("不出现在任何人的列表里",
      "legacy01" not in [s["id"] for s in store.list_conversations(uid_a)] and
      "legacy01" not in [s["id"] for s in store.list_conversations(uid_b)])

n = store.claim_orphan_conversations(uid_a)
check("显式认领返回条数 1", n == 1, str(n))
check("认领后 A 可见", store.get_conversation(uid_a, "legacy01") is not None)
check("认领后 B 仍不可见", store.get_conversation(uid_b, "legacy01") is None)

print("\n" + "=" * 56)
print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
if FAIL:
    for f in FAIL:
        print("  ✗ " + f)
    sys.exit(1)
print("ALL PASS")
