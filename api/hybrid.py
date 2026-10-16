# -*- coding: utf-8 -*-
"""混合架构管理接口：工作流定义 / 切换规则 / 执行记录与日志（要求 2/5/6 的 API 面）。

归属约定与 store 层一致：
  - 系统内置（owner_id NULL）只读，改删一律 404；
  - 用户只能看到/改到自己的行，owner 一律取登录态，不信请求体。

整组默认要登录（与 api/chat.py 同一约定：挂在 router 级 dependencies 上，
新增端点忘了写鉴权也是默认安全的那一侧）。
"""

from __future__ import annotations

import ast

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.auth import get_current_user
from hybrid import store
from hybrid.context import ModeSwitch, TaskContext
from hybrid.orchestrator import request_manual_switch
from hybrid.workflow import validate_definition

router = APIRouter(dependencies=[Depends(get_current_user)])

# 切换动作的合法值（与 switch_rules 表的 CHECK 约束一致）
_RULE_ACTIONS = ("to_agent", "to_workflow", "terminate")


# ==================== 工作流定义 ====================

class WorkflowIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    description: str = Field(default="", max_length=500)
    definition: dict


@router.get("/workflows")
def list_workflows(uid: str = Depends(get_current_user)) -> dict:
    """系统内置 + 当前用户的全部工作流。"""
    return {"items": store.list_workflows(uid)}


@router.post("/workflows")
def create_workflow(req: WorkflowIn, uid: str = Depends(get_current_user)) -> dict:
    # 落库前必过图完整性校验：id 唯一 / 边与跳转指向存在 / 入口存在，
    # 这些错了运行期才炸就晚了
    try:
        validate_definition(req.definition)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return store.create_workflow(uid, req.name, req.description, req.definition)


@router.get("/workflows/{wf_id}")
def get_workflow(wf_id: str, uid: str = Depends(get_current_user)) -> dict:
    row = store.get_workflow(uid, wf_id)
    if not row:
        raise HTTPException(status_code=404, detail="工作流不存在")
    return row


@router.put("/workflows/{wf_id}")
def update_workflow(wf_id: str, req: WorkflowIn, uid: str = Depends(get_current_user)) -> dict:
    try:
        validate_definition(req.definition)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not store.update_workflow(uid, wf_id, req.name, req.description, req.definition):
        raise HTTPException(status_code=404, detail="工作流不存在或为系统内置（不可修改）")
    return store.get_workflow(uid, wf_id)


@router.delete("/workflows/{wf_id}")
def delete_workflow(wf_id: str, uid: str = Depends(get_current_user)) -> dict:
    if not store.delete_workflow(uid, wf_id):
        raise HTTPException(status_code=404, detail="工作流不存在或为系统内置（不可删除）")
    return {"ok": True}


# ==================== 切换规则 ====================

class RuleIn(BaseModel):
    signal: str = Field(..., min_length=1, max_length=40)
    condition: str = Field(default="", max_length=500)
    action: str = Field(..., description="to_agent / to_workflow / terminate")
    reason: str = Field(default="", max_length=200)
    priority: int = Field(default=100, ge=0, le=9999)
    enabled: bool = True
    cooldown_seconds: int = Field(default=30, ge=0, le=86400)


@router.get("/rules")
def list_rules(uid: str = Depends(get_current_user)) -> dict:
    """当前用户的生效规则集（系统默认 + 用户同名覆盖）。"""
    return {"items": store.list_rules(uid)}


@router.put("/rules/{name}")
def upsert_rule(name: str, req: RuleIn, uid: str = Depends(get_current_user)) -> dict:
    """写用户级规则：与系统默认同名即覆盖，不同名则并存新增。"""
    if req.action not in _RULE_ACTIONS:
        raise HTTPException(status_code=400, detail=f"非法的切换动作：{req.action}")
    # 条件表达式做语法级校验：存个根本解析不了的条件等于埋雷。
    # 语义（名字是否存在）不在这里验：safe_eval 的作用域是运行期注入的，
    # 静态全量求值会把合法的嵌套访问误杀
    if req.condition.strip():
        try:
            ast.parse(req.condition.strip(), mode="eval")
        except SyntaxError as e:
            raise HTTPException(status_code=400, detail=f"条件表达式语法错误：{e}")
    store.upsert_rule(
        uid, name, req.signal, req.condition, req.action,
        req.reason, req.priority, req.enabled, req.cooldown_seconds,
    )
    return {"ok": True}


@router.delete("/rules/{name}")
def delete_rule(name: str, uid: str = Depends(get_current_user)) -> dict:
    """删用户的覆盖规则（删掉后系统默认规则重新生效）。系统行删不掉。"""
    if not store.delete_rule(uid, name):
        raise HTTPException(status_code=404, detail="没有可删除的用户规则（系统默认规则不可删除）")
    return {"ok": True}


# ==================== 执行记录与日志 ====================

