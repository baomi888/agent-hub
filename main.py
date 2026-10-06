# -*- coding: utf-8 -*-
"""集合式 Agent 后端入口。

启动前必须保证：
  1. .env 已配置（DEEPSEEK_API_KEY / EMBEDDING_API_KEY / EMBEDDING_BASE_URL）
  2. 依赖已安装（requirements.txt）

启动方式：
  uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.auth import router as auth_router
from api.chat import router as chat_router
from api.feedback import router as feedback_router
from api.geo import router as geo_router
from api.kb import router as kb_router
from api.sessions import router as sessions_router
from core import config, quota
from kb import pipelines


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动事件：预热 + 配置检查。"""
    pipelines.warmup()
    missing = config.validate()
    if missing:
        print(f"[启动警告] 配置缺失：{'；'.join(missing)}")
    else:
        print("[启动 OK] 配置完整，RAG 链路就绪")
    yield


app = FastAPI(
    # 关闭自动斜杠重定向：否则 /api/sessions 会被 307 到 /api/sessions/，
    # 而这条 Location 指向 localhost:8000，浏览器经 Next 代理必然拿不到 200
    # （详见下方中间件）
    redirect_slashes=False,
    title="集合式 Agent",
    description="LangChain v1 + LangGraph + Chroma + DeepSeek 的集合式 Agent 产品",
    version="0.3.0 (W3)",
    lifespan=lifespan,
)

# ---------- CORS ----------
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
    ],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- 路由挂载 ----------
# 认证路由放最前面：它是唯一不带全局鉴权的（登录页总得能打开）。
# 其余四个业务 router 都在各自文件里挂了 dependencies=[Depends(get_current_user)]，
# 做成"默认要登录"——新增端点时忘了写鉴权，默认也是安全的那一侧。
app.include_router(auth_router, prefix="/api/auth", tags=["认证"])
app.include_router(kb_router, prefix="/api/kb", tags=["知识库"])
app.include_router(sessions_router, prefix="/api/sessions", tags=["会话"])
app.include_router(chat_router, prefix="/api/chat", tags=["问答"])
app.include_router(feedback_router, prefix="/api/feedback", tags=["反馈"])
# 公网 http 下浏览器 geolocation 不可用，由服务端按来源 IP 兜底定位（api/geo.py 有说明）
app.include_router(geo_router, prefix="/api/geo", tags=["定位"])


# ---------- 修复：前后端尾部斜杠不一致导致的重定向黑洞 ----------
# 症状：页面能打开，但「新建会话 / 发消息 / 所有 /api/* 调用」全部失败。
# 成因：前端走 Next.js rewrite 把 /api/* 代理到后端。前端调的是 /api/sessions/，
#       而 Next.js 默认「去尾斜杠」会先 308 到 /api/sessions；FastAPI 又默认
#       「补尾斜杠」，把 /api/sessions 307 回 /api/sessions/ —— 致命的是这条
#       重定向的 Location 指向 http://localhost:8000/...（Next 代理把 Host 改成了
#       localhost:8000）。浏览器照做就是去连它自己那台机器的 8000 端口，必然失败，
#       fetch 直接挂掉 → 前端报「新建会话失败」。
# 本项目前端发起的路径与后端路由的斜杠约定其实完全一致（有斜杠的对有斜杠、无斜杠的
# 对无斜杠）。所以只要后端不再自作主张重定向（redirect_slashes=False），并在进入
# 路由前把路径归一到「已注册的那种」，无论 Next 是否先去尾斜杠，后端都能直接命中路由。
def _collect_api_paths(routes):
    """收集所有已注册路由的完整路径（含 /api 前缀）。

    注意：include_router 进来的路由是 _IncludedRouter，其 .path 为 None，
    真实 APIRoute 收在 effective_route_contexts() 里、path 是相对前缀的，
    必须用 route_path 才能拿到带前缀的完整路径。
    """
    acc: set[str] = set()

    def walk(rs):
        for r in rs:
            p = getattr(r, "route_path", None) or getattr(r, "path", None)
            if p:
                acc.add(p)
            erc = getattr(r, "effective_route_contexts", None)
            if callable(erc):
                try:
                    for ctx in erc():
                        cp = getattr(ctx, "route_path", None) or getattr(ctx, "path", None)
                        if cp:
                            acc.add(cp)
                except Exception:
                    pass
            sub = getattr(r, "routes", None)
            if sub is not None and sub is not rs:
                walk(sub)

    walk(routes)
    return acc


def _exact_matches(template: str, path: str) -> bool:
    """路径是否精确命中模板（尾斜杠严格、{param} 视为通配）。"""
    ts = template.split("/")
    ps = path.split("/")
    if len(ts) != len(ps):
        return False
    for a, b in zip(ts, ps):
        if a == b or (a.startswith("{") and a.endswith("}")):
            continue
        return False
    return True


@app.middleware("http")
async def normalize_api_slash(request, call_next):
    path = request.scope.get("path", "")
    if path.startswith("/api/") and path != "/api/":
        has_slash = path.endswith("/")
        alt = path[:-1] if has_slash else path + "/"
        # 把已注册路径集合缓存到 app.state，避免每次请求重建
        cache = getattr(request.app.state, "api_paths", None)
        if cache is None:
            cache = _collect_api_paths(request.app.routes)
            request.app.state.api_paths = cache

        def matches_any(p: str) -> bool:
            return any(_exact_matches(t, p) for t in cache)

        # 当前形式命中不了、但翻一个尾斜杠就能命中时，归一到能命中的那一种
        if not matches_any(path) and matches_any(alt):
            request.scope["path"] = alt
            if request.scope.get("raw_path") is not None:
                request.scope["raw_path"] = alt.encode()
    return await call_next(request)


# ---------- 配额 / 限流 → 429 ----------
# 注册成全局异常处理器，而不是在每个端点里 try/except：
# 端点自己 catch 容易顺手把它转成 500，前端就分不清"系统出错"和"你今天用超了"。
@app.exception_handler(quota.RateLimited)
async def _on_rate_limited(request: Request, exc: quota.RateLimited):
    return JSONResponse(
        status_code=429,
        content={"detail": str(exc), "retry_after": round(exc.retry_after, 1)},
        headers={"Retry-After": str(int(exc.retry_after) + 1)},
    )


@app.exception_handler(quota.QuotaExceeded)
async def _on_quota_exceeded(request: Request, exc: quota.QuotaExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": str(exc), "kind": exc.kind, "limit": exc.limit},
    )


@app.get("/api/config/defaults")
def get_defaults():
    """返回前端初始表单所需的默认参数。"""
    return {
        "chunk_size": config.CHUNK_SIZE_DEFAULT,
        "chunk_overlap": config.CHUNK_OVERLAP_DEFAULT,
        "top_k": config.TOP_K_DEFAULT,
        "supported_extensions": [".txt", ".md", ".pdf"],
    }


@app.get("/health")
def health():
    """健康检查端点。"""
    return {"status": "ok", "version": "0.3.0 (W3)"}


if __name__ == "__main__":
    import uvicorn

    # 只监听回环：前端本来就是靠 Next 的 /api/* rewrite 访问它（next.config.ts
    # 指向 http://localhost:8000），后端不需要对公网开放。
    # 现在虽然已经加了登录鉴权，仍然保持回环——少一层暴露面就少一层风险，
    # 而且 8000 端口直连绕过了 Next 的 Cookie 域设置，容易踩登录态丢失的坑。
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
