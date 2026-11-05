"""建库建议的确认 / 忽略端点（板块 D 的"确认制"那一半）。

  POST /api/kb/proposals/{proposal_id}/confirm   确认 → 建库 + 入库 + 绑会话
  POST /api/kb/proposals/{proposal_id}/dismiss   忽略
  GET  /api/kb/proposals/{proposal_id}           查状态（按 id）
  GET  /api/kb/proposals?sid=xxx                 这个会话还没处理的建议（按会话）

为什么建议要落库、要能 GET：SSE 只有一帧，用户刷一下页面那张卡就永远回不来了，
而"我刚才好像看到它问我要不要建库"这种事用户不会重问一遍。所以
`core/kb_proposals.maybe_propose` 先落一条 pending，这里按 id 认领。

状态机（三态 + 过期）：
    pending ──confirm──▶ confirmed
        └────dismiss──▶ dismissed
        └────到期──────▶ expired（410）

重复 confirm / dismiss 返回 409 而不是 200：并发点两下时，第二下必须知道
"这事儿已经办完了"，否则前端会把同一次建库显示成建了两次。
"""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core import sediment_store
from core import session as store
from core.auth import get_current_user
from core.kb_proposals import card_of
from kb import dialogue_import, pipelines
from service import sediment

router = APIRouter(dependencies=[Depends(get_current_user)])


class ConfirmIn(BaseModel):
    """确认建库。`name` 覆盖建议的库名，`bind` 决定要不要把这个库绑到当前会话。"""

    name: str | None = None
    bind: bool = True


def _alive(owner: str, proposal_id: str) -> dict:
    """取一条**可操作**的建议：不存在 404 / 非 pending 409 / 过期 410。"""
    prop = sediment_store.get_proposal(owner, proposal_id)
    if not prop:
        # 别人的 proposal 也走这里：统一 404，不泄露存在性
        raise HTTPException(status_code=404, detail="建议不存在或已失效")
    if prop.get("status") != "pending":
        raise HTTPException(status_code=409, detail="这条建议已经处理过了")
    if int(prop.get("expires_at") or 0) < int(time.time()):
        # 顺手落状态：不落的话它会一直是 pending，下次还会被判成"已处理"
        sediment_store.update_proposal(owner, proposal_id, "expired")
        raise HTTPException(status_code=410, detail="这条建议已过期，请重新发起")
    return prop


def _pick_from_web(prop: dict) -> list[dict]:
    """联网场景的入库内容：外部来源 + 本轮回答的整理稿。

    ★ 来源的正文只有 title/snippet：这里不重新抓网页（确认是一次点击，
      不该在点击里做 5 个网络请求）。snippet 已经是检索返回的摘要，
      足够撑起一条可检索的片段，link 一并入库便于溯源。
    """
    payload = prop.get("payload") or {}
    now = int(time.time())
    picked: list[dict] = []
    for s in payload.get("web_sources") or []:
        title = (s.get("title") or "").strip()
        link = (s.get("link") or "").strip()
        snippet = (s.get("snippet") or "").strip()
        text = "\n".join(x for x in (title, snippet, link) if x)
        if not text:
            continue
        picked.append(
            {
                "text": text,
                "refs": [],
                "kind": "外部资料",
                # role=web：检索命中时界面上能看出"这是联网抓来的"，
                # 而不是把它当成用户说过的话（引用卡按 role 分组）
                "role": "web",
                "ts": now,
                "salience": 0.6,
            }
        )
    mid = int(payload.get("answer_message_id") or 0)
    if mid:
        msg = store.get_message(prop["owner_id"], prop["sid"], mid)
        if msg and msg.get("role") == "assistant":
            # 助手的整理稿按 markdown 结构切块，和手工沉淀同一套判据
            picked.extend(dialogue_import.extract_blocks([msg]))
    return picked


def _pick_from_session(owner: str, sid: str) -> list[dict]:
    """本会话的可入库候选（和手工沉淀同一套抽取器：用户按句、助手按结构）。"""
    msgs = store.get_messages(owner, sid, limit_turns=None, include_system=False)
    if not msgs:
        return []
    return dialogue_import.extract_blocks(msgs)


def _resolve_kb(owner: str, want_name: str, topic: str) -> tuple[str, str]:
    """定库：用户改写名字就按名字建/复用，否则走「对话沉淀·<主题>」复用。"""
    if want_name:
        exist = [k for k in pipelines.list_all(owner) if (k.get("name") or "") == want_name]
        if exist:
            return str(exist[0]["kb_id"]), want_name
        return pipelines.create_kb(owner, want_name)
    return sediment.reuse_or_create_topic_kb(owner, topic)


