"""集合式 Agent 后端入口。

启动前必须保证：
  1. .env 已配置（DEEPSEEK_API_KEY / EMBEDDING_API_KEY / EMBEDDING_BASE_URL）
  2. 依赖已安装（requirements.txt）

启动方式：
  uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.admin import router as admin_router
from api.agent import router as agent_router
from api.auth import router as auth_router
from api.cet import router as cet_router
from api.chat import router as chat_router
from api.checkin import router as checkin_router
from api.delegate import router as delegate_router
from api.feedback import router as feedback_router
from api.geo import router as geo_router
from api.hybrid import router as hybrid_router
from api.kb import router as kb_router
from api.metrics import router as metrics_router
from api.path import router as path_router
from api.profile import router as profile_router
from api.prompt_evo import router as prompt_evo_router
from api.query import router as query_router
from api.report import router as report_router
from api.schedule import router as schedule_router
from api.sessions import router as sessions_router
from api.templates import router as templates_router
from api.usage import router as usage_router
from api.user_feedback import router as user_feedback_router
from core import account, apilog, config, llm_gate, quota
from kb import pipelines
from service import daily


async def _apilog_flush_loop() -> None:
    """每 3 秒把采集缓冲落一次库（文档 §5 承诺的"准实时 ≤ 3 秒"靠它兜底）。

    **为什么必须有这个循环**：只靠"请求结束时判断是否该 flush"是不够的 ——
    零星的最后几个请求会把数据留在缓冲里，直到**下一个请求**才被带走。
    真机验证时踩到了：4 个请求过后，只有 1 条落了库，剩下 1 条一直悬着。
    这类问题在单测里测不出来（TestClient 每次手动 flush），只有真起进程才会暴露。

    多 worker 时每个进程各跑一个，各清自己的缓冲，互不干扰。
    """
    while True:
        try:
            await asyncio.sleep(apilog.INTERVAL)
            await asyncio.to_thread(apilog.flush)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 —— 采集循环挂了也不能拖垮服务
            pass


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动事件：预热 + 配置检查 + 数据表迁移。"""
    pipelines.warmup()
    # 部署后免手动触发：在启动时把 daily_reports / delegations 两张表建好、补列（幂等）。
    # 否则 _ensure_daily_tables 是懒执行的，刚部署完没人访问过 delegation 时会查不到新列。
    # 兜 try/except：迁移失败（如库被锁）只告警不阻断启动——懒执行那条路仍会在首次访问时自愈。
    try:
        await asyncio.to_thread(daily._ensure_daily_tables)
    except Exception as e:  # noqa: BLE001
        print(f"[启动警告] 数据表迁移失败（首次访问时会自动重试）：{e}")
    missing = config.validate()
    if missing:
        print(f"[启动警告] 配置缺失：{'；'.join(missing)}")
    else:
        print("[启动 OK] 配置完整，RAG 链路就绪")

    # 管理员配置自检（2026-10-29 安全加固）：
    # ADMIN_USERNAMES 里的名字已被自助注册保留（见 api/auth.py），
    # 若账号还没建，这里把"下一步该做什么"直接打出来，避免部署后以为注册坏了。
    try:
        for _name in config.ADMIN_USERNAMES:
            if not account.get_user_by_username(_name):
                print(
                    f"[启动警告] 管理员用户名 {_name!r} 尚无账号；该名字已被自助注册保留。"
                    f"请用 `python tools/create_user.py {_name}` 创建，"
                    "或改用不可猜的 ADMIN_UIDS。"
                )
        if not config.ADMIN_UIDS and not config.ADMIN_USERNAMES:
            print(
                "[启动提示] 未配置管理员（ADMIN_UIDS / ADMIN_USERNAMES 均为空）："
                "数据看板、调度手动触发与指标导出不可用。\n"
                "            若你是原管理员、升级后突然变成普通用户，"
                "在 .env 追加 ADMIN_USERNAMES=<你的用户名> 或 ADMIN_UIDS=<你的uid>，"
                "重启后端即可恢复。\n"
                "            uid 查法：venv/bin/python tools/create_user.py <用户名> --show-uid"
            )
    except Exception as e:  # noqa: BLE001 —— 自检失败不阻断启动
        print(f"[启动警告] 管理员配置自检失败：{e}")
    # API 调用采集的定时落库：低流量下没有它，最后几条会一直卡在内存缓冲里
    flush_task = asyncio.create_task(_apilog_flush_loop())
    try:
        yield
    finally:
        flush_task.cancel()
        try:
            await flush_task
        except asyncio.CancelledError:
            pass
        # 退出前最后落一次：否则正常停机时缓冲里那几十条就丢了
        try:
            await asyncio.to_thread(apilog.flush)
        except Exception:  # noqa: BLE001
            pass


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
# Agent 编排的元数据端点（工具清单 / 延迟发现）：只读代码里的 TOOL_META，不执行工具
app.include_router(agent_router, prefix="/api/agent", tags=["Agent 编排"])
app.include_router(kb_router, prefix="/api/kb", tags=["知识库"])
app.include_router(sessions_router, prefix="/api/sessions", tags=["会话"])
app.include_router(chat_router, prefix="/api/chat", tags=["问答"])
app.include_router(feedback_router, prefix="/api/feedback", tags=["反馈"])
# 公网 http 下浏览器 geolocation 不可用，由服务端按来源 IP 兜底定位（api/geo.py 有说明）
app.include_router(geo_router, prefix="/api/geo", tags=["定位"])
app.include_router(templates_router, prefix="/api/templates", tags=["模板广场"])
# 用量/反馈看板：只读聚合，鉴权沿用默认（get_current_user）
app.include_router(usage_router, prefix="/api/usage", tags=["用量"])
# 调度可观测与手动触发：只读调度库 + 写「待执行」行，
# 真正的执行在独立 worker 进程（python -m worker.scheduler），web 不 import apscheduler
app.include_router(schedule_router, prefix="/api/schedule", tags=["调度"])
# 每日总结与每日委托：业务能力全在 service/daily.py，
# worker 定时触发走的是同一套 service 函数，手动与定时不产生第二条代码路径
app.include_router(report_router, prefix="/api/report", tags=["每日总结"])
app.include_router(checkin_router, prefix="/api/checkin", tags=["打卡"])
app.include_router(delegate_router, prefix="/api/delegate", tags=["每日委托"])
# 混合架构（工作流 × 自主 Agent）：工作流定义 / 切换规则 / 执行记录与人工切换
app.include_router(hybrid_router, prefix="/api/hybrid", tags=["混合执行"])
# Prompt 自进化（GEPA）：候选列表 / 指标对比 / promote / rollback / 手动触发。
# 读端点要登录、写端点要管理员；执行同样交给 worker（job_key=prompt.evolve）
app.include_router(prompt_evo_router, prefix="/api/prompt-evo", tags=["Prompt 自进化"])
# 指标导出（审计 A5）：Prometheus 文本 + 结构化快照。
# 权限是「管理员 或 带正确 X-Metrics-Token」——**不能靠回环地址判定**，
# 因为 Next 的 rewrite 让所有请求看起来都来自 127.0.0.1（api/metrics.py 有说明）。
app.include_router(metrics_router, prefix="/api/metrics", tags=["指标"])
# 用户反馈（DeepSeek 式分类按钮 + 一段话）。与 /api/feedback（消息赞踩）是两张表、
# 两个语义：这里提的是"产品哪里要改"，那条是"这个回答好不好"（后者喂给提示词进化）
app.include_router(user_feedback_router, prefix="/api/user-feedback", tags=["用户反馈"])
# 开发者数据看板：口径见 docs/数据看板_2026-10-27/口径与数据字典.md。
# 未登录 401（路由级依赖）、非管理员 403（函数内判定）—— 两层顺序不能反
app.include_router(admin_router, prefix="/api/admin", tags=["开发者看板"])
# 四六级备考工作区：计划 / 任务 / 词汇本 / 模考分数（申报书 B3 承诺的专项模块）
app.include_router(cet_router, prefix="/api/cet", tags=["四六级"])
# 关卡式学习路径（申报书 3.4 关卡式学习路径 + 2.8 输出层「学习路径进度」）
app.include_router(path_router, prefix="/api/path", tags=["学习路径"])
# 用户兴趣标签（画像）：30 条定稿词表 / 手选标签 / 按大类聚合的权重
app.include_router(profile_router, prefix="/api/profile", tags=["用户画像"])
# 检索词增强（roadmap B3）：预览"把口语词换成标准词"的结果，只读、不写库、不调模型
app.include_router(query_router, prefix="/api/query", tags=["检索词增强"])


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
def _collect_api_paths(routes: list) -> set[str]:
    """收集所有已注册路由的完整路径（含 /api 前缀）。

    注意：include_router 进来的路由是 _IncludedRouter，其 .path 为 None，
    真实 APIRoute 收在 effective_route_contexts() 里、path 是相对前缀的，
    必须用 route_path 才能拿到带前缀的完整路径。
    """
    acc: set[str] = set()

    def walk(rs: list) -> None:
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
    for a, b in zip(ts, ps, strict=False):
        if a == b or (a.startswith("{") and a.endswith("}")):
            continue
        return False
    return True


