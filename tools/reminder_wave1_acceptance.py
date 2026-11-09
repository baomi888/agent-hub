"""第一波验收：按规格第七节逐条跑，输出可贴给评审的结果表。

为什么不用 curl 手敲：验收要能反复跑、每次结论一致，而且「改了什么」得看得见。
这里走的是**真 HTTP 栈**（FastAPI TestClient + 真路由 + 真 SQLite），只把
「发到推送服务方」这一步换成了记事本 —— 没有浏览器订阅和 VAPID 密钥时，
真发只会在 pywebpush 里报 401，验证不到闸门。

用法：venv\\Scripts\\python.exe tools/reminder_wave1_acceptance.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

# 往上找到仓库根（这个脚本可能被放在 tools/ 或 .workbuddy/acc/ 下，
# 写死层数会在挪位置时静默 import 错东西）
ROOT = os.path.dirname(os.path.abspath(__file__))
while not os.path.exists(os.path.join(ROOT, "main.py")):
    _parent = os.path.dirname(ROOT)
    if _parent == ROOT:
        raise SystemExit("找不到仓库根（缺 main.py）")
    ROOT = _parent
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="baomi_acc_")
DB = os.path.join(TMP, "sessions.db")

# ★★ 必须用 AGENT_HUB_DB 这个**测试缝**把库钉到临时目录，而且**绝不还原**。
#    踩过的坑：先 `session._db_path = lambda: DB`、导入完又还原成原函数，
#    后续所有 HTTP 请求就都写进了真实 data/sessions.db（6 条测试提醒 + 1 条
#    reminder_prefs 落在真库里，事后手工清掉）。AGENT_HUB_DB 是 core/session.py
#    里的正规测试缝，`_db_path()` 每次都读它，不存在"忘了还原/还原早了"。
os.environ["AGENT_HUB_DB"] = DB

import core.session as session  # noqa: E402

session._init_db()

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from core import reminder_core, reminder_store, timeutil  # noqa: E402
from core.auth import get_current_user  # noqa: E402
from service import push as push_svc  # noqa: E402
from service import reminder_tick  # noqa: E402


def _guard_off_production() -> None:
    """任何一次写之前都先确认自己在临时库上。

    上面那个坑的代价是「测试数据悄悄进了真实库」，而且**表面上一切正常**
    （PASS 照打）。所以这里不是注释提醒，是硬闸：路径不对就直接退出。
    """
    path = str(session._db_path())
    if path != DB or "baomi_acc_" not in path:
        raise SystemExit(f"拒绝在非临时库上跑验收：{path}")


_guard_off_production()

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  —— {detail}" if detail else ""))


def as_user(uid: str) -> TestClient:
    _guard_off_production()
    main.app.dependency_overrides[get_current_user] = lambda: uid
    return TestClient(main.app)


def clock(ts: int) -> str:
    return datetime.fromtimestamp(ts, timeutil.TZ).strftime("%m-%d %H:%M")


print(f"临时库：{DB}\n")

# ==================== 前置：造一个「升级前就存在的老用户」 ====================
print("【前置】老用户 u_old：设过 20:00 + 有活推送订阅，升级前状态")
old_client = as_user("u_old")
with session._conn() as conn:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS reminder_prefs "
        "(owner_id TEXT PRIMARY KEY, hour INTEGER, minute INTEGER, updated_at INTEGER)"
    )
    conn.execute("INSERT INTO reminder_prefs VALUES ('u_old', 20, 0, 0)")
# 迁移会把这批人搬成系统提醒；这里只造数据，不提前建表
check("老用户数据已就位（reminder_prefs=20:00）", True)

# ==================== 验收 5：老用户不丢 ====================
print("\n【验收 5】老用户升级后，原来 20:00 那条还在")
reminder_core.ensure()
system = reminder_core.get_system("u_old")
check(
    "迁移生成了系统提醒，且时间是 20:00",
    bool(system) and system["at"] == "20:00",
    f"at={system and system['at']} action={system and system['action']}",
)
legacy = old_client.get("/api/remind").json()
check("老接口 /api/remind 仍返回 20:00（结构不变）", legacy["time"] == "20:00", str(legacy))
check(
    "日历读的 reminder_prefs 仍是同一时刻",
    reminder_store.get("u_old") == (20, 0),
    str(reminder_store.get("u_old")),
)
# 迁移幂等：再跑一次不该多出一条
reminder_core.reset_cache()
reminder_core.ensure()
with session._conn() as conn:
    n = conn.execute("SELECT COUNT(*) FROM reminders WHERE owner_id='u_old'").fetchone()[0]
check("迁移幂等（跑两遍仍只有 1 条）", n == 1, f"rows={n}")

# ==================== 验收 1 / 2：自定义提醒能响，且与四六级无关 ====================
print("\n【验收 1+2】建一条与四六级无关的提醒，到点能推")
new_client = as_user("u_new")
now = timeutil.now_ts()
target = datetime.fromtimestamp(now, timeutil.TZ) + timedelta(minutes=2)
at_str = target.strftime("%H:%M")
res = new_client.post(
    "/api/reminders",
    json={"title": "复盘今天", "freq": "daily", "at": at_str, "action": "notify", "channels": "push"},
)
item = res.json()
check("POST /api/reminders 成功", res.status_code == 200, f"id={item.get('id')}")
check("来源是 manual（全程不碰 CET 数据）", item.get("source") == "manual", str(item.get("source")))
check(
    "next_fire_at 落在 2 分钟后",
    0 < int(item.get("next_fire_at") or 0) - now <= 180,
    clock(int(item.get("next_fire_at") or 0)),
)

sent_log: list[dict] = []


def fake_send(owner_id: str, payload: dict) -> dict:
    sent_log.append({"owner": owner_id, "payload": payload})
    return {"sent": 1, "failed": 0, "gone": 0, "skipped": 0, "items": []}


push_svc.send_to_user = fake_send  # type: ignore[assignment]
planned = int(item["next_fire_at"])
subjects = reminder_tick.due_subjects(reminder_tick.JOB_KEY, planned)
check("tick 的 due 阶段选中了这个用户", subjects == ["u_new"], str(subjects))
tick = reminder_tick.run_one(reminder_tick.JOB_KEY, "u_new", planned)
check(
    "发出 1 条通知",
    len(sent_log) == 1 and tick.get("fired") == 1,
    f"title={sent_log and sent_log[0]['payload']['title']}",
)
fires = new_client.get(f"/api/reminders/fires?reminder_id={item['id']}").json()["items"]
check("流水记成 sent", fires and fires[0]["status"] == "sent", str(fires[:1]))
after = new_client.get(f"/api/reminders/{item['id']}").json()
check(
    "发完自动推进到明天同一时刻（不会每分钟重发）",
    int(after["next_fire_at"]) > planned,
    clock(int(after["next_fire_at"])),
)

# ==================== 验收 3：静默时段 ====================
print("\n【验收 3】静默时段 23:00–07:00，23:30 那条不该响")
quiet_client = as_user("u_quiet")
quiet_client.put("/api/reminders/prefs", json={"quiet_start": "23:00", "quiet_end": "07:00"})
q = quiet_client.post("/api/reminders", json={"title": "深夜复盘", "at": "23:30"}).json()
q_planned = int(q["next_fire_at"])
sent_log.clear()
reminder_tick.run_one(reminder_tick.JOB_KEY, "u_quiet", q_planned)
q_fires = quiet_client.get(f"/api/reminders/fires?reminder_id={q['id']}").json()["items"]
check("没有发出通知", sent_log == [], f"calls={len(sent_log)}")
check(
    "流水是 skipped_quiet",
    bool(q_fires) and q_fires[0]["status"] == "skipped_quiet",
    str(q_fires[:1]),
)
check(
    "不补发（顺到明天 23:30，不是十分钟后）",
    int(quiet_client.get(f"/api/reminders/{q['id']}").json()["next_fire_at"]) - q_planned > 3600,
    clock(int(quiet_client.get(f"/api/reminders/{q['id']}").json()["next_fire_at"])),
)

# ==================== 验收 4：每日上限 ====================
print("\n【验收 4】每日上限（cap=1，先占满，再让一条到点）")
cap_client = as_user("u_cap")
cap_client.put(
    "/api/reminders/prefs",
    json={"daily_cap": 1, "quiet_start": "03:00", "quiet_end": "03:00"},
)
filled = cap_client.post("/api/reminders", json={"title": "已发过", "at": "08:00"}).json()
blocked = cap_client.post("/api/reminders", json={"title": "超上限", "at": "12:00"}).json()
today_noon = timeutil.day_bounds(timeutil.day_str())[0] + 12 * 3600
reminder_core.record_fire(filled["id"], today_noon, reminder_core.STATUS_SENT)
with session._conn() as conn:
    conn.execute("UPDATE reminders SET next_fire_at=? WHERE id=?", (today_noon, blocked["id"]))
sent_log.clear()
reminder_tick.run_one(reminder_tick.JOB_KEY, "u_cap", today_noon)
cap_fires = cap_client.get(f"/api/reminders/fires?reminder_id={blocked['id']}").json()["items"]
check("没有发出通知", sent_log == [], f"calls={len(sent_log)}")
check(
    "流水是 skipped_cap",
    bool(cap_fires) and cap_fires[0]["status"] == "skipped_cap",
    str(cap_fires[:1]),
)

# ==================== 同刻合并 ====================
print("\n【附加】同一分钟两条提醒 → 一条通知（风险表要求的「同刻合并」）")
merge_client = as_user("u_merge")
a = merge_client.post("/api/reminders", json={"title": "复盘今天", "at": "12:00"}).json()
b = merge_client.post("/api/reminders", json={"title": "喝水", "at": "12:00"}).json()
with session._conn() as conn:
    conn.execute("UPDATE reminders SET next_fire_at=? WHERE owner_id='u_merge'", (today_noon,))
sent_log.clear()
mr = reminder_tick.run_one(reminder_tick.JOB_KEY, "u_merge", today_noon)
check("只发 1 条", len(sent_log) == 1, f"calls={len(sent_log)}")
check(
    "标题体现合并条数",
    "2 条" in sent_log[0]["payload"]["title"],
    sent_log[0]["payload"]["title"] if sent_log else "-",
)
check("两条都记了流水（第二条标 merged_into）", mr.get("fired") == 2, str(mr.get("fired")))

# ==================== 验收 7：委托不被污染 ====================
print("\n【验收 7】在提醒里改标题，delegations 一个字节都不动")
with session._conn() as conn:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS delegations (delegation_id TEXT PRIMARY KEY, owner_id TEXT, "
        "name TEXT, prompt TEXT, enabled INTEGER DEFAULT 1, created_at INTEGER, updated_at INTEGER)"
    )
    conn.execute("DELETE FROM delegations WHERE delegation_id='dl_1'")
    # 显式列名：真库这张表有 15 列（daily 的迁移会补 kind/enabled/created_at…），
    # 位置式 INSERT 会直接炸
    conn.execute(
        "INSERT INTO delegations (delegation_id, owner_id, name, prompt, created_at, updated_at) "
        "VALUES ('dl_1','u_dl','晚间复盘','今天学得怎么样',0,0)"
    )
dl_client = as_user("u_dl")
dl = dl_client.post(
    "/api/reminders",
    json={"title": "委托", "source": "delegate", "source_ref": "dl_1", "at": "21:00", "action": "agent_deliver"},
).json()
dl_client.patch(f"/api/reminders/{dl['id']}", json={"override": {"title": "我自己的叫法"}})
view = dl_client.get(f"/api/reminders/{dl['id']}").json()
with session._conn() as conn:
    row = conn.execute("SELECT name, prompt FROM delegations WHERE delegation_id='dl_1'").fetchone()
check("提醒视图用了 override 标题", view["title"] == "我自己的叫法", view["title"])
check("delegations 原文未被改写", (row[0], row[1]) == ("晚间复盘", "今天学得怎么样"), str(tuple(row)))

# ==================== 验收 6：四六级仍能提醒 ====================
print("\n【验收 6】source=cet 的提醒只推 CET 内容，不被打卡内容顶掉")


def cet_builder(uid: str, day: str) -> dict:
    return {"title": "六级今天还剩 2 个任务", "body": "背单词 · 距考试 12 天", "url": "/cet", "tag": "daily-cet"}


push_svc.build_message = cet_builder  # type: ignore[assignment]
cet_client = as_user("u_cet")
cet = cet_client.post(
    "/api/reminders", json={"title": "六级", "source": "cet", "at": "19:00", "action": "agent_deliver"}
).json()
with session._conn() as conn:
    conn.execute("UPDATE reminders SET next_fire_at=? WHERE id=?", (today_noon, cet["id"]))
sent_log.clear()
reminder_tick.run_one(reminder_tick.JOB_KEY, "u_cet", today_noon)
check(
    "CET 内容原样推出（与改造前文案一致）",
    bool(sent_log) and sent_log[0]["payload"]["title"] == "六级今天还剩 2 个任务",
    sent_log[0]["payload"]["title"] if sent_log else "-",
)


def checkin_builder(uid: str, day: str) -> dict:
    return {"title": "连续 3 天，今天还没打卡", "body": "", "url": "/checkin", "tag": "daily-checkin"}


push_svc.build_message = checkin_builder  # type: ignore[assignment]
sent_log.clear()
with session._conn() as conn:
    conn.execute("UPDATE reminders SET next_fire_at=? WHERE id=?", (today_noon - 60, cet["id"]))
reminder_tick.run_one(reminder_tick.JOB_KEY, "u_cet", today_noon - 60)
cet_fires = cet_client.get(f"/api/reminders/fires?reminder_id={cet['id']}").json()["items"]
second = [f for f in cet_fires if f["planned_at"] == today_noon - 60]
check("当天只有打卡内容时，CET 那条不发（记 skipped_empty）", sent_log == [], f"calls={len(sent_log)}")
check(
    "这一次的流水是 skipped_empty",
    bool(second) and second[0]["status"] == "skipped_empty",
    str(second[:1]),
)

# ==================== 老链路静默 ====================
print("\n【总闸】push.daily 的定时路径必须静默（否则每天响两遍）")
check("push.daily due 阶段返回空", push_svc.due_subjects("push.daily", today_noon) == [])

# ==================== 验收清单里没法在本机验的 ====================
print("\n【未验】需要在真机上做的两条")
print("  · 验收 8「关页面也能响」：本机没有浏览器订阅 + VAPID，只能证明走的是服务端 tick")
print("  · 验收 9/10/11/12：课表 ICS、上云、番茄钟、导航 —— 属于第二波")

passed = sum(1 for _, ok, _ in RESULTS if ok)
print(f"\n{'=' * 60}\n合计 {len(RESULTS)} 条断言：通过 {passed}，失败 {len(RESULTS) - passed}")
sys.exit(0 if passed == len(RESULTS) else 1)
