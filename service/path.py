# -*- coding: utf-8 -*-
"""关卡式学习路径：把学生在知穗里的真实行为折算成 12 个可通关的关卡。

为什么这么做（而不是画一张流程图）：
  申报书 3.4 写「以关卡式学习路径降低入门门槛」，2.8 把「学习路径进度」列为
  三类输出形态之一。如果关卡进度是手工点出来的勾，演示时评委问一句「这一关
  凭什么算过」就答不上；所以每一关的完成条件都绑定到**已有数据表**，
  读的时候现算，不做任何手工打分，也没有后台配置界面。

★ 核心取舍：读时重算，不写时维护。
  「掌握多少词」「连打几天卡」都是聚合量。如果在写路径去维护计数器，
  必然和真实数据漂移（漏加、重复加、跨表对不上），而这类错误只有在演示当天
  才会被发现。因此 overview() 每次现算，**只有「首次达成时间」落库**——
  时间轴和「你哪天真过去了」需要的恰好是那个时间戳，而不是一个会和事实
  对不上的计数。

与 service/checkin.py、service/cet.py 保持同一套约定：
  - 复用 sessions.db，新表懒建、幂等、不在导入期写库；
  - owner 强制隔离，所有查询带 WHERE owner_id = ?；
  - 不做排行榜：路径是给自己看的，不是用来比大小的。
"""

from __future__ import annotations

import time
from typing import Any

from core import quota
from core import session as store
from service import cet as cet_svc
from service import checkin as checkin_svc
from service import daily as daily_svc

# ==================== 关卡定义 ====================
#
# 顺序即通关顺序：前一关没过，后面的关卡一律 locked。
# target 允许写成字符串 —— 表示「目标值本身来自数据」（如模考目标分取用户自己填的 target）。

_CHAPTERS: list[dict[str, str]] = [
    {"id": "ch1", "name": "起步", "desc": "把资料放进来，问出第一个问题"},
    {"id": "ch2", "name": "备考", "desc": "把计划落到每一天"},
    {"id": "ch3", "name": "实战", "desc": "真题与词汇的硬仗"},
    {"id": "ch4", "name": "收口", "desc": "让系统替你跑起来"},
]

_LEVELS: list[dict[str, Any]] = [
    {
        "id": "L01",
        "chapter": "ch1",
        "name": "建库",
        "desc": "创建 1 个属于自己的知识库：资料归自己，回答才有依据。",
        "action": "/",
        "action_label": "去工作台",
        "checks": [{"key": "kb_count", "target": 1, "label": "知识库", "unit": "个"}],
    },
    {
        "id": "L02",
        "chapter": "ch1",
        "name": "入库",
        "desc": "往库里放 3 份资料（课件、真题、笔记都算）。",
        "action": "/",
        "action_label": "去上传",
        "checks": [{"key": "kb_files", "target": 3, "label": "已入库资料", "unit": "份"}],
    },
    {
        "id": "L03",
        "chapter": "ch1",
        "name": "首问",
        "desc": "带着知识库问第一个问题，回答会带原文出处。",
        "action": "/",
        "action_label": "去提问",
        "checks": [{"key": "ask_count", "target": 1, "label": "已提问", "unit": "次"}],
    },
    {
        "id": "L04",
        "chapter": "ch2",
        "name": "立计划",
        "desc": "生成一份备考计划，把听力、词汇、阅读、写作翻译、真题排进每天。",
        "action": "/cet",
        "action_label": "去生成计划",
        "checks": [{"key": "plan_count", "target": 1, "label": "备考计划", "unit": "份"}],
    },
    {
        "id": "L05",
        "chapter": "ch2",
        "name": "稳节奏",
        "desc": "连续打卡 3 天。备考拼的不是某天很猛，而是每天都来。",
        "action": "/checkin",
        "action_label": "去打卡",
        "checks": [{"key": "streak", "target": 3, "label": "连续打卡", "unit": "天"}],
    },
    {
        "id": "L06",
        "chapter": "ch2",
        "name": "词汇地基",
        "desc": "词汇本里掌握 100 个词（复习过至少一档才算掌握）。",
        "action": "/cet",
        "action_label": "去背词",
        "checks": [{"key": "vocab_mastered", "target": 100, "label": "已掌握", "unit": "词"}],
    },
    {
        "id": "L07",
        "chapter": "ch2",
        "name": "首测",
        "desc": "录入第一次模考分数，先知道自己站在哪。",
        "action": "/cet",
        "action_label": "去记分数",
        "checks": [{"key": "score_count", "target": 1, "label": "分数记录", "unit": "条"}],
    },
    {
        "id": "L08",
        "chapter": "ch3",
        "name": "真题推进",
        "desc": "完成 10 套真题。真题是最好的复习资料，没有之一。",
        "action": "/cet",
        "action_label": "去刷真题",
        "checks": [{"key": "real_done", "target": 10, "label": "已完成真题", "unit": "套"}],
    },
    {
        "id": "L09",
        "chapter": "ch3",
        "name": "词汇攻坚",
        "desc": "累计掌握 300 个词（复习到第二档以上）。",
        "action": "/cet",
        "action_label": "去背词",
        "checks": [{"key": "vocab_solid", "target": 300, "label": "稳固掌握", "unit": "词"}],
    },
    {
        "id": "L10",
        "chapter": "ch3",
        "name": "达标",
        "desc": "模考最高分达到你设定的目标线。",
        "action": "/cet",
        "action_label": "去记分数",
        "checks": [
            {"key": "best_score", "target": "score_target", "label": "最高分", "unit": "分"},
        ],
    },
    {
        "id": "L11",
        "chapter": "ch4",
        "name": "复盘沉淀",
        "desc": "积累 7 份每日总结。总结过的东西才会变成自己的。",
        "action": "/report",
        "action_label": "去看总结",
        "checks": [{"key": "report_count", "target": 7, "label": "每日总结", "unit": "份"}],
    },
    {
        "id": "L12",
        "chapter": "ch4",
        "name": "自动托管",
        "desc": "启用 1 个每日委托，并连续打卡 7 天——之后它每天替你把活干了。",
        "action": "/delegate",
        "action_label": "去建委托",
        "checks": [
            {"key": "del_enabled", "target": 1, "label": "启用中的委托", "unit": "个"},
            {"key": "streak", "target": 7, "label": "连续打卡", "unit": "天"},
        ],
    },
]