@router.get("/runs")
def list_runs(uid: str = Depends(get_current_user), limit: int = 20) -> dict:
    return {"items": store.list_runs(uid, min(limit, 100))}


@router.get("/runs/{run_id}")
def get_run(run_id: str, uid: str = Depends(get_current_user)) -> dict:
    row = store.get_run(uid, run_id)
    if not row:
        raise HTTPException(status_code=404, detail="执行记录不存在")
    return row


@router.get("/runs/{run_id}/logs")
def run_logs(run_id: str, uid: str = Depends(get_current_user), limit: int = 500) -> dict:
    """一次执行的全链路事件（要求 6：执行轨迹 / 决策依据 / 状态变更）。"""
    return {"items": store.list_logs(uid, run_id, min(limit, 2000))}


class SwitchIn(BaseModel):
    action: str = Field(..., description="to_agent / to_workflow / terminate")


@router.post("/runs/{run_id}/switch")
def manual_switch(run_id: str, req: SwitchIn, uid: str = Depends(get_current_user)) -> dict:
    """人工干预切换入口（要求 5）：优先级高于一切自动规则。

    指令写入后由正在运行的引擎在安全点（节点边界 / 事件边界）让出，
    编排器统一消费并应用——不是这里直接改状态。
    """
    row = store.get_run(uid, run_id)
    if not row:
        raise HTTPException(status_code=404, detail="执行记录不存在")
    if row["status"] != "running":
        raise HTTPException(status_code=409, detail="任务已结束，无法切换")
    try:
        request_manual_switch(run_id, req.action)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


class ResumeIn(BaseModel):
    """人工恢复（human 节点挂起后）。"""

    decision: str = Field(default="approve", description="approve / reject")
    note: str = Field(default="", max_length=500, description="人工填写的意见，进轨迹日志")


@router.post("/runs/{run_id}/resume")
def resume_run(run_id: str, req: ResumeIn, uid: str = Depends(get_current_user)) -> dict:
    """恢复挂起的人工节点（要求 5 的"人工介入"入口）。

    与 manual_switch 的区别必须说清：
      switch  = 改道，任务**还在跑**（等引擎在安全点消费指令）
      resume  = 唤醒，任务**已经停下**（status=awaiting_human），需要重新起跑

    所以这里不直接执行引擎，而是：
      1. 校验 belongs-to-you 且状态确为 awaiting_human（否则 409，避免重复恢复）
      2. 把 human_resumed 标记写回工作流状态并落库（**恢复即快照**，与挂起对称）
      3. 把"重新起跑所需的一切"返回给调用方（前端据此开一条新 SSE 续跑）

    为什么不在这里直接跑引擎：原 SSE 连接在挂起时已经结束，
    服务端没有可推送的通道；让客户端重新发起是唯一能立刻见效的做法。
    后续若要做"后台续跑 + 轮询取结果"，改动只在这一层。
    """
    row = store.get_run(uid, run_id)
    if not row:
        raise HTTPException(status_code=404, detail="执行记录不存在")
    if row["status"] != "awaiting_human":
        raise HTTPException(
            status_code=409,
            detail=f"该执行当前状态为 {row['status']}，只有 awaiting_human 才能恢复",
        )

    ctx_data = row.get("context") or {}
    if isinstance(ctx_data, str):
        try:
            import json as _json

            ctx_data = _json.loads(ctx_data)
        except ValueError:
            raise HTTPException(status_code=500, detail="执行快照损坏，无法恢复")

    wf_state = ctx_data.get("workflow_state") or {}
    suspended_at = wf_state.get("suspended_at") or wf_state.get("next_node")
    if not suspended_at:
        raise HTTPException(status_code=409, detail="快照里没有挂起点，无法定位续跑位置")

    if req.decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="decision 需为 approve 或 reject")

    # 恢复即快照：标记写回并落库，避免重复恢复 / 进程重启后丢失
    ctx = TaskContext.restore(ctx_data)
    if req.decision == "approve":
        ctx.workflow_state["human_resumed"] = suspended_at
        ctx.status = "running"
    else:
        # 拒绝：不续跑，直接落终态（后续可扩展为走 on_fail 分支）
        ctx.status = "stopped"
        ctx.error = f"人工拒绝：{req.note or '未填写理由'}"
        ctx.workflow_state.pop("suspended_at", None)
    ctx.mode_trace.append(
        ModeSwitch(
            from_mode=ctx.current_mode, to_mode=ctx.current_mode,
            rule="human_" + ("resume" if req.decision == "approve" else "reject"),
            reason=req.note or ("人工确认继续" if req.decision == "approve" else "人工拒绝"),
        )
    )
    store.save_run(ctx)

    return {
        "ok": True,
        "run_id": run_id,
        "decision": req.decision,
        "status": ctx.status,
        "resume_from": suspended_at if req.decision == "approve" else None,
        # 前端据此开一条新 SSE 续跑（同一 mode=hybrid + 同一 workflow_id）
        "context": ctx.snapshot(),
        "workflow_id": ctx.workflow_id,
    }
