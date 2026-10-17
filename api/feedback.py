from __future__ import annotations

"""消息反馈路由（赞 / 踩）。

路由清单：
  POST /api/feedback/           记录或覆盖一条消息的反馈
  GET  /api/feedback/{sid}/     列出某会话的全部反馈

之前前端的赞踩只弹了个 toast，点完刷新就没了——这里给它一个真正的落点，
数据攒下来日后可以做回答质量复盘（哪些检索条数/模式容易被踩）。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core import quota
from core import session as store
from core.auth import get_current_user

# 默认要登录：反馈挂在别人的会话 id 上，没有归属校验就等于能往别人会话里写东西
router = APIRouter(dependencies=[Depends(get_current_user)])

# 反馈的每分钟上限。刻意比全局 RATE_PER_MINUTE 严：
# 正常用户点一次赞踩就完事，脚本才会一分钟打几十次 —— 而每次都是一条写库。
# 0 = 不限（沿用 config.RATE_PER_MINUTE）。
_FEEDBACK_RATE = 10


class FeedbackRequest(BaseModel):
    sid: str = Field(..., description="会话 id")
    message_id: int | None = Field(default=None, description="messages 表自增 id")
    rating: str = Field(..., description="up 赞 / down 踩")
    comment: str = Field(default="", description="可选的文字补充")


class FeedbackResponse(BaseModel):
    ok: bool
    rating: str


@router.post("/", response_model=FeedbackResponse)
def add_feedback(req: FeedbackRequest, uid: str = Depends(get_current_user)) -> dict:
    """记录反馈。同一条消息重复提交会覆盖旧值（点赞再点踩算改，不算追加两条）。"""
    # 先限流再校验归属：限流必须在最前面，否则脚本可以靠刷 404 来无限试探会话 id 是否存在。
    # RateLimited 已由 main.py 注册成全局 429，这里不要自己 try 成 500。
    quota.hit(uid, _FEEDBACK_RATE)
    # 先验归属，再验其它：不存在和不属于你都走同一个 404，不给枚举空间
    if not store.get_conversation(uid, req.sid):
        raise HTTPException(status_code=404, detail=f"会话不存在：{req.sid}")
    if req.rating not in ("up", "down"):
        raise HTTPException(status_code=400, detail="rating 只能是 up 或 down")
    if req.message_id is not None and not store.get_message(uid, req.sid, req.message_id):
        raise HTTPException(status_code=404, detail=f"消息不存在：{req.message_id}")

    ok = store.add_feedback(uid, req.sid, req.message_id, req.rating, req.comment)
    return {"ok": ok, "rating": req.rating}


@router.get("/{sid}/")
def list_feedback(sid: str, uid: str = Depends(get_current_user)) -> dict:
    """列出某会话的全部反馈。"""
    if not store.get_conversation(uid, sid):
        raise HTTPException(status_code=404, detail=f"会话不存在：{sid}")
    return {"sid": sid, "items": store.list_feedbacks(uid, sid)}
