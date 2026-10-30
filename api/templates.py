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

from core import quota
from core import session as store
from core.auth import get_current_user
from core.templates_catalog import get_catalog, get_template, recommend_for_tags
from kb import pipelines

from .chat import VALID_MODES

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/")
async def list_templates(uid: str = Depends(get_current_user)) -> dict:
    """返回模板广场的全部场景模板，并附带「按你的兴趣标签推荐」。

    推荐是**附加值**，不是必需项：拿不到标签（新用户 / 读库失败）时
    返回空列表，前端就不渲染「为你推荐」那一栏 —— 绝不因为推荐失败
    让整个模板广场 500。这才是把个性化挂在这里的理由：可降级。
    """
    try:
        rows = await asyncio.to_thread(store.get_user_tags, uid, 30)
        weights = {r["tag"]: float(r["weight"] or 0) for r in rows}
        recommended = recommend_for_tags(weights, limit=3)
    except Exception:
        recommended = []
    return {"templates": get_catalog(), "recommended": recommended}


@router.post("/{tid}/apply")
async def apply_template(tid: str, uid: str = Depends(get_current_user)) -> dict:
    """套用一个场景模板。

    流程：校验模板存在 →（可选）建建议知识库 → 建已绑定的会话 → 返回跳转信息。
    两个写操作都是同步阻塞（SQLite / JSON 注册表），按本项目约定丢进线程跑。
    套用是「一键建库+建会话」的批量资源操作，先过每分钟限流，避免连点刷爆。
    """
    # 每分钟限流（非管理员）。击中上限会抛 RateLimited，由 main.py 统一转 429。
    await asyncio.to_thread(quota.hit, uid)

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

    # recommended_mode：**已废弃**，仅为旧前端缓存版本保留。
    # 2026-10-30 起前端不再有"模式"，只有两个能力开关，所以下面同时给出
    # use_kb / use_web 两个派生字段，新前端请读这两个。
    #
    # 为什么不直接删字段：模板目录是纯数据（core/templates_catalog.py 里 8 处），
    # 删字段要同时改数据与消费方；这轮前端不动，先让两套字段并存一个版本。
    raw_mode = t.get("recommended_mode", "auto")
    mode = raw_mode if raw_mode in VALID_MODES + ("hybrid",) else "auto"

    # 旧值 → 开关的映射口径与 api/chat.py::_resolve_switches 保持一致：
    #   rag    = 只检索；llm = 都不用；agent/hybrid = 检索 + 联网；auto = 看有没有库
    use_kb = mode in ("rag", "agent", "hybrid") or (mode == "auto" and bool(kb_id))
    use_web = mode in ("agent", "hybrid", "auto")

    return {
        "session_id": conv["id"],
        "kb_id": kb_id,
        "title": title,
        "starter_prompt": t.get("starter_prompt") or None,
        "recommended_mode": mode,
        "use_kb": use_kb,
        "use_web": use_web,
    }
