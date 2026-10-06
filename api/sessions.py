# -*- coding: utf-8 -*-
"""会话管理路由。

路由清单：
  POST   /api/sessions/          新建会话
  GET    /api/sessions/          列出所有会话
  GET    /api/sessions/{sid}/    查看会话详情 + 消息历史
  PATCH  /api/sessions/{sid}/    重命名 / 绑定知识库
  DELETE /api/sessions/{sid}/    删除会话
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core import session as store
from core.auth import get_current_user

# 默认要登录：会话列表是最直接的隐私面，绝不能让访客互相看到
router = APIRouter(dependencies=[Depends(get_current_user)])


class SessionCreate(BaseModel):
    title: str = Field(default="新对话", description="会话标题")
    kb_id: str | None = Field(default=None, description="绑定的知识库 id（可选）")


class SessionUpdate(BaseModel):
    title: str | None = None
    kb_id: str | None = None  # None = 解绑


class MessageDeleteResponse(BaseModel):
    ok: bool
    deleted: int


# ==================== 路由 ====================

@router.post("/")
def create(body: SessionCreate, uid: str = Depends(get_current_user)):
    conv = store.create_conversation(uid, title=body.title, kb_id=body.kb_id)
    return conv


@router.get("/")
def list_all(uid: str = Depends(get_current_user)):
    return {"sessions": store.list_conversations(uid)}


@router.get("/{sid}/")
def get_detail(sid: str, uid: str = Depends(get_current_user)):
    conv = store.get_conversation(uid, sid)
    if not conv:
        raise HTTPException(status_code=404, detail=f"会话不存在：{sid}")
    conv["messages"] = store.get_messages(uid, sid)
    conv["message_count"] = store.count_messages(uid, sid)
    return conv


@router.patch("/{sid}/")
def update(sid: str, body: SessionUpdate, uid: str = Depends(get_current_user)):
    conv = store.get_conversation(uid, sid)
    if not conv:
        raise HTTPException(status_code=404, detail=f"会话不存在：{sid}")

    if body.title is not None:
        store.rename_conversation(uid, sid, body.title)
    if body.kb_id is not None or "kb_id" in body.model_fields_set:
        # 允许显式传 None 来解绑
        store.bind_kb(uid, sid, body.kb_id)

    return store.get_conversation(uid, sid)


@router.delete("/{sid}/")
def delete(sid: str, uid: str = Depends(get_current_user)):
    if not store.delete_conversation(uid, sid):
        raise HTTPException(status_code=404, detail=f"会话不存在：{sid}")
    return {"ok": True}


@router.delete("/{sid}/messages/{msg_id}/", response_model=MessageDeleteResponse)
def delete_message_turn(sid: str, msg_id: int, uid: str = Depends(get_current_user)):
    """删除某条消息及其所在的完整一轮对话（user + 后续连续 assistant）。"""
    conv = store.get_conversation(uid, sid)
    if not conv:
        raise HTTPException(status_code=404, detail=f"会话不存在：{sid}")
    if not store.get_message(uid, sid, msg_id):
        raise HTTPException(status_code=404, detail=f"消息不存在：{msg_id}")

    ids = store.find_turn_ids(uid, sid, msg_id)
    store.delete_messages(uid, sid, ids)
    return {"ok": True, "deleted": len(ids)}