@app.middleware("http")
async def normalize_api_slash(request: Request, call_next: Callable[[Request], Any]) -> Response:
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


# ---------- API 调用采集（开发者看板的数据源）----------
# 口径与取舍见 docs/数据看板_2026-10-27/口径与数据字典.md §3 / §5，
# 实现说明见 core/apilog.py。
#
# 放在中间件链最外层（后注册 = 先进入）：能包住 normalize 的路径归一与后续全部处理；
# 且 `call_next` 之后 `scope["route"]` 已被 router 填好，可以直接取**路由模板**
# 作为聚合键 —— 不用从真实路径猜 id（猜不准会把真接口并成一条，比不归一化更糟）。


def _route_template(request: Request) -> str:
    """这次请求命中的路由模板（如 `/api/sessions/{sid}`）。

    **一个坑**：`include_router` 进来的路由，它的 `route.path` 是**相对前缀**的
    （`/api/auth/me/` 的 route.path 是 `/me/`，不含 `/api/auth`）。
    直接拿它当结果，会被"只记 /api/ 前缀"的规则整批过滤掉——
    表现为"中间件明明跑了，但一条日志都没有"。所以这里按**段**把前缀接回去：
    模板段数 ≤ 真实段数，且从尾部逐段匹配（`{x}` 视为通配）才认为对齐。

    拿不到模板或对不齐时（404、静态挂载），退回按段归一化：
    至少把 uuid/纯数字换成 `{id}`，否则 404 的噪声会以成千上万个
    不同路径的形式占满排行。
    """
    raw = str(request.scope.get("path") or "")
    tmpl = getattr(request.scope.get("route"), "path", None)
    if isinstance(tmpl, str) and tmpl:
        rs = [x for x in raw.split("/") if x]
        ts = [x for x in tmpl.split("/") if x]
        if ts and len(ts) <= len(rs):
            tail = rs[len(rs) - len(ts) :]
            if all(
                t == r or (t.startswith("{") and t.endswith("}"))
                for t, r in zip(ts, tail, strict=False)
            ):
                head = "/".join(rs[: len(rs) - len(ts)] + ts)
                return "/" + head + ("/" if raw.endswith("/") else "")
    return apilog.normalize_path(raw)


