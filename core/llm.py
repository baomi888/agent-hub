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

_CLIENT_LOCK = threading.Lock()

# 大模型（DeepSeek Chat）：键 = (temperature, max_tokens)
_LLM_CACHE: dict[tuple[float, int | None], ChatOpenAI] = {}
# 视觉模型（qwen-vl-plus）：键同上，单独一份，别和纯文本模型混用
_VISION_CACHE: dict[tuple[float, int | None], ChatOpenAI] = {}
# 向量化客户端：无参数，单例
_EMBEDDINGS: OpenAIEmbeddings | None = None


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
            llm = ChatOpenAI(
                api_key=config.DEEPSEEK_API_KEY,
                base_url=config.DEEPSEEK_BASE_URL,
                model=config.DEEPSEEK_MODEL,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            _LLM_CACHE[key] = llm
    return llm


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
            )
            _VISION_CACHE[key] = llm
    return llm
