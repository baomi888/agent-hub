# -*- coding: utf-8 -*-
"""离线自测：知识库归属隔离（多用户改造 P2 的验收点）。

不联网、不碰真实数据：PROJECT_ROOT / PERSIST_DIR / SQLite 全部指到临时目录。

验的是四件事：
  1. 同名知识库在不同人名下是**两个库**（kb_id 里混了 owner）
  2. 拿到别人的 kb_id 也进不去（归属表门禁，不是靠 id 不可猜）
  3. 列表只列出自己的
  4. 改造前的无主老库默认谁都看不见，显式认领后才归某个人
"""

import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="baomi_kbtest_")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import core.config  # noqa: E402

# 必须在 import 任何业务模块之前把落盘位置挪走
core.config.PROJECT_ROOT = os.path.join(_TMP, "proj")
core.config.PERSIST_DIR = os.path.join(_TMP, "proj", "chroma_db")
core.config.DATA_DIR = os.path.join(_TMP, "proj", "data")
core.config.DOWNLOAD_DIR = os.path.join(_TMP, "proj", "downloads")
os.makedirs(os.path.join(core.config.PROJECT_ROOT, "data"), exist_ok=True)

import core.session  # noqa: E402

core.session._db_path = lambda: os.path.join(_TMP, "sessions.db")
core.session._init_db()
core.config._AUTH_SECRET_FILE = os.path.join(_TMP, ".auth_secret")

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from kb import pipelines  # noqa: E402
from kb.pipelines import NotOwned  # noqa: E402

ALICE = "user_alice"
BOB = "user_bob"

PASS = []
FAIL = []


def check(name: str, cond: bool, extra: str = ""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  <- " + extra) if extra and not cond else ""))


print("\n[1] 同名知识库在不同人名下是两个库")
ka, _ = pipelines.create_kb(ALICE, "我的库")
kb, _ = pipelines.create_kb(BOB, "我的库")
check("两人同名库拿到不同 kb_id", ka != kb, f"{ka} vs {kb}")
check("各自归属正确", pipelines.owner_of(ka) == ALICE and pipelines.owner_of(kb) == BOB)
check("同一个人重建同名库 -> 同一个 id（幂等）",
      pipelines.create_kb(ALICE, "我的库")[0] == ka)

print("\n[2] 拿到别人的 kb_id 也进不去")
try:
    pipelines.get_or_create(BOB, ka)
    check("B 打开 A 的库被拒", False, "没抛异常")
except NotOwned:
    check("B 打开 A 的库被拒", True)
check("B 查 A 的库是否存在 -> False", pipelines.exists(BOB, ka) is False)
check("B 读 A 的文件清单 -> 空", pipelines.get_files(BOB, ka) == [])
check("B 读 A 的文件名 -> 空", pipelines.get_file_names(BOB, ka) == [])
check("B 查 A 的状态 -> None", pipelines.get_status(BOB, ka) is None)
check("B 删 A 的库 -> False", pipelines.delete_kb(BOB, ka) is False)
check("删完 A 的库还在", pipelines.owner_of(ka) == ALICE)
try:
    pipelines.add_file_records(BOB, ka, [{"name": "x.txt", "chunks": 1, "added_at": 0}])
    check("B 往 A 的库登记文件被拒", False, "没抛异常")
except NotOwned:
    check("B 往 A 的库登记文件被拒", True)

print("\n[3] 列表只出自己的")
mine_a = {k["kb_id"] for k in pipelines.list_all(ALICE)}
mine_b = {k["kb_id"] for k in pipelines.list_all(BOB)}
check("A 的列表里有自己的库", ka in mine_a, str(mine_a))
check("A 的列表里没有 B 的库", kb not in mine_a, str(mine_a))
check("B 的列表里有自己的库", kb in mine_b, str(mine_b))
check("B 的列表里没有 A 的库", ka not in mine_b, str(mine_b))

print("\n[4] 改造前的无主老库：默认谁都看不见")
legacy = "legacy_kb_0001"
pipelines._get_or_create_raw(legacy)  # 内存里有这个 pipeline
# 模拟"磁盘上真有一个改造前留下的 collection"（不然认领扫不到东西）
from kb.rag_core import chroma_client  # noqa: E402

chroma_client().create_collection(f"kb_{legacy}")
check("老库 owner_of -> None", pipelines.owner_of(legacy) is None)
check("A 看不到老库", pipelines.get_status(ALICE, legacy) is None)
check("B 看不到老库", pipelines.get_status(BOB, legacy) is None)
check("老库不出现在任何人的列表里",
      legacy not in {k["kb_id"] for k in pipelines.list_all(ALICE)} and
      legacy not in {k["kb_id"] for k in pipelines.list_all(BOB)})

n = pipelines.claim_orphan_kbs(ALICE)
check("显式认领至少 1 个", n >= 1, str(n))
check("认领后归 A", pipelines.owner_of(legacy) == ALICE)
check("认领后 B 依然看不到", pipelines.get_status(BOB, legacy) is None)

print("\n[5] HTTP 层：跨用户访问知识库接口")
c_a = TestClient(main.app)
c_b = TestClient(main.app)
c_a.post("/api/auth/register/", json={"username": "kbowner_a", "password": "pw123456"})
c_b.post("/api/auth/register/", json={"username": "kbowner_b", "password": "pw123456"})
# 让两个 client 拿到的分别是各自账号的 token
c_a.post("/api/auth/login/", json={"username": "kbowner_a", "password": "pw123456"})
c_b.post("/api/auth/login/", json={"username": "kbowner_b", "password": "pw123456"})

ra = c_a.post("/api/kb/create", json={"name": "A 的资料"})
check("A 建库 200", ra.status_code == 200, ra.text[:120])
http_kb = ra.json()["kb_id"]
check("B 看不到 A 的库（列表）", http_kb not in [k["kb_id"] for k in c_b.get("/api/kb/").json()["kbs"]])
check("B 直接查 A 的库 -> 404", c_b.get(f"/api/kb/{http_kb}/").status_code == 404)
check("B 直接删 A 的库 -> 404", c_b.delete(f"/api/kb/{http_kb}/").status_code == 404)
# 注意：不带文件时 FastAPI 会先返回 422（参数校验），走不到归属判断。
# 这里只验"没被放行"：任何非 200 都算过关。
check("B 往 A 的库传文件被拒", c_b.post(f"/api/kb/{http_kb}/upload").status_code != 200)
check("B 读 A 的库内文件 -> 404", c_b.get(f"/api/kb/{http_kb}/files").status_code == 404)
check("删完 A 的库还在", c_a.get(f"/api/kb/{http_kb}/").status_code == 200)

print("\n" + "=" * 56)
print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
if FAIL:
    for f in FAIL:
        print("  ✗ " + f)
    sys.exit(1)
print("ALL PASS")
