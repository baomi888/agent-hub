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
import time
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
        # 注册按用户名分桶限 10 次/分（auth:deleg_user），本文件所有用例复用同一用户名，
        # 跨用例累积会撑爆限流 → setUp 每次清空进程内滑动窗口，恢复隔离
        quota._HITS.clear()

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

    def test_patch_preserves_next_run_and_created_at(self):
        """编辑/切开关不能把 next_run_at 重置成"立刻到期"，也不能改写 created_at。

        此前 save_delegation 硬编码 next_run_at=None，导致 PATCH 一次（或切一下开关）
        就触发一次立即派发——白白烧一次 LLM + 推一条消息（见
        docs/前后端可更新点_2026-10-19.md 后端 #1）。
        """
        d = self._create()
        did = d["id"]
        future = int(time.time()) + 86400
        daily._bump_next_run(did, future)  # 模拟一次定时投递后"下次=明天"
        # 编辑指令 + 切 enabled（此前会触发"改一下就多投一次"）
        r = self.client.patch(
            f"/api/delegate/{did}",
            json={"instruction": "换个说法，仍每天复盘", "enabled": True},
        )
        assert r.status_code == 200, r.text
        # next_run_at 必须保留为"明天"，而不是被重置成 NULL（立刻到期）
        self.assertEqual(r.json()["next_run_at"], future)
        # created_at 不被改写成今天
        got = self.client.get(f"/api/delegate/{did}").json()
        self.assertEqual(got["created_at"], d["created_at"])

    def test_compute_next_run_covers_all_freqs(self):
        """compute_next_run 按 freq 真算下次时间（修『每周/间隔是假功能』的核心）。"""
        after = time.mktime(time.struct_time((2026, 10, 19, 14, 0, 0, 0, 0, -1)))

        # daily at 09:00：今天 09:00 已过 → 次日 09:00（时刻对齐 at 且严格晚于 after）
        nxt = daily.compute_next_run({"freq": "daily", "at": "09:00"}, int(after))
        lt = time.localtime(nxt)
        self.assertEqual((lt.tm_hour, lt.tm_min), (9, 0))
        self.assertGreater(nxt, after)

        # at 在当天未来：今天 21:00 > 14:00 → 今天 21:00（< 1 天）
        nxt = daily.compute_next_run({"freq": "daily", "at": "21:00"}, int(after))
        self.assertTrue(0 < nxt - after < 86400)
        self.assertEqual((time.localtime(nxt).tm_hour, time.localtime(nxt).tm_min), (21, 0))

        # weekdays at 09:00：落在工作日(周一..周五)、时刻对齐
        nxt = daily.compute_next_run({"freq": "weekdays", "at": "09:00"}, int(after))
        lt = time.localtime(nxt)
        self.assertEqual((lt.tm_hour, lt.tm_min), (9, 0))
        self.assertLessEqual(lt.tm_wday, 4)

        # weekly weekday=0(周一) at 09:00：输出星期必须等于请求值、时刻对齐、是下一个(≤7天)
        nxt = daily.compute_next_run({"freq": "weekly", "at": "09:00", "weekday": 0}, int(after))
        lt = time.localtime(nxt)
        self.assertEqual((lt.tm_hour, lt.tm_min), (9, 0))
        self.assertEqual(lt.tm_wday, 0)
        self.assertTrue(0 < nxt - after <= 7 * 86400 + 60)
        # 步进性：再算一次必须更晚，证明是「下一个」而非「今天已过的那次」
        self.assertGreater(daily.compute_next_run({"freq": "weekly", "at": "09:00", "weekday": 0}, nxt), nxt)

        # interval every_minutes=60：now + 3600，与 at 无关
        nxt = daily.compute_next_run({"freq": "interval", "every_minutes": 60}, int(after))
        self.assertEqual(nxt, int(after) + 3600)

        # 兜底：无 schedule → after + 86400（维持旧行为，不破坏既有委托）
        self.assertEqual(daily.compute_next_run(None, int(after)), int(after) + 86400)

    def test_compute_next_run_accepts_json_string(self):
        """schedule 是 JSON 文本时也要按 freq 真算，不能掉进 +86400 兜底。

        库里的 delegations.schedule_json 存的是 json.dumps 的文本，_load_delegation
        直接 dict(row) 取出来就是字符串。只判断 dict 的话，_dispatch_one 每次都会
        走兜底，weekly / interval 全部退化成「每天一次」——实测：每天 21:00 的委托
        在 15:45 手动跑一次，next_run_at 变成次日 15:45。
        """
        after = int(time.mktime(time.struct_time((2026, 10, 19, 14, 0, 0, 0, 0, -1))))
        as_dict = daily.compute_next_run({"freq": "daily", "at": "21:00"}, after)
        as_json = daily.compute_next_run('{"freq": "daily", "at": "21:00"}', after)
        self.assertEqual(as_json, as_dict)
        # 反向断言：不是「+86400 兜底」那条路
        self.assertNotEqual(as_json, after + 86400)

        # weekly 同样要认字符串，且落在指定星期
        nxt = daily.compute_next_run('{"freq": "weekly", "at": "09:00", "weekday": 0}', after)
        self.assertEqual(time.localtime(nxt).tm_wday, 0)
        self.assertLess(nxt - after, 8 * 86400)

        # 坏 JSON 仍然走兜底，不抛异常（worker 不能因为一条脏数据停摆）
        self.assertEqual(daily.compute_next_run("{不是 json", after), after + 86400)

    def test_dispatch_one_reads_schedule_from_db_and_reschedules(self):
        """★ 端到端：worker 派发一次后，下次触发必须按频次算，不能变成「执行时刻 +24h」。

        上面 test_compute_next_run_accepts_json_string 只证明**函数本身**能吃字符串。
        本用例证明**调用点真的把库里的字符串传进去了** —— 这两件事是分开的：
        `_dispatch_one` 若哪天改成 `json.loads(d["schedule_json"])` 而库里存的是
        已解析的 dict，或者反过来漏了一层，单测再绿也拦不住。

        用 weekly 是为了让「按频次」和「+86400」两者给出的结果**必然不同**：
        daily 的频率下 +86400 恰好等于次日同刻，与正确结果重合，测不出问题。
        """
        import json as _json

        out = self._create(
            schedule={
                "freq": "weekly",
                "at": "09:00",
                "weekday": 0,
                "every_minutes": None,
                "tz": "Asia/Shanghai",
            }
        )
        did = out["delegation_id"]

        # 前提钉住：库里这一段确实是**文本**，不是 dict。
        # 若哪天存储层改成 JSON 列（自动解析），这条会红 —— 那是提醒去改注释，不是回归。
        raw = daily._load_delegation(did)["schedule_json"]
        self.assertIsInstance(raw, str, "schedule_json 已是 dict，说明存储层变了，本用例前提失效")
        self.assertEqual(_json.loads(raw)["freq"], "weekly")

        # plan_today 会真的调模型，替换掉（本用例只关心排期，不关心生成内容）
        orig_plan = daily.plan_today
        daily.plan_today = lambda d, ctx: "（测试）本周复盘"
        try:
            planned = int(time.time())
            res = daily._dispatch_one(did, planned)
        finally:
            daily.plan_today = orig_plan

        self.assertEqual(res["status"], "succeeded")

        nxt = daily._load_delegation(did)["next_run_at"]
        # 负向断言优先：不能落进 +86400 兜底
        self.assertNotEqual(nxt, planned + 86400, "下次触发退化成「执行时刻 +24h」")
        # 正向：落在指定的星期一 09:00
        self.assertEqual(time.localtime(nxt).tm_wday, 0, "weekly 没落在周一")
        self.assertEqual(time.localtime(nxt).tm_hour, 9)
        self.assertEqual(time.localtime(nxt).tm_min, 0)
        self.assertLess(nxt - planned, 8 * 86400)

    def test_patch_schedule_recomputes_next_run(self):
        """改 schedule 时 next_run_at 按新节奏重算（使 api.delegate 的 docstring 承诺成真）。"""
        out = self._create()  # daily at 21:00，启用，next_run_at 初始 None
        did = out["id"]
        # 模拟「已经派发过、排到了一个未来的 daily 时间」
        future = int(time.time()) + 86400
        daily._bump_next_run(did, future)
        # 改成 weekly(周一) at 09:00
        r = self.client.patch(
            f"/api/delegate/{did}",
            json={"schedule": {"freq": "weekly", "at": "09:00", "weekday": 0,
                              "every_minutes": None, "tz": "Asia/Shanghai"}},
        )
        assert r.status_code == 200, r.text
        nr = r.json()["next_run_at"]
        assert nr is not None and nr != future, (nr, future)
        lt = time.localtime(nr)
        assert (lt.tm_hour, lt.tm_min) == (9, 0)
        assert lt.tm_wday == 0
        assert nr > int(time.time())

    def test_auto_disable_records_last_error(self):
        """后7：worker 调 on_failure 自动停用时，要留下原因而非静默消失。"""
        out = self._create()  # 启用中的委托
        did = out["id"]
        # 模拟 worker 因投递目标会话失效而致命失败
        daily.on_failure("delegate.dispatch", did, True, "NotOwned: conversation gone")
        d = daily._load_delegation(did)
        self.assertFalse(d["enabled"])
        self.assertIsNotNone(d["last_error"])
        self.assertIn("NotOwned", d["last_error"])
        # API 也会把这个原因透传给前端
        got = self.client.get(f"/api/delegate/{did}").json()
        self.assertFalse(got["enabled"])
        self.assertIn("NotOwned", got["last_error"])

    def test_reenable_clears_last_error(self):
        """重新打开开关时清掉自动停用的告警，避免开起来还显示旧错误。"""
        out = self._create()
        did = out["id"]
        daily.on_failure("delegate.dispatch", did, True, "some fatal error")
        self.assertIsNotNone(daily._load_delegation(did)["last_error"])
        # 通过 PATCH 重新启用
        r = self.client.patch(f"/api/delegate/{did}", json={"enabled": True})
        assert r.status_code == 200, r.text
        self.assertIsNone(daily._load_delegation(did)["last_error"])
        self.assertIsNone(self.client.get(f"/api/delegate/{did}").json()["last_error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