@router.get("")
async def pending_for_session(sid: str, uid: str = Depends(get_current_user)) -> dict:
    """这个会话还有没有等我决定的建议（没有就 `{"proposal": null}`）。

    `GET /{proposal_id}` 要求前端先知道 id —— 但刷新后前端手上什么都没有，
    所以恢复卡片必须先有这一条"按会话问"的入口，否则那个按 id 查的端点
    永远没人调用，pending 行就成了只进不出的死数据。

    ★ 没有建议时回 **200 + null**，不是 404。这不是洁癖：这条请求在**每次**
      打开/切换会话时都会发一次，而浏览器会把任何 404 记成一条红色资源错误
      —— 正常状态（这个会话本来就没有建议）在控制台里长得像故障。
      集合存在、只是空的时候，回空集合才是准确的。
    """
    prop = await asyncio.to_thread(sediment_store.latest_pending, uid, sid)
    if not prop:
        return {"proposal": None}
    return {
        "proposal": {
            **card_of(prop),
            "status": prop.get("status") or "",
            "kb_id": prop.get("kb_id") or "",
        }
    }


@router.get("/{proposal_id}")
async def read_proposal(proposal_id: str, uid: str = Depends(get_current_user)) -> dict:
    """查建议状态（前端刷新后用它把卡片/结果恢复出来）。

    卡片形状走 `card_of`，与 SSE 那一帧同源 —— 恢复出来的卡片和刚生成时
    长的必须一模一样，条数、库名、理由都不能有第二种算法。
    """
    prop = await asyncio.to_thread(sediment_store.get_proposal, uid, proposal_id)
    if not prop:
        raise HTTPException(status_code=404, detail="建议不存在")
    return {
        **card_of(prop),
        "status": prop["status"],
        "kb_id": prop.get("kb_id") or "",
    }


@router.post("/{proposal_id}/confirm")
async def confirm(proposal_id: str, body: ConfirmIn, uid: str = Depends(get_current_user)) -> dict:
    """确认：建库（或复用同主题库）→ 入库 → 回写 staged → 绑会话。"""
    prop = await asyncio.to_thread(_alive, uid, proposal_id)
    sid = prop["sid"]

    # 1) 库：用户改名就按名字建/找，否则用「对话沉淀·<主题>」复用
    want_name = (body.name or "").strip()
    kb_id, kb_name = await asyncio.to_thread(
        _resolve_kb, uid, want_name, prop.get("topic") or "未命名对话"
    )

    # 2) 入库内容：联网场景用 payload 里那批来源，其余用本会话候选
    if (prop.get("source") or "") == "web_search":
        picked = await asyncio.to_thread(_pick_from_web, prop)
    else:
        picked = await asyncio.to_thread(_pick_from_session, uid, sid)

    if not picked:
        # 到这一步还没内容：库已经建了，但里面是空的。告诉用户实话，
        # 不假装成功（否则他会去 /knowledge 找一个空库，以为数据丢了）
        await asyncio.to_thread(sediment_store.update_proposal, uid, proposal_id, "confirmed", kb_id)
        return {
            "proposal_id": proposal_id,
            "status": "confirmed",
            "kb_id": kb_id,
            "name": kb_name,
            "imported": 0,
            "chunks": 0,
            "staged_segments": 0,
            "bound": False,
            "batch_id": "",
            "duplicate": False,
            "note": "库已建好，但这段对话里没有可入库的整理内容",
        }

    res = await asyncio.to_thread(
        sediment.write_picked, uid, sid, kb_id, picked, source="web_confirm"
    )

    bound = False
    if body.bind:
        bound = await asyncio.to_thread(store.bind_kb, uid, sid, kb_id)

    await asyncio.to_thread(sediment_store.update_proposal, uid, proposal_id, "confirmed", kb_id)
    return {
        "proposal_id": proposal_id,
        "status": "confirmed",
        "kb_id": kb_id,
        "name": kb_name,
        "imported": int(res.get("imported") or 0),
        "chunks": int(res.get("chunks") or 0),
        "staged_segments": int(res.get("staged_segments") or 0),
        "bound": bool(bound),
        "batch_id": res.get("batch_id") or "",
        # 幂等命中：内容之前存过，这次一条没写。前端要能把「已经存过」和
        # 「没抓到内容」分开说 —— 两者在卡上长得一样，但意思完全不同。
        "duplicate": bool(res.get("duplicate")),
    }


@router.post("/{proposal_id}/dismiss")
async def dismiss(proposal_id: str, uid: str = Depends(get_current_user)) -> dict:
    """忽略建议。不做任何入库动作。"""
    await asyncio.to_thread(_alive, uid, proposal_id)
    ok = await asyncio.to_thread(sediment_store.update_proposal, uid, proposal_id, "dismissed")
    if not ok:
        # CAS 失败 = 同一瞬间被 confirm 掉了
        raise HTTPException(status_code=409, detail="这条建议已经处理过了")
    return {"proposal_id": proposal_id, "status": "dismissed"}