_TOTAL = len(_LEVELS)
_DEFAULT_SCORE_TARGET = 425


# ==================== 表 ====================

def _ensure_path_tables() -> None:
    """只建一张表：记录每关**首次达成**的时间（幂等）。"""
    with store._get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS path_progress (
                owner_id TEXT NOT NULL,
                node_id  TEXT NOT NULL,
                done_at  INTEGER NOT NULL,
                PRIMARY KEY (owner_id, node_id)
            );
            """
        )


# ==================== 指标采集 ====================

def _kb_counts(uid: str) -> tuple[int, int]:
    """返回 (知识库个数, 已登记资料份数)。

    只读注册表（内存 + data/kb_*.json），**不开 Chroma collection**：
    路径页每打开一次就把所有向量库扫一遍，代价太大，而且会被磁盘 I/O 拖住。
    读不出来时降级成 0 —— 知识库清单坏了不该让整页 500，其余关卡照常算。
    """
    try:
        from kb import pipelines

        kb_ids = pipelines.owned_kb_ids(uid)
        if not kb_ids:
            return 0, 0
        # warmup() 正常在启动时已加载清单；测试进程或未跑 warmup 时补一次（幂等）。
        with pipelines._REGISTRY_LOCK:
            pipelines._load_files()
        files = sum(len(pipelines.get_file_names(uid, kb_id)) for kb_id in kb_ids)
        return len(kb_ids), files
    except Exception:
        return 0, 0


def _metrics(uid: str) -> dict[str, int]:
    """一次把所有关卡的度量值算出来。全靠聚合查询，不读 Chroma、不调模型。"""
    _ensure_path_tables()
    cet_svc._ensure_cet_tables()
    checkin_svc._ensure_checkin_tables()
    daily_svc._ensure_daily_tables()

    kb_count, kb_files = _kb_counts(uid)

    with store._get_conn() as conn:
        ask_count = conn.execute(
            "SELECT COUNT(*) AS n FROM messages m "
            "JOIN conversations c ON c.id = m.conversation_id "
            "WHERE c.owner_id = ? AND m.role = 'user'",
            (uid,),
        ).fetchone()["n"]
        plan_count = conn.execute(
            "SELECT COUNT(*) AS n FROM cet_plans WHERE owner_id = ?", (uid,)
        ).fetchone()["n"]
        # step 0 = 刚入本，未复习过；≥1 才算「过了一档」，≥2 才算稳。
        vocab_mastered = conn.execute(
            "SELECT COUNT(*) AS n FROM vocab_items WHERE owner_id = ? AND step >= 1", (uid,)
        ).fetchone()["n"]
        vocab_solid = conn.execute(
            "SELECT COUNT(*) AS n FROM vocab_items WHERE owner_id = ? AND step >= 2", (uid,)
        ).fetchone()["n"]
        score_rows = conn.execute(
            "SELECT score, target FROM cet_scores WHERE owner_id = ?", (uid,)
        ).fetchall()
        real_done = conn.execute(
            "SELECT COUNT(*) AS n FROM cet_tasks "
            "WHERE owner_id = ? AND module = '真题' AND done = 1",
            (uid,),
        ).fetchone()["n"]
        report_count = conn.execute(
            "SELECT COUNT(*) AS n FROM daily_reports WHERE owner_id = ? AND status = 'ready'",
            (uid,),
        ).fetchone()["n"]
        del_enabled = conn.execute(
            "SELECT COUNT(*) AS n FROM delegations WHERE owner_id = ? AND enabled = 1", (uid,)
        ).fetchone()["n"]

    # 模考目标：取用户自己填过的最高目标，没填过按 425（四六级及格线）兜底。
    score_target = max([int(r["target"] or _DEFAULT_SCORE_TARGET) for r in score_rows] or [_DEFAULT_SCORE_TARGET])
    best_score = max([int(r["score"] or 0) for r in score_rows] or [0])

    return {
        "kb_count": int(kb_count),
        "kb_files": int(kb_files),
        "ask_count": int(ask_count),
        "plan_count": int(plan_count),
        "streak": int(checkin_svc.compute_streak(uid).get("streak") or 0),
        "vocab_mastered": int(vocab_mastered),
        "vocab_solid": int(vocab_solid),
        "score_count": len(score_rows),
        "best_score": best_score,
        "score_target": score_target,
        "real_done": int(real_done),
        "report_count": int(report_count),
        "del_enabled": int(del_enabled),
    }


# ==================== 聚合 ====================

def _resolve_target(raw: Any, m: dict[str, int]) -> int:
    """target 允许是数字或指标名（如 "score_target" 由用户数据决定）。"""
    if isinstance(raw, str):
        return int(m.get(raw) or 0)
    return int(raw)


def _evaluate(spec: dict[str, Any], m: dict[str, int]) -> dict[str, Any]:
    """算一关的完成情况 + 展示文案。"""
    checks: list[dict[str, Any]] = []
    ratio = 1.0
    for c in spec["checks"]:
        target = _resolve_target(c["target"], m)
        value = int(m.get(c["key"]) or 0)
        passed = value >= target if target > 0 else True
        share = 1.0 if target <= 0 else max(0.0, min(1.0, value / target))
        ratio = min(ratio, share)
        checks.append(
            {
                "key": c["key"],
                "label": c["label"],
                "unit": c["unit"],
                "value": value,
                "target": target,
                "passed": passed,
            }
        )

    ok = all(c["passed"] for c in checks)
    # 文案取「第一项未达成」的条件，避免多条件关卡显示成一串数字。
    focus = next((c for c in checks if not c["passed"]), checks[0] if checks else None)
    detail = ""
    if focus:
        detail = f"{focus['label']} {focus['value']} / {focus['target']} {focus['unit']}"
    return {
        "ok": ok,
        "ratio": round(ratio, 4),
        "percent": int(round(ratio * 100)),
        "checks": checks,
        "detail": detail,
    }


def overview(uid: str) -> dict[str, Any]:
    """路径页首屏：章节 / 12 关三态 / 总进度 / 当前该做哪一关。"""
    m = _metrics(uid)

    evs = [_evaluate(spec, m) for spec in _LEVELS]
    # 第一个没达成的关卡 = 「当前该做的那一关」（进度条下面那个 CTA）
    first_open = next((i for i, ev in enumerate(evs) if not ev["ok"]), None)

    levels: list[dict[str, Any]] = []
    done_ids: list[str] = []
    for i, (spec, ev) in enumerate(zip(_LEVELS, evs)):
        if ev["ok"]:
            status = "done"
            done_ids.append(spec["id"])
        elif i == first_open or ev["percent"] > 0:
            # 已经开始做的关卡不该显示成「未解锁」——那是在说用户没动手，而事实是
            # 他走了 45%，只是还没到线。只有一步没走的关卡才配得上「未解锁」。
            status = "current"
        else:
            status = "locked"
        levels.append(
            {
                "id": spec["id"],
                "chapter": spec["chapter"],
                "name": spec["name"],
                "desc": spec["desc"],
                "action": spec["action"],
                "action_label": spec["action_label"],
                "status": status,
                "detail": ev["detail"],
                "percent": ev["percent"],
                "checks": ev["checks"],
                "done_at": None,
            }
        )

    # 只落「首次达成时间」：INSERT OR IGNORE，已存在就不覆盖。
    now = int(time.time())
    if done_ids:
        with store._get_conn() as conn:
            for lid in done_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO path_progress(owner_id, node_id, done_at) VALUES (?, ?, ?)",
                    (uid, lid, now),
                )

    done_at: dict[str, int] = {}
    with store._get_conn() as conn:
        for r in conn.execute(
            "SELECT node_id, done_at FROM path_progress WHERE owner_id = ?", (uid,)
        ).fetchall():
            done_at[r["node_id"]] = int(r["done_at"])
    for lv in levels:
        # 条件可能回退（如连续打卡断掉）：只有仍然达成的关卡才挂时间戳。
        lv["done_at"] = done_at.get(lv["id"]) if lv["status"] == "done" else None

    chapters = []
    for ch in _CHAPTERS:
        lv = [x for x in levels if x["chapter"] == ch["id"]]
        chapters.append(
            {
                **ch,
                "levels": lv,
                "done": sum(1 for x in lv if x["status"] == "done"),
                "total": len(lv),
            }
        )

    done = len(done_ids)
    # 指路用「第一个没达成的关卡」，不是「第一个 current」：
    # 顺序上更早的关卡哪怕已有进展，也仍然是该先补的那一关。
    current = levels[first_open] if first_open is not None else None
    return {
        "progress": {
            "done": done,
            "total": _TOTAL,
            "percent": int(round(done / _TOTAL * 100)) if _TOTAL else 0,
        },
        "chapters": chapters,
        "current": current,
        "today": quota.day_key(),
        "metrics": m,
    }
