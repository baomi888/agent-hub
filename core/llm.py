# -*- coding: utf-8 -*-
"""大模型客户端统一封装。

为什么不用 init_chat_model？
  init_chat_model 会根据 provider 名称自动路由，但 LangChain 1.4.1 对
  deepseek / dashscope 这类第三方 provider 支持不稳定（见经验 1251481）。
  DeepSeek 和百炼都严格遵循 OpenAI 兼容接口，直接用 ChatOpenAI /
  OpenAIEmbeddings 绕过 provider 路由层，更可靠。

为什么要缓存客户端实例？
  构造 ChatOpenAI / OpenAIEmbeddings 会建 httpx 连接池，每次请求 new 一个纯属浪费
  （实测每请求会 new 2~3 个：标题生成 + 流式生成 + 可能的视觉模型）。实例本身不带
  会话状态，一次请求的 invoke / astream 各自独立，可安全地跨请求、跨线程复用。
  按 (temperature, max_tokens) 作键：同参数拿同一实例，参数变了才新建。
"""

import threading

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from core import config

# 思考内容（reasoning_content）只有 provider 专用类才会转发。
# langchain_openai/chat_models/base.py:1-12 的原文警告：
#   "Non-standard response fields added by third-party providers (e.g., reasoning_content)
#    are NOT extracted or preserved. If you are pointing base_url at a provider such as
#    DeepSeek, use the corresponding provider-specific LangChain package instead."
# ⇒ 用 ChatDeepSeek 才拿得到思考流；拿不到就退回 ChatOpenAI（功能不变，只是没有思考内容）。
try:  # pragma: no cover - 取决于部署是否装了该包
    from langchain_deepseek import ChatDeepSeek

    _HAS_DEEPSEEK = True
except Exception:  # noqa: BLE001
    ChatDeepSeek = None  # type: ignore[assignment]
    _HAS_DEEPSEEK = False

_CLIENT_LOCK = threading.Lock()

# 大模型（DeepSeek Chat）：键 = (temperature, max_tokens)
_LLM_CACHE: dict[tuple[float, int | None], ChatOpenAI] = {}
# 思考模型（DeepSeek Reasoner）：单独一份缓存，别和普通模型混用
_REASONER_CACHE: dict[tuple[float, int | None], ChatOpenAI] = {}
# 视觉模型（qwen-vl-plus）：键同上，单独一份，别和纯文本模型混用
_VISION_CACHE: dict[tuple[float, int | None], ChatOpenAI] = {}
# 向量化客户端：无参数，单例
_EMBEDDINGS: OpenAIEmbeddings | None = None


def has_reasoner() -> bool:
    """当前部署是否启用了思考模型（填了 DEEPSEEK_REASONER_MODEL 且装了 langchain-deepseek）。"""
    return bool(config.DEEPSEEK_REASONER_MODEL) and _HAS_DEEPSEEK


def _build(model: str, temperature: float, max_tokens: int | None):
    """按是否装了 langchain-deepseek 选择客户端类。

    两者接口一致（ChatDeepSeek 继承 BaseChatOpenAI），区别只在
    ChatDeepSeek 会把 delta.reasoning_content 放进 additional_kwargs。

    ⚠️ key 为空时必须显式传占位串：两个基类的 validate_environment 都会用
    `api_key_env="OPENAI_API_KEY"` 兜底，不传就会**静默拿环境里另一个 key 去请求**，
    既不报错也不走我们要的 base_url。占位串保证「没配 key」= 请求时 401，而不是串到别处。
    """
    kwargs = dict(
        api_key=config.DEEPSEEK_API_KEY or "not-set",
        base_url=config.DEEPSEEK_BASE_URL,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        request_timeout=config.LLM_TIMEOUT,
        max_retries=config.LLM_MAX_RETRIES,
    )
    if _HAS_DEEPSEEK:
        return ChatDeepSeek(**kwargs)
    return ChatOpenAI(**kwargs)


# ---------- 大模型（DeepSeek Chat）----------

