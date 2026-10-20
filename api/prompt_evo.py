"""Prompt 自进化的 HTTP 端点（GEPA · issue #12）。

  GET  /api/prompt-evo/status                  总览：样本攒料进度 / 各 path 版本 / 最近评估
  GET  /api/prompt-evo/variants                候选列表（可按 path / status 过滤）
  GET  /api/prompt-evo/variants/{vid}          单个候选详情（含 diff 与评估留档）
  GET  /api/prompt-evo/replay-sets?path=       回放集版本历史
  POST /api/prompt-evo/run                     手动触发一轮（管理员，投递给 worker）
  POST /api/prompt-evo/variants/{vid}/promote  晋升为 active（管理员）
  POST /api/prompt-evo/rollback                回退到上一版 / 指定版本 / 内置基线（管理员）

权限口径：**所有端点默认要登录**（router 级 Depends）；三个写操作额外要管理员。
promote 之所以不自动化：与 CODEOWNERS / PR 文化一致——候选只到 shadow，
上线这一步留给人（`PROMPT_EVO_AUTO_PROMOTE` 默认关，只在演示环境打开）。
"""

from __future__ import annotations

import asyncio
from functools import partial

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core import config, prompt_store, quota
from core import scheduler as sched
from core.auth import get_current_user
from service import prompt_evo

router = APIRouter(dependencies=[Depends(get_current_user)])


def _require_admin(uid: str) -> None:
    """管理员判定复用 `core/quota._is_admin`（与 /api/schedule 同一口径）。

    不在这里重写一份：两处规则一旦漂移，会出现「配额系统认为你是管理员、
    这里认为你不是」这种最难查的 bug。
    """
    if not quota._is_admin(uid):
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _variant_brief(v: dict) -> dict:
    return {
        "id": v["id"],
        "path": v["path"],
        "version": v["version"],
        "parent_id": v["parent_id"],
        "status": v["status"],
        "created_by": v["created_by"],
        "created_at": v["created_at"],
        "prompt_chars": len(v.get("prompt_text") or ""),
        "metrics": v.get("metrics"),
        "diff": v.get("diff"),
    }


class RunRequest(BaseModel):
    path: str | None = Field(default=None, description="进化哪条提示词；缺省取第一条配置")
    dry_run: bool = Field(default=False, description="true 时只出候选、不评估不落库")


class PromoteRequest(BaseModel):
    note: str | None = Field(default=None, description="可选备注（写日志用）")


class RollbackRequest(BaseModel):
    path: str = Field(..., description="agent / rag_note_low / rag_note_medium")
    to_version: int | None = Field(default=None, description="缺省回到上一个生效版本；0 = 内置基线")


@router.get("/status")
async def prompt_evo_status() -> dict:
    return await asyncio.to_thread(prompt_evo.status)


@router.get("/variants")
async def prompt_evo_variants(
    path: str | None = None, status: str | None = None, limit: int = 30
) -> dict:
    paths = prompt_evo.known_paths()
    if path and path not in paths:
        raise HTTPException(status_code=400, detail=f"未知 path，可选：{', '.join(paths)}")
    rows = await asyncio.to_thread(
        partial(prompt_store.list_variants, path=path, status=status, limit=limit)
    )
    return {"items": [_variant_brief(v) for v in rows], "paths": list(paths)}


@router.get("/variants/{vid}")
async def prompt_evo_variant(vid: int) -> dict:
    v = await asyncio.to_thread(prompt_store.get_variant, vid)
    if v is None:
        raise HTTPException(status_code=404, detail="候选不存在")
    evals = await asyncio.to_thread(prompt_store.list_evals, v["path"], vid, 10)
    out = _variant_brief(v)
    out["prompt_text"] = v["prompt_text"]
    out["evals"] = evals
    return out


@router.get("/replay-sets")
async def prompt_evo_replay_sets(path: str, limit: int = 10) -> dict:
    if path not in prompt_evo.known_paths():
        raise HTTPException(status_code=400, detail="未知 path")
    sets = await asyncio.to_thread(prompt_evo.list_sets, path, limit)
    return {"path": path, "items": sets, "cases": await asyncio.to_thread(prompt_evo.case_counts)}