def _spawn_flush() -> None:
    """把批量落库丢进线程池，不 await。

    统计绝不能成为响应变慢的原因：这里只 create_task，本次请求立刻结束。
    没有运行中的事件循环时（同步测试）就地写 —— 否则测试断言看不到数据。
    """
    try:
        asyncio.create_task(asyncio.to_thread(apilog.flush))
    except RuntimeError:
        apilog.flush()


def _is_streaming(response: Response) -> bool:
    """这个响应是不是"边算边发"的流。

    **不要用 `isinstance(response, StreamingResponse)`**：`BaseHTTPMiddleware`
    交回来的实际是 Starlette 内部的 `_StreamingResponse`，它的 MRO 里根本没有
    `StreamingResponse`（只有 `Response`），isinstance 恒为 False，
    判断会静默失效 —— 表现为流式接口的耗时永远是 0，而且没有任何报错。
    只有 `_StreamingResponse` 带 `body_iterator`，普通 `Response` 不带，
    所以按属性判断既准确又不依赖内部类名。
    """
    return hasattr(response, "body_iterator")


@app.middleware("http")
async def collect_api_call(request: Request, call_next: Callable[[Request], Any]) -> Response:
    """采集一次调用。**流式与非流式的记录时机不同，这是本函数唯一的复杂点**。

    非流式：`call_next` 返回时响应已经做好，此刻记时长就是真实时长（文档 §2.5）。

    流式（SSE / 文件下载）：`call_next` 在**第一帧产生之前**就返回了 —— 这层
    中间件看到的是"响应头已就绪"，body 还没开始生成。此刻记时长会得到 0~30ms，
    而真实时长可能是几十秒。于是 `/api/chat/stream` 会在"最慢接口"榜上消失
    （实测客户端 15851ms，看板记 29ms，差 546 倍）。所以这里把 `body_iterator`
    包一层计时，等生成器跑完再记。

    记在 `finally` 而不是 else：客户端中途断开（浏览器关页、网络断）时生成器
    被 close，照样会走到 finally —— 那次调用确实发生过，不该漏统计。
    """
    t0 = time.perf_counter()
    status = 500  # 下游抛异常时的兜底：这次调用确实发生了，且没有正常响应

    def _emit(code: int) -> None:
        """统一出口：算时长 → 归一化路径 → 塞缓冲。任何异常都吞掉。"""
        try:
            dur_ms = int((time.perf_counter() - t0) * 1000)
            uid = str(getattr(request.state, "uid", "") or "") or None
            if apilog.record(request.method, _route_template(request), code, dur_ms, uid):
                _spawn_flush()
        except Exception:  # noqa: BLE001 —— 采集失败绝不能让请求失败
            pass

    try:
        response = await call_next(request)
        status = response.status_code
    except BaseException:
        # 业务异常要继续往上抛（不能吞），但"这次调用发生过"这个事实仍要记下来
        _emit(status)
        raise

    if _is_streaming(response):
        inner = response.body_iterator
        final_status = status

        async def _timed_body():
            try:
                async for chunk in inner:
                    yield chunk
            finally:
                _emit(final_status)

        response.body_iterator = _timed_body()
        return response

    _emit(status)
    return response