def get_llm(temperature: float = 0.1, max_tokens: int | None = None) -> ChatOpenAI:
    """返回 DeepSeek Chat 客户端（OpenAI 兼容），按参数缓存复用。

    Args:
        temperature: 生成温度，RAG 场景建议 0.1，Agent 场景可调高
        max_tokens: 单次回答的 token 上限。不传时用平台默认；
            传了就给回答长度兜底，防止失控长文烧 token
    """
    key = (temperature, max_tokens)
    with _CLIENT_LOCK:
        llm = _LLM_CACHE.get(key)
        if llm is None:
            llm = _build(config.DEEPSEEK_MODEL, temperature, max_tokens)
            _LLM_CACHE[key] = llm
    return llm


# ---------- 思考模型（DeepSeek Reasoner，可选）----------

def get_reasoner_llm(temperature: float = 0.1, max_tokens: int | None = None) -> ChatOpenAI:
    """返回思考模型客户端；未配置时直接抛错，由调用方决定降级。

    为什么单独一个函数而不是给 get_llm 加参数：
    调用方需要能**明确知道**当前跑的是不是思考模型（决定要不要发 reasoning 事件），
    用返回值类型区分不出来的东西，不如用入口区分。
    """
    if not has_reasoner():
        raise RuntimeError(
            "未启用思考模型：请在 .env 里设置 DEEPSEEK_REASONER_MODEL=deepseek-reasoner，"
            "并确认已安装 langchain-deepseek"
        )
    key = (temperature, max_tokens)
    with _CLIENT_LOCK:
        llm = _REASONER_CACHE.get(key)
        if llm is None:
            llm = _build(config.DEEPSEEK_REASONER_MODEL, temperature, max_tokens)
            _REASONER_CACHE[key] = llm
    return llm


def pick_llm(mode_reasoner: bool, temperature: float = 0.1, max_tokens: int | None = None) -> tuple[ChatOpenAI, bool]:
    """按「本次要不要思考」挑客户端，返回 (客户端, 是否真的是思考模型)。

    第二个返回值是给流式层用的：**只有真的走思考模型才应该发 reasoning 事件**，
    否则前端会等一个永远不来的事件。
    """
    if mode_reasoner and has_reasoner():
        return get_reasoner_llm(temperature, max_tokens), True
    return get_llm(temperature, max_tokens), False


# ---------- Embedding（阿里百炼）----------

def get_embeddings() -> OpenAIEmbeddings:
    """返回向量化客户端（单例）。

    默认用阿里百炼 text-embedding-v3，DeepSeek 不提供 embedding 接口。
    更换向量化模型后必须删除 chroma_db/ 目录重建向量库！
    """
    global _EMBEDDINGS
    if _EMBEDDINGS is None:
        with _CLIENT_LOCK:
            if _EMBEDDINGS is None:
                _EMBEDDINGS = OpenAIEmbeddings(
                    api_key=config.EMBEDDING_API_KEY,
                    base_url=config.EMBEDDING_BASE_URL,
                    model=config.EMBEDDING_MODEL,
                    check_embedding_ctx_length=False,  # 第三方兼容接口关闭 token 长度检查
                    request_timeout=config.EMBED_TIMEOUT,
                    max_retries=config.EMBED_MAX_RETRIES,
                )
    return _EMBEDDINGS


# ---------- 视觉多模态模型（阿里百炼 qwen-vl-plus）----------

def get_vision_llm(temperature: float = 0.1, max_tokens: int | None = None) -> ChatOpenAI:
    """返回视觉多模态模型客户端，用于图片理解。按参数缓存复用。

    使用阿里百炼 qwen-vl-plus（OpenAI 兼容接口），支持传入图片 content。
    """
    key = (temperature, max_tokens)
    with _CLIENT_LOCK:
        llm = _VISION_CACHE.get(key)
        if llm is None:
            llm = ChatOpenAI(
                api_key=config.VISION_API_KEY,
                base_url=config.VISION_BASE_URL,
                model=config.VISION_MODEL,
                temperature=temperature,
                max_tokens=max_tokens,
                request_timeout=config.LLM_TIMEOUT,
                max_retries=config.LLM_MAX_RETRIES,
            )
            _VISION_CACHE[key] = llm
    return llm