@router.post("/run", status_code=202)
async def prompt_evo_run(req: RunRequest, uid: str = Depends(get_current_user)) -> dict:
    """手动触发一轮：只登记 pending，由 worker 下一轮扫到执行。

    与定时触发走**同一条执行路径**（`sched.enqueue_job`），不另开同步分支——
    否则会出现"手动能跑、定时不能跑"这类只在生产暴露的差异。
    """
    await asyncio.to_thread(quota.hit, uid)
    _require_admin(uid)
    paths = prompt_evo.known_paths()
    if not paths:
        raise HTTPException(status_code=400, detail="没有配置任何可进化的 path")
    path = req.path or paths[0]
    if path not in paths:
        raise HTTPException(status_code=400, detail=f"未知 path，可选：{', '.join(paths)}")
    if req.dry_run:
        rep = await asyncio.to_thread(prompt_evo.run_evolution, path, dry_run=True)
        return {"status": "dry_run", "report": rep}
    run_id, idem_key = await asyncio.to_thread(
        partial(sched.enqueue_job, prompt_evo.JOB_KEY, path, sched.now_ts(), trigger="manual")
    )
    return {
        "queued": bool(run_id),
        "run_id": run_id,
        "idem_key": idem_key,
        "path": path,
        "note": "已登记，worker 下一轮执行；重复触发会被幂等键吞掉",
    }


@router.post("/variants/{vid}/promote")
async def prompt_evo_promote(
    vid: int, req: PromoteRequest, uid: str = Depends(get_current_user)
) -> dict:
    await asyncio.to_thread(quota.hit, uid)
    _require_admin(uid)
    v = await asyncio.to_thread(prompt_store.get_variant, vid)
    if v is None:
        raise HTTPException(status_code=404, detail="候选不存在")
    if v["status"] == prompt_store.STATUS_REJECTED:
        raise HTTPException(
            status_code=409,
            detail="该候选没过闸门（CI 下界 / 成本 / 延迟未达标），不能晋升",
        )
    out = await asyncio.to_thread(prompt_store.promote, vid, f"manual:{uid}")
    return {"promoted": out, "note": req.note or ""}


@router.post("/rollback")
async def prompt_evo_rollback(req: RollbackRequest, uid: str = Depends(get_current_user)) -> dict:
    await asyncio.to_thread(quota.hit, uid)
    _require_admin(uid)
    if req.path not in prompt_evo.known_paths():
        raise HTTPException(status_code=400, detail="未知 path")
    if req.to_version == 0:
        # 回到内置基线：rollback(path, 0) 查不到 version=0 的行 → 把 active 全部 retired，
        # resolve() 于是退回代码常量。不需要额外分支。
        out = await asyncio.to_thread(prompt_store.rollback, req.path, 0, f"manual:{uid}")
        return {"rolled_back": True, "path": req.path, **out, "to_builtin": True}
    out = await asyncio.to_thread(prompt_store.rollback, req.path, req.to_version, f"manual:{uid}")
    return {"rolled_back": True, "path": req.path, **out}


@router.get("/config")
async def prompt_evo_config() -> dict:
    """当前生效的闸门 / 开关（前端"为什么这轮没产出"的排查入口）。"""
    return {
        "enabled": bool(getattr(config, "PROMPT_EVO_ENABLED", True)),
        "auto_promote": bool(getattr(config, "PROMPT_EVO_AUTO_PROMOTE", False)),
        "paths": list(prompt_evo.known_paths()),
        "cron": sched.JOB_DEFS.get(prompt_evo.JOB_KEY, {}).get("cron"),
        "replay_min": int(getattr(config, "PROMPT_EVO_REPLAY_MIN", 200)),
        "replay_max": int(getattr(config, "PROMPT_EVO_REPLAY_MAX", 300)),
        "max_drop_pp": float(getattr(config, "PROMPT_EVO_MAX_DROP", 0.05)) * 100,
        "ci_alpha": float(getattr(config, "PROMPT_EVO_CI_ALPHA", 0.05)),
        "max_diff_lines": int(getattr(config, "PROMPT_EVO_MAX_DIFF_LINES", 6)),
        "judge": str(getattr(config, "PROMPT_EVO_JUDGE", "heuristic")),
    }
