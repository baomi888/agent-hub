# -*- coding: utf-8 -*-
from __future__ import annotations
"""模板广场路由。

路由清单：
  GET    /api/templates/          列出全部场景模板
  POST   /api/templates/{tid}/apply  套用模板：自动建知识库 + 建会话并绑定

「套用模板」是在现有能力上做了一层薄封装：
  - 复用 kb.pipelines.create_kb 建一个带建议名的知识库（只登记，不建 Chroma 集合）
  - 复用 core.session.create_conversation 建一个已绑定该库的会话
  - 返回新建会话 id，前端据此跳转并预填首个问题
不引入新表、不改既有数据模型，所以零迁移成本。
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from core import session as store
from core.auth import get_current_user
from core.templates_catalog import get_catalog, get_template
from kb import pipelines

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/")
def list_templates() -> dict:
    """返回模板广场的全部场景模板（纯静态数据，已登录即可浏览）。"""
    return {"templates": get_catalog()}


@router.post("/{tid}/apply")
async def apply_template(tid: str, uid: str = Depends(get_current_user)) -> dict:
    """套用一个场景模板。

    流程：校验模板存在 →（可选）建建议知识库 → 建已绑定的会话 → 返回跳转信息。
    两个写操作都是同步阻塞（SQLite / JSON 注册表），按本项目约定丢进线程跑。
    """
    t = get_template(tid)
    if not t:
        raise HTTPException(status_code=404, detail=f"模板不存在：{tid}")

    kb_id: str | None = None
    kb_name = (t.get("suggested_kb_name") or "").strip()
    if kb_name:
        # 同名库会由 create_kb 内部用 owner+name 派生出同一 kb_id，天然幂等
        kb_id, _ = await asyncio.to_thread(pipelines.create_kb, uid, kb_name)

    title = t.get("session_title") or t["name"]
    conv = await asyncio.to_thread(store.create_conversation, uid, title, kb_id)
    return {
        "session_id": conv["id"],
        "kb_id": kb_id,
        "title": title,
        "starter_prompt": t.get("starter_prompt") or None,
        "recommended_mode": t.get("recommended_mode", "auto"),
    }