# ---------- 配额 / 限流 → 429 ----------
# 注册成全局异常处理器，而不是在每个端点里 try/except：
# 端点自己 catch 容易顺手把它转成 500，前端就分不清"系统出错"和"你今天用超了"。
@app.exception_handler(quota.RateLimited)
async def _on_rate_limited(request: Request, exc: quota.RateLimited) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": str(exc), "retry_after": round(exc.retry_after, 1)},
        headers={"Retry-After": str(int(exc.retry_after) + 1)},
    )


@app.exception_handler(quota.QuotaExceeded)
async def _on_quota_exceeded(request: Request, exc: quota.QuotaExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": str(exc), "kind": exc.kind, "limit": exc.limit},
    )


@app.get("/api/config/defaults")
async def get_defaults() -> dict:
    """返回前端初始表单所需的默认参数。

    刻意用 async def：这个接口只读常量，没有任何阻塞调用。写成同步 def 的话
    FastAPI 会把它丢进 anyio 线程池（默认上限 40 个线程）执行，200 人同时
    在线时请求先在线程池外排队，p95 直接从毫秒级涨到秒级——2026-10-20 的
    压测就是这个现象（见 docs/并发承载与JWT改造报告_2026-10-20.md）。
    """
    return {
        "chunk_size": config.CHUNK_SIZE_DEFAULT,
        "chunk_overlap": config.CHUNK_OVERLAP_DEFAULT,
        "top_k": config.TOP_K_DEFAULT,
        "supported_extensions": [".txt", ".md", ".pdf"],
    }


@app.get("/health")
async def health() -> dict:
    """健康检查端点。同样必须 async：探活/负载均衡器会高频打它，
    同步版会把线程池占满，连带把真正的业务请求一起拖慢。

    附带 `llm_gate` 快照（每进程一份，多 worker 时各自报各自的）：
    排查"是不是闸把请求挡在门外"时，第一眼看的就是 rejected / waiting。
    """
    return {"status": "ok", "version": "0.3.0 (W3)", "llm_gate": llm_gate.snapshot()}


if __name__ == "__main__":
    import uvicorn

    # 只监听回环：前端本来就是靠 Next 的 /api/* rewrite 访问它（next.config.ts
    # 指向 http://localhost:8000），后端不需要对公网开放。
    # 现在虽然已经加了登录鉴权，仍然保持回环——少一层暴露面就少一层风险，
    # 而且 8000 端口直连绕过了 Next 的 Cookie 域设置，容易踩登录态丢失的坑。
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
