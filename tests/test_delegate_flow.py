"""每日委托 HTTP 全链路回归（离线：TestClient + 临时库，不联网、不碰真实 data/）。

本轮修复的是什么（"异常"的根因）
--------------------------------
前端 `delegate/page.tsx::submit` 提交的 body 用的是 **`instruction` / `session_id`**，
而后端 `api/delegate.py::DelegationIn` 只认 **`prompt` / `deliver_sid`**（`deliver_sid` 还是必填）。
于是「创建委托」每一次都被 FastAPI 挡在 422：

    {"detail": [{"type": "missing", "loc": ["body", "deliver_sid"], "msg": "Field required"}]}

前端 `api.ts::httpError` 当时把 `body.detail`（**数组**）直接丢给 `new Error(...)`，
JS 把数组转成字符串 ⇒ 页面上就是一个 **"[object Object]"** 气泡 —— 用户既看不出
错在哪个字段，也看不出这是校验失败。

本文件钉住修复后的端到端行为（创建→列表→编辑→删除，外加边界）：
  1. 用前端的 body 形状创建 → 201，响应同时带两套键名（加法兼容，不打断任一调用方）
  2. 前端历史的 `kind=study` 被归一成规范值 `study_plan`（规范/02 第 9 节）
  3. 「默认会话」（session_id=null）落到最近会话，而不是存一条**永不投递**的委托
  4. 同名 → 409；越界 max_chars → 仍是 422 且 detail 是数组（前端负责渲染成人话）
  5. PATCH 改 instruction / session_id **真的落库**（此前被静默丢弃）
  6. 删除 → 从列表消失

跑法：
  python -m pytest -q tests/test_delegate_flow.py
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.config  # noqa: E402  先让 chdir / load_dotenv 跑完
import core.session  # noqa: E402
from core import (
    account,  # noqa: E402
    quota,  # noqa: E402
)
from service import daily  # noqa: E402

# 每台机器、每个用例一个独立临时目录：绝不写真实 data/
_TMP = tempfile.mkdtemp(prefix="baomi_delegate_test_")
_SEQ = [0]


def _next(tag: str) -> str:
    _SEQ[0] += 1
    return os.path.join(_TMP, f"{tag}_{_SEQ[0]}.db")


class TestDelegateFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        import main

        cls.main = main
        cls.TestClient = TestClient

    def setUp(self):
        self._orig = {
            "session_path": core.session._db_path,
            "secret": core.config._AUTH_SECRET_FILE,
            "signup": core.config.ALLOW_SIGNUP,
            "rate": core.config.RATE_PER_MINUTE,
        }

        self.session_db = _next("sessions")
        core.session._db_path = lambda: self.session_db
        core.session._init_db()
        # users / usage 表建在临时库里：不建的话第一次注册会撞 "no such table"
        account._ensure_tables()
        quota._ensure_tables()
        # daily 的 delegations 表用模块级 _daily_tables_ready 做"只建一次"短路，
        # 每个用例换库后必须复位，否则第二个用例会撞 "no such table: delegations"
        daily._daily_tables_ready = False

        core.config._AUTH_SECRET_FILE = _next("secret")
        core.config.ALLOW_SIGNUP = True
        core.config.RATE_PER_MINUTE = 0  # 端点测试不受每分钟限流干扰

        self.client = self.TestClient(self.main.app)
        r = self.client.post(
            "/api/auth/register/", json={"username": "deleg_user", "password": "secret123"}
        )
        assert r.status_code == 200, r.text
        # 建一个会话，作为正常投递目标
        c = self.client.post("/api/sessions/", json={"title": "用户询问建议"})
        assert c.status_code == 200, c.text
        self.sid = c.json()["id"]

    def tearDown(self):
        core.session._db_path = self._orig["session_path"]
        core.config._AUTH_SECRET_FILE = self._orig["secret"]
        core.config.ALLOW_SIGNUP = self._orig["signup"]
        core.config.RATE_PER_MINUTE = self._orig["rate"]

    # ---- 工具 ----

    def _frontend_body(self, **over) -> dict:
        """照抄前端 submit 里拼的 body（含 session_id / instruction 这套命名）。"""
        body = {
            "name": "每日复盘",
            "kind": "study_plan",
            "schedule": {
                "freq": "daily",
                "at": "21:00",
                "weekday": None,
                "every_minutes": None,
                "tz": "Asia/Shanghai",
            },
            "session_id": self.sid,
            "kb_id": None,
            "instruction": "给我每天都生成一次复盘",
            "tone": "neutral",
            "max_chars": 600,
            "enabled": True,
        }
        body.update(over)
        return body

    def _create(self, **over) -> dict:
        r = self.client.post("/api/delegate", json=self._frontend_body(**over))
        assert r.status_code == 201, r.text
        return r.json()

    # ---- 用例 ----

    def test_create_with_frontend_body_returns_both_key_sets(self):
        """前端 body 直接 201；响应两套键名并存（后端"加法兼容"的既有约定）。"""
        out = self._create()
        assert out["delegation_id"] == out["id"]
        assert out["session_id"] == self.sid and out["deliver_sid"] == self.sid
        assert out["instruction"] == "给我每天都生成一次复盘"
        assert out["prompt"] == out["instruction"]
        assert out["kind"] == "study_plan"
        assert out["enabled"] is True

    def test_legacy_kind_alias_is_normalized(self):
        """前端历史写法 kind=study 不再是 400，归一成规范值 study_plan。"""
        out = self._create(name="旧写法", kind="study")
        assert out["kind"] == "study_plan"

    def test_default_session_falls_back_to_latest_conversation(self):
        """「默认会话」发 null → 落到最近更新的会话，而不是永不投递。"""
        out = self._create(name="默认会话委托", session_id=None)
        assert out["session_id"] == self.sid

    def test_foreign_session_is_404(self):
        """投递目标不是本人的会话 → 404（不泄露存在性）。"""
        r = self.client.post("/api/delegate", json=self._frontend_body(session_id="not-my-sid"))
        assert r.status_code == 404

    def test_duplicate_name_returns_409(self):
        self._create()
        r = self.client.post("/api/delegate", json=self._frontend_body())
        assert r.status_code == 409
        assert r.json()["detail"] == "同名委托已存在"

    def test_out_of_range_max_chars_still_422_and_detail_is_list(self):
        """越界仍是 422 —— 但 detail 是**数组**；渲染成人话是前端 api.ts 的职责。"""
        r = self.client.post("/api/delegate", json=self._frontend_body(max_chars=100))
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert isinstance(detail, list) and detail[0]["loc"][-1] == "max_chars"

    def test_patch_updates_instruction_and_session(self):
        """编辑指令 / 投递会话必须真的落库（此前被静默丢弃）。"""
        did = self._create()["id"]
        c2 = self.client.post("/api/sessions/", json={"title": "另一个会话"}).json()["id"]
        r = self.client.patch(
            f"/api/delegate/{did}",
            json={"instruction": "改成每天写复盘", "session_id": c2},
        )
        assert r.status_code == 200, r.text
        got = self.client.get(f"/api/delegate/{did}").json()
        assert got["instruction"] == "改成每天写复盘"
        assert got["session_id"] == c2

    def test_list_and_delete(self):
        did = self._create()["id"]
        items = self.client.get("/api/delegate").json()
        assert [d["id"] for d in items] == [did]
        assert self.client.delete(f"/api/delegate/{did}").status_code == 200
        assert self.client.get("/api/delegate").json() == []


if __name__ == "__main__":
    unittest.main(verbosity=2)
