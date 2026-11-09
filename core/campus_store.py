"""校园工具集的云端快照（课表 / 周次 / 学期 / 单词 / DDL）。

设计口径（规格 2.3，照抄不改）：
  · **整份快照，不做字段级合并**。冲突时 `updated_at` 新者胜、整份覆盖。
    课表带 seq/term/periods 嵌套结构，字段级合并要处理「删课 vs 改课」的歧义，
    代码量是整份快照的 5 倍，收益接近零（单人自用，两台设备同时编辑同一门课的概率≈0）。
  · 白名单是**穷举**的：只认这 5 个键，别的一律 400。
  · 番茄钟（`campus.pomodoro.*`）**永不入库**：它已有 `focusSync.ts` 自己的同步逻辑，
    再接一遍会打架，表现为「专注记录偶尔少几条」。这里连白名单都不给它留位置。
  · localStorage 永远是唯一读源，云端只是「抄一份」。所以网关层不做任何缓存，
    也不在写入时回读覆盖本地 —— 那条路径会制造「本地刚改完又被云端写回旧值」。

表结构复用 `reminder_core._SCHEMA` 里已经建好的 `campus_state`
（owner_id / tool / payload_json / updated_at，主键 (owner_id, tool)）。
这里再写一遍 `CREATE TABLE IF NOT EXISTS` 是刻意的：本模块不该依赖
「提醒模块先被 import 过」这个隐式顺序。
"""

from __future__ import annotations

import json
from typing import Any

from core import session as store

# 白名单：与 frontend/src/campus/storage/keys.ts 的 CAMPUS_KEYS 逐字对应。
# 加键必须两边一起改 —— 只改一边的表现是「同步静默失效」，最难查。
ALLOWED_TOOLS = (
    "campus.schedule.courses.v2",
    "campus.schedule.week.v1",
    "campus.schedule.term.v1",
    "campus.words.progress.v1",
    "campus.deadline.items.v1",
)

# 明确拒绝并给出人话的键（不是白名单漏了，是刻意不放）
_REFUSED_TOOLS = {
    "campus.pomodoro.daily.v1": "番茄钟有自己的一套同步，别再上报一遍",
    "campus.pomodoro.tasks.v1": "番茄钟有自己的一套同步，别再上报一遍",
    "campus.pomodoro.sessions.v1": "番茄钟有自己的一套同步，别再上报一遍",
    "campus.pomodoro.sound.v1": "番茄钟有自己的一套同步，别再上报一遍",
    "campus.gpa.rows.v1": "成绩表暂不上云（涉及跨学期历史，口径要先定）",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS campus_state (
    owner_id     TEXT NOT NULL,
    tool         TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    updated_at   INTEGER NOT NULL,
    PRIMARY KEY (owner_id, tool)
);
"""

_READY: set[str] = set()


class CampusInvalid(ValueError):
    """请求不合法 → 端点回 400。"""


def _db_path() -> str:
    return str(store._db_path())  # noqa: SLF001


def _conn():
    return store._get_conn()  # noqa: SLF001


def ensure() -> None:
    """幂等建表（键用库路径：测试里换库要能重建）。"""
    path = _db_path()
    if path in _READY:
        return
    with _conn() as conn:
        conn.executescript(_SCHEMA)
    _READY.add(path)


def reset_cache() -> None:
    _READY.clear()


def _check_tool(tool: str) -> str:
    key = str(tool or "").strip()
    if key in _REFUSED_TOOLS:
        raise CampusInvalid(_REFUSED_TOOLS[key])
    if key not in ALLOWED_TOOLS:
        raise CampusInvalid(f"不支持的同步项：{key or '(空)'}")
    return key


def list_all(owner_id: str) -> list[dict[str, Any]]:
    """这个人云端的全部快照（不含 payload 之外的任何元信息）。"""
    ensure()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT tool, payload_json, updated_at FROM campus_state WHERE owner_id=?",
            (owner_id,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for tool, raw, updated_at in rows:
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            # 脏数据不该让整次拉取失败：跳过这一项，别的工具照常同步
            continue
        out.append({"tool": tool, "payload": payload, "updated_at": int(updated_at)})
    return out


def get(owner_id: str, tool: str) -> dict[str, Any] | None:
    key = _check_tool(tool)
    ensure()
    with _conn() as conn:
        row = conn.execute(
            "SELECT payload_json, updated_at FROM campus_state WHERE owner_id=? AND tool=?",
            (owner_id, key),
        ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    return {"tool": key, "payload": payload, "updated_at": int(row[1])}


def put(owner_id: str, tool: str, payload: Any, updated_at: int) -> dict[str, Any]:
    """整份写入。云端更新则**不写**，把云端那份返回给调用方。

    返回值两种形状：
      写成功   {"ok": True,  "updated_at": <本条的>}
      云端更新 {"ok": False, "conflict": True, "cloud": {payload, updated_at}}
    """
    key = _check_tool(tool)
    if not isinstance(payload, (dict, list)):
        raise CampusInvalid("payload 必须是对象或数组")

    try:
        stamp = int(updated_at)
    except (TypeError, ValueError):
        raise CampusInvalid("updated_at 必须是整数毫秒时间戳") from None
    if stamp <= 0:
        raise CampusInvalid("updated_at 必须是正整数")

    ensure()
    with _conn() as conn:
        current = conn.execute(
            "SELECT payload_json, updated_at FROM campus_state WHERE owner_id=? AND tool=?",
            (owner_id, key),
        ).fetchone()

        # 云端更新（严格大于）：不覆盖。相等视为同一次写入的重放，让它过。
        if current is not None and int(current[1]) > stamp:
            try:
                cloud_payload = json.loads(current[0])
            except (TypeError, ValueError):
                cloud_payload = None
            return {
                "ok": False,
                "conflict": True,
                "cloud": {"payload": cloud_payload, "updated_at": int(current[1])},
            }

        conn.execute(
            "INSERT INTO campus_state (owner_id, tool, payload_json, updated_at) "
            "VALUES (?,?,?,?) "
            "ON CONFLICT(owner_id, tool) DO UPDATE SET "
            "  payload_json=excluded.payload_json, updated_at=excluded.updated_at",
            (owner_id, key, json.dumps(payload, ensure_ascii=False), stamp),
        )
    return {"ok": True, "updated_at": stamp}


def delete(owner_id: str, tool: str) -> bool:
    """清掉一项（用户主动「清空云端」时用；目前没有界面入口，先留着给运维）。"""
    key = _check_tool(tool)
    ensure()
    with _conn() as conn:
        return conn.execute(
            "DELETE FROM campus_state WHERE owner_id=? AND tool=?", (owner_id, key)
        ).rowcount > 0


def load_snapshot(owner_id: str) -> dict[str, Any]:
    """课表 ICS 用：把打散的三条快照拼回一个快照，缺的给 None。

    分开存是上云那侧的需求（逐个键独立上报），而 ICS 要的是完整视图，
    所以在这里拼一次 —— 让调用方（service/calendar）不用知道存储怎么切。
    """
    courses = get(owner_id, "campus.schedule.courses.v2")
    term = get(owner_id, "campus.schedule.term.v1")
    return {
        "courses": (courses or {}).get("payload"),
        "term": (term or {}).get("payload"),
        "courses_updated_at": (courses or {}).get("updated_at") or 0,
    }


__all__ = [
    "ALLOWED_TOOLS",
    "CampusInvalid",
    "delete",
    "ensure",
    "get",
    "list_all",
    "load_snapshot",
    "put",
    "reset_cache",
]
