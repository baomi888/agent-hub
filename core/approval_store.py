"""L4：高风险工具的真实人工确认（一次性 token）。

旧实现的问题（`core/tool_guard.py` 2026-10 版本）
----------------------------------------
`needs_approval` 工具第一次只回一个 `confirm:<hash>` 串，模型带着**同一个参数**
再调一次、串一命中就放行。等于模型自己走完「申请 → 回填」两步即可执行，
全程没有真实用户参与——这是纸糊的审批。

新语义
------
1. 工具第一次被调用：只登记一条 pending request，**不执行**；
2. 前端把这条 pending 渲染成确认卡片，用户点击后才调 `POST /api/chat/approval`；
3. 服务端在**点击这个动作发生之后**签发一次性 token，绑定
   `(user, tool_name, args_hash, turn_id)`，带过期时间；
4. 前端带着 token + 同一个 turn_id 重发本轮提问，工具执行前由守卫消费 token；
5. token 用后即焚、不能重放；参数变了（args_hash 变了）token 无效；
6. 模型拿不到 token（token 只走服务端注入的 `configurable`，不在工具 schema 里），
   所以「同参数再调一次」仍然被拒——这是 L4 的核心断言。

存储位置：进程内内存（与 `tool_guard` 的 contextvar 同层）。
为什么不做持久化：pending 的生命周期是「一次用户点击」，跨进程/重启后
本来就该作废（用户重新发起即可）。持久化反而会留下可被重放的 token。
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

from core import config

_LOCK = threading.Lock()


def args_hash(args: dict[str, Any]) -> str:
    """工具参数的稳定指纹。参数变了 -> hash 变 -> 已签发的 token 自动失效。"""
    payload = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


@dataclass
class PendingApproval:
    request_id: str
    user: str
    tool_name: str
    args_hash: str
    args_preview: str
    turn_id: str
    created_at: float
    expires_at: float
    # 已经签发过但还没被消费的 token（同一个 pending 只允许一个活跃 token）
    token: str | None = None

    def as_event(self) -> dict:
        return {
            "request_id": self.request_id,
            "tool_name": self.tool_name,
            "input": self.args_preview,
            "turn_id": self.turn_id,
            "expires_in": max(0, int(self.expires_at - time.time())),
        }


_PENDING: dict[str, PendingApproval] = {}
_TOKENS: dict[str, PendingApproval] = {}
# (user, tool_name, args_hash, turn_id) -> request_id：同一个待确认请求不重复登记
_DEDUPE: dict[tuple[str, str, str, str], str] = {}


def _ttl() -> int:
    return max(1, int(getattr(config, "TOOL_APPROVAL_TTL_SECONDS", 120)))


def _prune(now: float | None = None) -> None:
    """惰性清理过期条目。调用方必须已持有 _LOCK。"""
    now = time.time() if now is None else now
    for rid, rec in list(_PENDING.items()):
        if rec.expires_at <= now:
            _PENDING.pop(rid, None)
            _DEDUPE.pop((rec.user, rec.tool_name, rec.args_hash, rec.turn_id), None)
            if rec.token:
                _TOKENS.pop(rec.token, None)
    for tok, rec in list(_TOKENS.items()):
        if rec.expires_at <= now:
            _TOKENS.pop(tok, None)


def create_pending(
    user: str, tool_name: str, args: dict[str, Any], turn_id: str
) -> PendingApproval:
    """登记一条待确认请求。同 (user, tool, args_hash, turn) 复用同一条。"""
    ah = args_hash(args)
    preview = json.dumps(args, ensure_ascii=False, default=str)[:400]
    key = (user, tool_name, ah, turn_id)
    with _LOCK:
        _prune()
        rid = _DEDUPE.get(key)
        if rid and rid in _PENDING:
            return _PENDING[rid]
        rid = uuid.uuid4().hex
        now = time.time()
        rec = PendingApproval(
            request_id=rid,
            user=user,
            tool_name=tool_name,
            args_hash=ah,
            args_preview=preview,
            turn_id=turn_id,
            created_at=now,
            expires_at=now + _ttl(),
        )
        _PENDING[rid] = rec
        _DEDUPE[key] = rid
        return rec


def get_pending(request_id: str, user: str | None = None) -> PendingApproval | None:
    with _LOCK:
        _prune()
        rec = _PENDING.get(request_id)
        if rec is None:
            return None
        if user is not None and rec.user != user:
            return None
        return rec


def pending_for_turn(user: str, turn_id: str) -> list[PendingApproval]:
    """本轮产生的待确认请求（供 SSE 在流结束时推给前端）。"""
    with _LOCK:
        _prune()
        return [
            r
            for r in _PENDING.values()
            if r.user == user and r.turn_id == turn_id
        ]


def issue_token(request_id: str, user: str) -> dict | None:
    """**前端点击后**由服务端调用：签发一次性 token。

    同一个 pending 若已有未过期的活跃 token，直接回同一个（避免连点两下
    产生两个都能用的 token）。pending 不存在 / 不属于该用户 / 已过期 -> None。
    """
    with _LOCK:
        _prune()
        rec = _PENDING.get(request_id)
        if rec is None or rec.user != user:
            return None
        if rec.token and rec.token in _TOKENS:
            existing = _TOKENS[rec.token]
            return {
                "request_id": rec.request_id,
                "tool_name": rec.tool_name,
                "args_hash": rec.args_hash,
                "turn_id": rec.turn_id,
                "approval_token": rec.token,
                "expires_in": max(0, int(existing.expires_at - time.time())),
            }
        token = "apr:" + secrets.token_urlsafe(32)
        rec.token = token
        _TOKENS[token] = rec
        return {
            "request_id": rec.request_id,
            "tool_name": rec.tool_name,
            "args_hash": rec.args_hash,
            "turn_id": rec.turn_id,
            "approval_token": token,
            "expires_in": max(0, int(rec.expires_at - time.time())),
        }


def consume(token: str, user: str, tool_name: str, ah: str, turn_id: str) -> bool:
    """消费一次 token。绑定全部匹配且未过期才返回 True；成功后立即删除。

    删除即「用后即焚」：同一条 token 第二次调用必然 False（重放被拒）。
    """
    if not token:
        return False
    with _LOCK:
        _prune()
        rec = _TOKENS.get(token)
        if rec is None:
            return False
        if rec.user != user or rec.tool_name != tool_name:
            return False
        if rec.args_hash != ah or rec.turn_id != turn_id:
            return False
        # 用后即焚：token 与 pending 一起删除，任何一方残留都可能被重放
        _TOKENS.pop(token, None)
        _PENDING.pop(rec.request_id, None)
        _DEDUPE.pop((rec.user, rec.tool_name, rec.args_hash, rec.turn_id), None)
        return True


def has_active_token(request_id: str) -> bool:
    with _LOCK:
        _prune()
        rec = _PENDING.get(request_id)
        return bool(rec and rec.token and rec.token in _TOKENS)


def reset() -> None:
    """清空全部状态（仅供测试）。"""
    with _LOCK:
        _PENDING.clear()
        _TOKENS.clear()
        _DEDUPE.clear()
