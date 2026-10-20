"""指标导出端点（审计 A5）。

  GET /api/metrics        Prometheus 文本格式（贴在 probe-health.sh 里被采集）
  GET /api/metrics/json   结构化快照（人看 / 排障脚本用）

**权限为什么不靠回环地址**
最初的想法是"只允许 127.0.0.1 访问"，在这个架构下**不成立**：
前端走 Next.js 的 /api/* rewrite 代理，后端看到的 `request.client.host`
永远是 127.0.0.1。用它做判据等于对公网全开——这正好是"看着安全、实际敞开"
的典型形态，也是审计报告里点名的那一类问题。

所以判据换成两条：
  1. 登录用户是管理员（config.ADMIN_USERNAMES）；
  2. 或者请求带了正确的 `X-Metrics-Token`（值来自 .env 的 METRICS_TOKEN）。
第二条是给 Prometheus 用的——采集器不会登录，但可以带一个静态头。
METRICS_TOKEN 没配就等于这条路不存在，不会退化成"空 token 也放行"。
"""

from __future__ import annotations

import hmac
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from core import metrics

router = APIRouter()

# 未配置时为空串 → token 这条路整体关闭（不是"空串也能过"）
def _token() -> str:
    """每次现读（与 agent/tools.py 里 AMAP Key 同一惯例）：
    改 .env 后不必重启进程就生效，运维上少一步容易忘的动作。"""
    return (os.getenv("METRICS_TOKEN", "") or "").strip()


# Prometheus 的标准 content type；带 charset 是为了 curl 看中文 HELP 不乱码
_TEXT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _uid_of(request: Request) -> str:
    """尽力取当前用户，取不到返回空串。

    这里刻意**不用** `Depends(get_current_user)`：那个依赖在未登录时直接 401，
    而 Prometheus 采集器既没有 Cookie 也不会登录——它只有 X-Metrics-Token。
    如果先过鉴权依赖，token 这条路永远走不到。
    所以认证整体下移到 `_authorized()`：token 或管理员，二者之一即可。
    """
    from core import auth

    tok = auth.extract_token(request)
    claims = auth.decode_claims(tok) if tok else None
    if not claims:
        return ""
    return str(claims.get("uid") or claims.get("sub") or "")


def _authorized(request: Request) -> None:
    token = _token()
    if token:
        got = (request.headers.get("x-metrics-token") or "").strip()
        # 定长比较：token 比对不该留下计时侧信道（与口令校验同一套要求）
        if got and hmac.compare_digest(got, token):
            return
    uid = _uid_of(request)
    if uid:
        from core import quota

        if quota._is_admin(uid):
            return
    raise HTTPException(status_code=403, detail="指标仅管理员可见")


@router.get("", response_class=PlainTextResponse)
async def metrics_text(request: Request) -> PlainTextResponse:
    """Prometheus 文本格式。多 worker 下每进程各一份，抓取应覆盖每个实例。"""
    _authorized(request)
    return PlainTextResponse(metrics.render_prometheus(), media_type=_TEXT_TYPE)


@router.get("/json")
async def metrics_json(request: Request) -> dict:
    """结构化快照：排障时比 Prometheus 文本好读。"""
    _authorized(request)
    return metrics.snapshot()
