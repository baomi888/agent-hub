"""提示词版本化存储（GEPA 闭环的第 ⑤ 层）。

为什么要有这一层
----------------
在它之前，提示词是**硬编码常量**（`agent/builder.py::SYSTEM_PROMPT`、
`kb/rag_core.py::LOW_CONFIDENCE_NOTE`），既不能版本化、也不能离线测量，
于是"按 badcase 改提示词"只能靠人工一次一次改代码。这一层把提示词变成
**数据**：可存快照、可对比指标、可 promote / rollback。

三条设计决定
------------
1. **version=0 = 内置基线，不落库。** 基线永远从代码常量读（`default` 参数），
   库里只存"改过的版本"。好处是：数据库清空 / 迁移 / 新环境首次启动时，
   行为自动退回代码里的基线，而不是"没有提示词可用"。
2. **一条 path 同时只有一个 active。** promote 在一个事务里把同 path 其它
   active 置 retired，再置新的 active——不做"先查再写"的两段式，
   否则并发 promote 会出现两个 active。
3. **读侧绝不抛异常。** `resolve()` 是每轮问答都要走的热路径，DB 写锁/
   表缺失都不该让问答失败：任何异常都退回内置基线（并记一条 warning）。

进程内缓存的取舍
----------------
`build_agent` 每轮都要拿版本号（它进 `_AGENT_CACHE` 的键）。每次都读库
没必要，缓存 TTL 默认 2s：promote 之后最多 2s 全进程生效，而正常问答
一个 tick 内只读一次。`PROMPT_EVO_CACHE_TTL=0` 可关掉缓存。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from core import config
from core import session as store

_log = logging.getLogger("core.prompt_store")

# 候选生命周期。rejected 不在原方案的四个状态里，但**必须有**：
# 没过闸门的候选要留档（不然"为什么这轮没产出"永远查不到，只剩一条空日志）。
STATUS_CANDIDATE = "candidate"
STATUS_SHADOW = "shadow"
STATUS_ACTIVE = "active"
STATUS_RETIRED = "retired"
STATUS_REJECTED = "rejected"
STATUSES = (STATUS_CANDIDATE, STATUS_SHADOW, STATUS_ACTIVE, STATUS_RETIRED, STATUS_REJECTED)

# path -> (expire_at_monotonic, prompt_text, version)
_CACHE: dict[str, tuple[float, str, int]] = {}
_LOCK = threading.Lock()


def _now() -> int:
    return int(time.time())


def _ttl() -> float:
    try:
        return float(getattr(config, "PROMPT_EVO_CACHE_TTL", 2) or 0)
    except (TypeError, ValueError):
        return 2.0


def invalidate(path: str | None = None) -> None:
    """清进程内缓存（promote / rollback 之后必须调用）。"""
    with _LOCK:
        if path is None:
            _CACHE.clear()
        else:
            _CACHE.pop(path, None)


# ==================== 读侧 ====================


def _active_row(path: str) -> dict | None:
    with store._get_conn() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT id, path, version, prompt_text, metrics_json, created_at "
            "FROM prompt_variants WHERE path = ? AND status = ? "
            "ORDER BY version DESC LIMIT 1",
            (path, STATUS_ACTIVE),
        ).fetchone()
    return dict(row) if row else None


def resolve(path: str, default: str) -> tuple[str, int]:
    """取该 path 当前生效的提示词，返回 (文本, 版本号)。

    版本号 0 表示"内置基线"（库里没有 active 版本）。**版本号必须回传给调用方**：
    `agent/builder.py` 的编译缓存键要带上它，否则 promote 之后缓存不失效，
    改了提示词线上毫无反应——这是本方案最容易踩的坑（方案 §4 已点名）。
    """
    if not getattr(config, "PROMPT_EVO_ENABLED", True):
        return default, 0

    ttl = _ttl()
    if ttl > 0:
        hit = _CACHE.get(path)
        if hit is not None and hit[0] > time.monotonic():
            return hit[1], hit[2]

    try:
        row = _active_row(path)
    except Exception as e:  # noqa: BLE001 —— 热路径不允许因存储故障挂掉问答
        _log.warning("prompt_store.resolve failed path=%s err=%s", path, e)
        return default, 0

    if not row or not row.get("prompt_text"):
        text, ver = default, 0
    else:
        text, ver = str(row["prompt_text"]), int(row["version"] or 0)

    if ttl > 0:
        _CACHE[path] = (time.monotonic() + ttl, text, ver)
    return text, ver


def active_version(path: str) -> int:
    """当前 active 版本号（0 = 内置基线）。给 API / 文档用。"""
    try:
        row = _active_row(path)
    except Exception:  # noqa: BLE001
        return 0
    return int(row["version"]) if row else 0


def active_variant(path: str) -> dict | None:
    """当前 active 的完整行（无 active 返回 None）。给变异层记 parent_id 用。"""
    try:
        return _active_row(path)
    except Exception:  # noqa: BLE001
        return None


def get_variant(variant_id: int) -> dict | None:
    with store._get_conn() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT * FROM prompt_variants WHERE id = ?", (int(variant_id),)
        ).fetchone()
    return _decode_variant(dict(row)) if row else None


def list_variants(
    path: str | None = None, status: str | None = None, limit: int = 50
) -> list[dict]:
    sql = "SELECT * FROM prompt_variants WHERE 1=1"
    args: list[Any] = []
    if path:
        sql += " AND path = ?"
        args.append(path)
    if status:
        sql += " AND status = ?"
        args.append(status)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, min(int(limit), 200)))
    with store._get_conn() as conn:  # noqa: SLF001
        rows = conn.execute(sql, args).fetchall()
    return [_decode_variant(dict(r)) for r in rows]


def _decode_variant(row: dict) -> dict:
    for key in ("diff_json", "metrics_json"):
        raw = row.get(key)
        if raw:
            try:
                row[key.replace("_json", "")] = json.loads(raw)
            except (TypeError, ValueError):
                row[key.replace("_json", "")] = None
        else:
            row[key.replace("_json", "")] = None
    return row


# ==================== 写侧 ====================


def next_version(path: str) -> int:
    """该 path 下一个可用版本号（= 当前最大 + 1）。

    `ux_prompt_variant(path, version)` 是最终防线：两个 worker 同时算出同一
    版本号时，后写的那条会因唯一约束失败而重算——不是静默覆盖。
    """
    with store._get_conn() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT MAX(version) AS v FROM prompt_variants WHERE path = ?", (path,)
        ).fetchone()
    return int(row["v"] or 0) + 1


def create_variant(
    path: str,
    prompt_text: str,
    *,
    parent_id: int | None = None,
    diff: list[dict] | None = None,
    metrics: dict | None = None,
    status: str = STATUS_CANDIDATE,
    created_by: str = "manual",
) -> int:
    """落一条候选快照，返回 variant id。"""
    if status not in STATUSES:
        raise ValueError(f"未知 status: {status}")
    version = next_version(path)
    with store._get_conn() as conn:  # noqa: SLF001
        cur = conn.execute(
            "INSERT INTO prompt_variants "
            "(path, version, parent_id, diff_json, prompt_text, metrics_json, status, "
            " created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                path,
                version,
                parent_id,
                json.dumps(diff, ensure_ascii=False) if diff is not None else None,
                prompt_text,
                json.dumps(metrics, ensure_ascii=False) if metrics is not None else None,
                status,
                created_by,
                _now(),
            ),
        )
        vid = int(cur.lastrowid)
    invalidate(path)
    return vid


def set_metrics(variant_id: int, metrics: dict) -> None:
    with store._get_conn() as conn:  # noqa: SLF001
        conn.execute(
            "UPDATE prompt_variants SET metrics_json = ? WHERE id = ?",
            (json.dumps(metrics, ensure_ascii=False), int(variant_id)),
        )


def set_status(variant_id: int, status: str) -> None:
    if status not in STATUSES:
        raise ValueError(f"未知 status: {status}")
    var = get_variant(variant_id)
    if var is None:
        raise KeyError(f"variant 不存在: {variant_id}")
    with store._get_conn() as conn:  # noqa: SLF001
        conn.execute(
            "UPDATE prompt_variants SET status = ? WHERE id = ?", (status, int(variant_id))
        )
    invalidate(var["path"])


def promote(variant_id: int, by: str = "manual") -> dict:
    """把某个候选置为 active，同 path 原 active 转 retired。

    全程一个事务：先 retire 再 active，中间不会出现"零 active"被读到的窗口
    （SQLite 的写事务对读是隔离的；读侧要么看到旧的 active，要么看到新的）。
    """
    var = get_variant(variant_id)
    if var is None:
        raise KeyError(f"variant 不存在: {variant_id}")
    if var["status"] == STATUS_ACTIVE:
        return {"changed": False, "reason": "已经是 active", "variant": var}
    path = var["path"]
    with store._get_conn() as conn:  # noqa: SLF001
        conn.execute(
            "UPDATE prompt_variants SET status = ? WHERE path = ? AND status = ?",
            (STATUS_RETIRED, path, STATUS_ACTIVE),
        )
        conn.execute(
            "UPDATE prompt_variants SET status = ? WHERE id = ?", (STATUS_ACTIVE, int(variant_id))
        )
    invalidate(path)
    return {
        "changed": True,
        "path": path,
        "version": var["version"],
        "id": int(variant_id),
        "by": by,
    }


def rollback(path: str, to_version: int | None = None, by: str = "rollback") -> dict:
    """回退：默认回到"上一个 active 之前的版本"。

    `to_version` 缺失时按「version 小于当前 active 的最大版本」取，
    也就是**上一次生效过的**那版；再没有就回内置基线（retire 全部 active）。
    """
    cur = _active_row(path)
    cur_version = int(cur["version"]) if cur else 0
    with store._get_conn() as conn:  # noqa: SLF001
        if to_version is None:
            row = conn.execute(
                "SELECT id, version FROM prompt_variants WHERE path = ? AND version < ? "
                "AND status IN (?,?) ORDER BY version DESC LIMIT 1",
                (path, cur_version, STATUS_RETIRED, STATUS_ACTIVE),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id, version FROM prompt_variants WHERE path = ? AND version = ?",
                (path, int(to_version)),
            ).fetchone()
        if row is None:
            conn.execute(
                "UPDATE prompt_variants SET status = ? WHERE path = ? AND status = ?",
                (STATUS_RETIRED, path, STATUS_ACTIVE),
            )
            invalidate(path)
            return {"changed": cur is not None, "path": path, "version": 0, "to_builtin": True}
    out = promote(int(row["id"]), by=by)
    out["to_builtin"] = False
    return out


# ==================== 评估留档 ====================


def record_eval(
    path: str,
    variant_id: int,
    replay_set_version: int,
    per_case: list[dict],
    summary: dict,
    elapsed_ms: int = 0,
) -> int:
    with store._get_conn() as conn:  # noqa: SLF001
        cur = conn.execute(
            "INSERT INTO prompt_evals "
            "(path, variant_id, replay_set_version, per_case_json, summary_json, "
            " elapsed_ms, created_at) VALUES (?,?,?,?,?,?,?)",
            (
                path,
                int(variant_id),
                int(replay_set_version),
                json.dumps(per_case, ensure_ascii=False),
                json.dumps(summary, ensure_ascii=False),
                int(elapsed_ms),
                _now(),
            ),
        )
        return int(cur.lastrowid)


def latest_eval(path: str, variant_id: int, replay_set_version: int) -> dict | None:
    """同一 (path, 版本, 回放集版本) 的最近一次评估（基线指标复用，省一半 judge 成本）。"""
    with store._get_conn() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT * FROM prompt_evals WHERE path = ? AND variant_id = ? "
            "AND replay_set_version = ? ORDER BY id DESC LIMIT 1",
            (path, int(variant_id), int(replay_set_version)),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["per_case"] = json.loads(d["per_case_json"])
        d["summary"] = json.loads(d["summary_json"])
    except (TypeError, ValueError):
        return None
    return d


def list_evals(path: str, variant_id: int | None = None, limit: int = 10) -> list[dict]:
    sql = "SELECT id, path, variant_id, replay_set_version, summary_json, elapsed_ms, created_at " \
          "FROM prompt_evals WHERE path = ?"
    args: list[Any] = [path]
    if variant_id is not None:
        sql += " AND variant_id = ?"
        args.append(int(variant_id))
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, min(int(limit), 100)))
    with store._get_conn() as conn:  # noqa: SLF001
        rows = conn.execute(sql, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["summary"] = json.loads(d.pop("summary_json"))
        except (TypeError, ValueError):
            d["summary"] = {}
        out.append(d)
    return out
