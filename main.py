# -*- coding: utf-8 -*-
"""集合式 Agent 后端入口。

启动前必须保证：
  1. .env 已配置（DEEPSEEK_API_KEY / EMBEDDING_API_KEY / EMBEDDING_BASE_URL）
  2. 依赖已安装（requirements.txt）

启动方式：
  uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.chat import router as chat_router
from api.kb import router as kb_router
from api.sessions import router as sessions_router
from core import config
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
app.include_router(kb_router, prefix="/api/kb", tags=["知识库"])
app.include_router(sessions_router, prefix="/api/sessions", tags=["会话"])
app.include_router(chat_router, prefix="/api/chat", tags=["问答"])


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
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
