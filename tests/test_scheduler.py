"""定时任务底座自测（离线：不联网、不启动 APScheduler、不碰真实 data/）。

覆盖 issue 04 的四条验收 + 端点契约：

  1. 同一 idem_key 登记两次 → 第二次返回 False，job_runs 只有一行（靠唯一索引，不靠查）
  2. 重试分级：可重试类退避 1/5/15 分钟、attempts 到 3 停；不可重试类 attempts 停在 1；
     配额触顶 → 延后到次日同刻
  3. heartbeat_age() 随写入推进；伪造陈旧心跳 → 健康端点判 down（并验 stalled 中间态）
  4. cron 按 Asia/Shanghai 解释（APScheduler 解析结果与本模块估算一致，07:00 就是 07:00）

另加：`/api/schedule/*` 五个端点的鉴权 / 形状 / 管理员口径 / 游标分页 / 重试。

跑法：
  python -m pytest -q tests/test_scheduler.py
  python tests/test_scheduler.py -v          # 脚本式，直接跑
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.config  # noqa: E402  先让 chdir / load_dotenv 跑完
import core.session  # noqa: E402
from core import (
    account,  # noqa: E402
    quota,  # noqa: E402
)
from core import scheduler as sched  # noqa: E402

# 每台机器、每个用例一个独立临时目录：绝不写真实 data/
_TMP = tempfile.mkdtemp(prefix="baomi_sched_test_")
_SEQ = [0]

# 第 2 问的 cron 表：写死在这里，改了代码就必须在这儿显式改一次
EXPECTED_CRONS = {
    "delegate.dispatch": "*/5 * * * *",
    "delegate.retry": "7,22,37,52 * * * *",
    "report.daily": "30 23 * * *",
    "report.backfill": "0 4 * * *",
    "housekeeping": "15 4 * * *",
    "profile.aggregate": "30 2 * * *",
}

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _next_db(tag: str) -> str:
    _SEQ[0] += 1
    return os.path.join(_TMP, f"sched_{tag}_{_SEQ[0]}.db")


class TempDbCase(unittest.TestCase):
    """把调度库指到临时文件；用例结束换回来（不污染真实 data/scheduler.db）。"""

    def setUp(self):
        self._orig_path = sched._db_path
        self.path = _next_db(self.__class__.__name__)
        sched._db_path = lambda: self.path
        sched.ensure_tables()

    def tearDown(self):
        sched._db_path = self._orig_path


# ==================== 1. 幂等 ====================


class TestIdempotency(TempDbCase):
    def test_claim_same_idem_key_twice(self):
        planned = sched.now_ts()
        self.assertTrue(sched.claim_job("report.daily", "2026-10-12", planned))
        self.assertFalse(sched.claim_job("report.daily", "2026-10-12", planned))
        self.assertEqual(sched.count_runs(), 1, "第二次登记不该多出一行")
        # 明天同一时刻是「另一次投递」，必须能排进去——幂等键里 planned_at 的意义就在这
        self.assertTrue(sched.claim_job("report.daily", "2026-10-12", planned + 86400))
        self.assertEqual(sched.count_runs(), 2)
        # 换个对象也照排
        self.assertTrue(sched.claim_job("report.daily", "2026-10-13", planned))
        self.assertEqual(sched.count_runs(), 3)

    def test_enqueue_dedup_and_idem_shape(self):
        rid, key = sched.enqueue_job("report.daily", "*", 1760311000)
        self.assertTrue(rid.startswith("run_"))
        self.assertEqual(key, "report.daily|*|1760311000")
        rid2, key2 = sched.enqueue_job("report.daily", "*", 1760311000)
        self.assertIsNone(rid2)
        self.assertEqual(key, key2)
        self.assertEqual(sched.count_runs(), 1)
        self.assertEqual(len(sched.list_runs()["items"]), 1)

    def test_unique_index_is_the_guard(self):
        """幂等靠数据库约束兜底：绕过 claim_job 直接插重复行必须失败。"""
        with sched._get_conn() as conn:
            names = {
                r["name"]
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
            }
        self.assertIn("ux_job_runs_idem", names)
        with self.assertRaises(sqlite3.IntegrityError):
            with sched._get_conn() as conn:
                conn.execute(
                    "INSERT INTO job_runs (run_id, job_key, subject_id, "
                    "planned_at, idem_key, status) "
                    "VALUES ('run_a', 'k', 's', 1, 'k|s|1', 'pending')"
                )
                conn.execute(
                    "INSERT INTO job_runs (run_id, job_key, subject_id, "
                    "planned_at, idem_key, status) "
                    "VALUES ('run_b', 'k', 's', 1, 'k|s|1', 'pending')"
                )

    def test_claim_pending_only_once(self):
        sched.enqueue_job("report.daily", "2026-10-12", 1760311000)
        first = sched.claim_pending()
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["status"], "running")
        self.assertEqual(first[0]["attempts"], 1)
        self.assertEqual(sched.claim_pending(), [], "同一条不能被领第二次")
        # 只领有业务实现的任务：没挂 handler 时 pending 行留在表里等实现
        sched.enqueue_job("housekeeping", "*", 1760311001)
        only_report = sched.claim_pending(job_keys=["report.daily"])
        self.assertEqual(only_report, [])
        self.assertEqual(sched.count_runs("pending"), 1)


# ==================== 2. 重试分级 ====================


class TestRetryGrading(TempDbCase):
    def _start(self, job_key="delegate.dispatch", subject="d1", planned=1760311000):
        rid = sched.new_run_id()
        self.assertTrue(sched.claim_job(job_key, subject, planned, run_id=rid))
        return rid, planned

    def test_retryable_backs_off_then_stops(self):
        rid, planned = self._start()
        now = planned + 100
        row = sched.finish_job(rid, ok=False, error="TimeoutError: read timeout", now=now)
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(row["next_attempt_at"], now + 60, "第 1 次重试等 1 分钟")

        self.assertEqual(sched.due_retries(now=now + 59), [], "没到点不该被扫出来")
        self.assertEqual(len(sched.due_retries(now=now + 60)), 1)
        self.assertTrue(sched.claim_retry(rid, now=now + 60))
        self.assertEqual(sched.get_run(rid)["attempts"], 2)
        self.assertEqual(sched.due_retries(now=now + 61), [], "领走后不再是「到点的失败」")

        row = sched.finish_job(rid, ok=False, error="502 Bad Gateway", now=now + 120)
        self.assertEqual(row["next_attempt_at"], now + 120 + 300, "第 2 次重试等 5 分钟")
        self.assertTrue(sched.claim_retry(rid, now=now + 420))
        self.assertEqual(sched.get_run(rid)["attempts"], 3)

        row = sched.finish_job(rid, ok=False, error="429 Too Many Requests", now=now + 500)
        self.assertEqual(row["attempts"], 3)
        self.assertIsNone(row["next_attempt_at"], "3 次用完，不再重试")
        self.assertEqual(sched.due_retries(now=now + 999999), [])
        self.assertFalse(sched.claim_retry(rid, now=now + 999999))

    def test_fatal_stays_at_one_attempt(self):
        for err in (
            "401 Unauthorized: invalid api key",
            "Error code: 402 - Insufficient Balance",
            "会话已删除",
            "知识库不存在",
        ):
            with self.subTest(error=err):
                rid, planned = self._start(subject=err[:6], planned=1760320000)
                row = sched.finish_job(rid, ok=False, error=err, now=planned + 5)
                self.assertEqual(row["status"], "failed")
                self.assertEqual(row["attempts"], 1, "不可重试类不该再涨 attempts")
                self.assertIsNone(row["next_attempt_at"])
                self.assertEqual(sched.due_retries(now=planned + 999999), [])
                self.assertFalse(sched.claim_retry(rid, now=planned + 999999))

    def test_quota_defers_to_same_time_next_day(self):
        rid, planned = self._start(job_key="report.daily", subject="2026-10-12")
        row = sched.finish_job(
            rid, ok=False, error=quota.QuotaExceeded("auto", 120), now=planned + 5
        )
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(row["next_attempt_at"], planned + 86400, "配额触顶 → 次日同刻")
        self.assertEqual(sched.due_retries(now=planned + 86399), [])
        self.assertEqual(len(sched.due_retries(now=planned + 86400)), 1)

    def test_classify_error_table(self):
        self.assertEqual(sched.classify_error(status_code=429), sched.CLASS_RETRY)
        self.assertEqual(sched.classify_error(status_code=503), sched.CLASS_RETRY)
        self.assertEqual(sched.classify_error(exc=TimeoutError("timed out")), sched.CLASS_RETRY)
        self.assertEqual(sched.classify_error(status_code=401), sched.CLASS_FATAL)
        self.assertEqual(sched.classify_error(status_code=402), sched.CLASS_FATAL)
        self.assertEqual(sched.classify_error(message="会话不存在"), sched.CLASS_FATAL)
        self.assertEqual(sched.classify_error(message="库不存在"), sched.CLASS_FATAL)
        self.assertEqual(
            sched.classify_error(exc=quota.QuotaExceeded("auto", 120)), sched.CLASS_QUOTA
        )
        # 判不出来时按不可重试：无人值守链路不拿 token 赌「也许下次就好了」
        self.assertEqual(sched.classify_error(message="TypeError: 'NoneType'"), sched.CLASS_FATAL)

    def test_requeue_run_paths(self):
        rid, planned = self._start()
        sched.finish_job(rid, ok=False, error="timeout", now=planned)
        res = sched.requeue_run(rid, now=planned + 1000)
        self.assertTrue(res["ok"])
        self.assertEqual(res["run"]["next_attempt_at"], planned + 1000)
        # 成功过的执行不给重排
        rid2, planned2 = self._start(subject="d2", planned=1760312000)
        sched.finish_job(rid2, ok=True)
        bad = sched.requeue_run(rid2)
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["reason"], "not_failed")
        # 不存在的 run_id 与「重试已用完」
        self.assertEqual(sched.requeue_run("run_nope")["reason"], "not_found")
        rid3, planned3 = self._start(subject="d3", planned=1760313000)
        for i in range(3):
            sched.finish_job(rid3, ok=False, error="timeout", now=planned3 + i)
            if i < 2:
                sched.claim_retry(rid3, now=planned3 + i)
        self.assertEqual(sched.get_run(rid3)["attempts"], 3)
        self.assertEqual(sched.requeue_run(rid3)["reason"], "exhausted")
        forced = sched.requeue_run(rid3, force=True)
        self.assertTrue(forced["ok"])
        self.assertEqual(forced["run"]["attempts"], 0, "force 清空计数重新给机会")


# ==================== 3. 心跳与健康 ====================


class TestHeartbeatHealth(TempDbCase):
    def test_age_advances_and_flips_down(self):
        self.assertIsNone(sched.heartbeat_age(), "从未心跳 → None")
        self.assertEqual(sched.health()["worker"]["status"], "down")

        ts = sched.heartbeat(pid=4242, version="test-version")
        self.assertLess(sched.heartbeat_age(), 2)
        self.assertEqual(sched.health()["worker"]["status"], "up")
        w = sched.health()["worker"]
        self.assertEqual(w["pid"], 4242)
        self.assertEqual(w["version"], "test-version")
        self.assertEqual(ts, sched.heartbeat_info()["ts"])

        now = sched.now_ts()
        # 伪造陈旧心跳：进程号照样在（模拟「进程在但卡死」），只有时间戳是旧的
        for age, expect in ((301, "stalled"), (600, "stalled"), (601, "down"), (700, "down")):
            with self.subTest(age=age):
                with sched._get_conn() as conn:
                    conn.execute("UPDATE worker_heartbeat SET ts = ? WHERE id = 1", (now - age,))
                body = sched.health(now=now)["worker"]
                self.assertEqual(body["status"], expect)
                self.assertEqual(body["heartbeat_age_seconds"], age)

    def test_jobs_health_and_consecutive_failures(self):
        body = sched.health()
        self.assertEqual([j["job_key"] for j in body["jobs"]], sorted(EXPECTED_CRONS))
        for j in body["jobs"]:
            self.assertIn("cron", j)
            self.assertIn("consecutive_failures", j)
            self.assertIsNone(j["last_status"], "还没跑过：状态为空，不是编一个")

        planned = 1760311000
        for i in range(3):
            rid = sched.new_run_id()
            sched.claim_job("report.daily", f"s{i}", planned + i, run_id=rid)
            sched.finish_job(rid, ok=False, error="boom", error_class=sched.CLASS_RETRY)
        st = sched.job_stats("report.daily")
        self.assertEqual(st["consecutive_failures"], 3, "连续 3 次失败 = 外发告警阈值")
        self.assertEqual(st["last_status"], "failed")
        self.assertIsNotNone(st["last_run_at"])

        rid = sched.new_run_id()
        sched.claim_job("report.daily", "ok", planned + 9, run_id=rid)
        sched.finish_job(rid, ok=True)
        self.assertEqual(sched.job_stats("report.daily")["consecutive_failures"], 0, "成功即归零")

    def test_purge_keeps_failed_by_default(self):
        for i, ok in enumerate((True, False)):
            rid = sched.new_run_id()
            sched.claim_job("housekeeping", f"h{i}", 1000 + i, run_id=rid)
            sched.finish_job(rid, ok=ok, error=None if ok else "boom")
        self.assertEqual(sched.purge_runs(99999), 1, "只清成功行")
        self.assertEqual(sched.count_runs("failed"), 1)
        self.assertEqual(sched.purge_runs(99999, keep_failed=False), 1)


# ==================== 4. 时区与 cron ====================


class TestCronTimezone(TempDbCase):
    def test_cron_table_matches_spec(self):
        self.assertEqual({k: v["cron"] for k, v in sched.JOB_DEFS.items()}, EXPECTED_CRONS)
        self.assertEqual(sched.TZ_NAME, "Asia/Shanghai")
        self.assertEqual(core.config.SCHEDULER_TZ, "Asia/Shanghai")
        # AP SCHEDULER_TIMEZONE 与 config.SCHEDULER_TZ 是同一个值（worker import 时设好）
        import worker.scheduler as worker

        self.assertEqual(os.environ["APSCHEDULER_TIMEZONE"], core.config.SCHEDULER_TZ)
        # 默认 jobstore 就是 data/scheduler.db；当前用例里指到临时库，URL 跟着走
        default_db = self._orig_path().replace("\\", "/")
        self.assertTrue(default_db.endswith("/data/scheduler.db"), default_db)
        self.assertEqual(worker.jobstore_url(), "sqlite:///" + self.path.replace("\\", "/"))
        self.assertEqual(
            sched.JOB_DEFS["report.daily"]["cron"],
            f"{core.config.REPORT_MINUTE} {core.config.REPORT_HOUR} * * *",
        )

    def test_apscheduler_cron_is_shanghai(self):
        from apscheduler.triggers.cron import CronTrigger

        trig = CronTrigger.from_crontab("30 23 * * *", timezone=SHANGHAI)
        base = datetime(2026, 10, 12, 12, 0, tzinfo=SHANGHAI)
        nxt = trig.get_next_fire_time(None, base)
        self.assertEqual((nxt.hour, nxt.minute), (23, 30))
        self.assertEqual(nxt.utcoffset(), timedelta(hours=8))
        # 上海 23:30 == UTC 15:30，说明它确实是「上海时间」而不是「UTC 时间」
        self.assertEqual(nxt.astimezone(UTC).hour, 15)

        # 07:00 就是 07:00：不带时区会被当成本机/UTC，带上才是上海的早上七点
        seven = datetime(2026, 10, 12, 7, 0, tzinfo=SHANGHAI)
        self.assertEqual(
            int(seven.timestamp()),
            int(datetime(2026, 10, 11, 23, 0, tzinfo=UTC).timestamp()),
        )

    def test_estimate_matches_apscheduler(self):
        from apscheduler.triggers.cron import CronTrigger

        after = int(datetime(2026, 10, 12, 12, 0, 30, tzinfo=SHANGHAI).timestamp())
        base = datetime.fromtimestamp(after, SHANGHAI)
        for key, meta in sched.JOB_DEFS.items():
            with self.subTest(job_key=key):
                trig = CronTrigger.from_crontab(meta["cron"], timezone=SHANGHAI)
                expect = int(trig.get_next_fire_time(None, base).timestamp())
                self.assertEqual(
                    sched.estimate_next_run(meta["cron"], "Asia/Shanghai", after), expect
                )
        # 日历规则（本项目的 5 个任务用不到）宁可返回 None，也不猜一个时间给运维看
        self.assertIsNone(sched.estimate_next_run("0 4 * * 1", "Asia/Shanghai", after))
        self.assertIsNone(sched.estimate_next_run("不是 cron", "Asia/Shanghai", after))

    def test_day_bounds_are_shanghai_midnight(self):
        start, end = sched.day_bounds("2026-10-12")
        self.assertEqual(end - start, 86400)
        self.assertEqual(
            datetime.fromtimestamp(start, SHANGHAI).strftime("%Y-%m-%d %H:%M"), "2026-10-12 00:00"
        )
        self.assertEqual(
            datetime.fromtimestamp(end, SHANGHAI).strftime("%Y-%m-%d %H:%M"), "2026-10-13 00:00"
        )


# ==================== 5. 端点 ====================


class TestScheduleApi(unittest.TestCase):
    """`/api/schedule/*` 五个端点：鉴权、形状、管理员口径、分页、重试。

    用 TestClient（不 with，跳过 lifespan 避免预热 Chroma），
    session 库与调度库都指到临时目录；用例结束全部换回来。
    """

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        import main

        cls.main = main
        cls.TestClient = TestClient

    def setUp(self):
        self._orig_session_path = core.session._db_path
        self._orig_secret = core.config._AUTH_SECRET_FILE
        self._orig_admins = list(core.config.ADMIN_USERNAMES)
        self._orig_signup = core.config.ALLOW_SIGNUP
        self._orig_rate = core.config.RATE_PER_MINUTE
        self._orig_sched_path = sched._db_path
        self._orig_now_ts = sched.now_ts

        self.session_db = _next_db("sessions")
        core.session._db_path = lambda: self.session_db
        core.session._init_db()
        # users / usage 表建在临时库里：不建的话第一次注册会撞 "no such table"
        account._ensure_tables()
        quota._ensure_tables()
        core.config._AUTH_SECRET_FILE = os.path.join(_TMP, f"auth_secret_{_SEQ[0]}")
        core.config.ALLOW_SIGNUP = True
        core.config.RATE_PER_MINUTE = 0  # 端点测试不受每分钟限流干扰
        core.config.ADMIN_USERNAMES = ["alice"]

        self.sched_db = _next_db("api")
        sched._db_path = lambda: self.sched_db
        sched.ensure_tables()

        self.client = self.TestClient(self.main.app)
        self._register(self.client, "alice")

    def tearDown(self):
        core.session._db_path = self._orig_session_path
        core.config._AUTH_SECRET_FILE = self._orig_secret
        core.config.ADMIN_USERNAMES = self._orig_admins
        core.config.ALLOW_SIGNUP = self._orig_signup
        core.config.RATE_PER_MINUTE = self._orig_rate
        sched._db_path = self._orig_sched_path
        sched.now_ts = self._orig_now_ts

    @staticmethod
    def _register(client, name):
        r = client.post("/api/auth/register/", json={"username": name, "password": "secret123"})
        assert r.status_code == 200, r.text

    # ---- 鉴权 ----

    def test_all_endpoints_require_login(self):
        anon = self.TestClient(self.main.app)
        calls = [
            ("get", "/api/schedule/health", None),
            ("get", "/api/schedule/jobs", None),
            ("get", "/api/schedule/runs", None),
            ("post", "/api/schedule/run-now", {"job_key": "report.daily"}),
            ("post", "/api/schedule/retry", {"run_id": "run_x"}),
        ]
        for method, path, body in calls:
            with self.subTest(path=path):
                r = getattr(anon, method)(path, json=body) if body else getattr(anon, method)(path)
                self.assertEqual(r.status_code, 401, r.text)

    # ---- health / jobs ----

    def test_health_shape_and_flip(self):
        r = self.client.get("/api/schedule/health")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(sorted(body), ["jobs", "worker"])
        self.assertEqual(body["worker"]["status"], "down", "没有心跳就是 down")
        self.assertIsNone(body["worker"]["heartbeat_age_seconds"])
        self.assertEqual(len(body["jobs"]), len(EXPECTED_CRONS))

        sched.heartbeat(pid=9999, version="pytest")
        body = self.client.get("/api/schedule/health").json()
        self.assertEqual(body["worker"]["status"], "up")
        self.assertEqual(body["worker"]["pid"], 9999)
        self.assertLessEqual(body["worker"]["heartbeat_age_seconds"], 2)

        # 伪造 11 分钟前的心跳（进程号还在）→ 必须判 down，而不是看进程表
        now = sched.now_ts()
        with sched._get_conn() as conn:
            conn.execute("UPDATE worker_heartbeat SET ts = ? WHERE id = 1", (now - 660,))
        body = sched.health(now=now)
        self.assertEqual(body["worker"]["status"], "down")
        self.assertEqual(sched.health(now=now)["worker"]["heartbeat_age_seconds"], 660)

    def test_jobs_list_and_filter(self):
        r = self.client.get("/api/schedule/jobs")
        self.assertEqual(r.status_code, 200, r.text)
        jobs = r.json()
        self.assertEqual({j["job_key"]: j["cron"] for j in jobs}, EXPECTED_CRONS)
        for j in jobs:
            self.assertTrue(j["enabled"])
            self.assertEqual(j["tz"], "Asia/Shanghai")
            self.assertIn("description", j)
        self.assertEqual(
            self.client.get("/api/schedule/jobs", params={"enabled": "false"}).json(), []
        )

    # ---- run-now ----

    def _freeze_now(self, ts=1760311000):
        sched.now_ts = lambda: ts
        return ts

    def test_run_now_enqueues_then_dedupes(self):
        ts = self._freeze_now()
        r = self.client.post("/api/schedule/run-now", json={"job_key": "report.daily"})
        self.assertEqual(r.status_code, 202, r.text)
        self.assertEqual(
            r.json(),
            {
                "accepted": True,
                "job_key": "report.daily",
                "queued": 1,
                "idem_key": f"report.daily|*|{ts}",
            },
        )
        r2 = self.client.post("/api/schedule/run-now", json={"job_key": "report.daily"})
        self.assertEqual(r2.json()["queued"], 0, "同一秒重复点只排一次")
        self.assertEqual(sched.count_runs("pending"), 1)

        # dry_run 不落库
        r3 = self.client.post(
            "/api/schedule/run-now", json={"job_key": "report.daily", "dry_run": True}
        )
        self.assertEqual(r3.status_code, 202)
        self.assertEqual(r3.json()["queued"], 0)
        self.assertEqual(sched.count_runs("pending"), 1)

        # 指定 subject_id：幂等键里带上它
        r4 = self.client.post(
            "/api/schedule/run-now",
            json={"job_key": "report.daily", "subject_id": "2026-10-12"},
        )
        self.assertEqual(r4.json()["idem_key"], f"report.daily|2026-10-12|{ts}")
        self.assertEqual(sched.count_runs("pending"), 2)

    def test_run_now_errors(self):
        r = self.client.post("/api/schedule/run-now", json={"job_key": "nope"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["detail"], "未知的 task key")

        bob = self.TestClient(self.main.app)
        self._register(bob, "bob")
        r2 = bob.post("/api/schedule/run-now", json={"job_key": "report.daily"})
        self.assertEqual(r2.status_code, 403, r2.text)
        self.assertEqual(r2.json()["detail"], "需要管理员权限")
        self.assertEqual(sched.count_runs(), 0, "越权不该产生任何行")

    # ---- runs / retry ----

    def test_runs_pagination_and_filters(self):
        for i in range(3):
            rid = sched.new_run_id()
            sched.claim_job("report.daily", f"s{i}", 1760311000 + i, run_id=rid)
            sched.finish_job(rid, ok=(i != 1), error=None if i != 1 else "boom")

        page1 = self.client.get("/api/schedule/runs", params={"limit": 2}).json()
        self.assertEqual(len(page1["items"]), 2)
        self.assertIsNotNone(page1["next_cursor"])
        page2 = self.client.get(
            "/api/schedule/runs", params={"limit": 2, "cursor": page1["next_cursor"]}
        ).json()
        self.assertEqual(len(page2["items"]), 1)
        self.assertIsNone(page2["next_cursor"])
        ids = [r["run_id"] for r in page1["items"] + page2["items"]]
        self.assertEqual(len(set(ids)), 3, "两页不该重复")

        only_failed = self.client.get("/api/schedule/runs", params={"status": "failed"}).json()
        self.assertEqual(len(only_failed["items"]), 1)
        self.assertEqual(only_failed["items"][0]["error"], "boom")

        r = self.client.get("/api/schedule/runs", params={"cursor": "不是游标"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["detail"], "cursor 无效")

    def test_retry_requeues_failed_run(self):
        rid = sched.new_run_id()
        sched.claim_job("delegate.dispatch", "d1", 1760311000, run_id=rid)
        sched.finish_job(rid, ok=False, error="timeout", now=1760311000)
        ts = self._freeze_now(1760312000)

        r = self.client.post("/api/schedule/retry", json={"run_id": rid})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["retried"], True)
        self.assertEqual(r.json()["attempts"], 1)
        self.assertEqual(r.json()["next_attempt_at"], ts)
        self.assertEqual(len(sched.due_retries(now=ts)), 1, "重排后立刻可被 worker 扫到")

        self.assertEqual(
            self.client.post("/api/schedule/retry", json={"run_id": "run_nope"}).status_code, 404
        )
        # 成功过的执行：409
        rid2 = sched.new_run_id()
        sched.claim_job("delegate.dispatch", "d2", 1760311001, run_id=rid2)
        sched.finish_job(rid2, ok=True)
        r2 = self.client.post("/api/schedule/retry", json={"run_id": rid2})
        self.assertEqual(r2.status_code, 409)
        self.assertEqual(r2.json()["detail"], "只有失败的执行才能重试")

        # 非管理员 403
        bob = self.TestClient(self.main.app)
        self._register(bob, "bob")
        self.assertEqual(bob.post("/api/schedule/retry", json={"run_id": rid}).status_code, 403)


if __name__ == "__main__":
    unittest.main(verbosity=2)
