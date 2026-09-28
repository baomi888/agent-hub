# -*- coding: utf-8 -*-
"""大模型客户端统一封装。

为什么不用 init_chat_model？
  init_chat_model 会根据 provider 名称自动路由，但 LangChain 1.4.1 对
  deepseek / dashscope 这类第三方 provider 支持不稳定（见经验 1251481）。
  DeepSeek 和百炼都严格遵循 OpenAI 兼容接口，直接用 ChatOpenAI /
  OpenAIEmbeddings 绕过 provider 路由层，更可靠。
"""

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from core import config


# ---------- 大模型（DeepSeek Chat）----------

def get_llm(temperature: float = 0.1) -> ChatOpenAI:
    """返回 DeepSeek Chat 客户端（OpenAI 兼容）。

    Args:
        temperature: 生成温度，RAG 场景建议 0.1，Agent 场景可调高
    """
    return ChatOpenAI(
        api_key=config.DEEPSEEK_API_KEY,
        base_url=config.DEEPSEEK_BASE_URL,
        model=config.DEEPSEEK_MODEL,
        temperature=temperature,
    )


# ---------- Embedding（阿里百炼）----------

def get_embeddings() -> OpenAIEmbeddings:
    """返回向量化客户端（OpenAI 兼容接口）。

    默认用阿里百炼 text-embedding-v3，DeepSeek 不提供 embedding 接口。
    更换向量化模型后必须删除 chroma_db/ 目录重建向量库！
    """
    return OpenAIEmbeddings(
        api_key=config.EMBEDDING_API_KEY,
        base_url=config.EMBEDDING_BASE_URL,
        model=config.EMBEDDING_MODEL,
        check_embedding_ctx_length=False,  # 第三方兼容接口关闭 token 长度检查
    )


# ---------- 视觉多模态模型（阿里百炼 qwen-vl-plus）----------

def get_vision_llm(temperature: float = 0.1) -> ChatOpenAI:
    """返回视觉多模态模型客户端，用于图片理解。

    使用阿里百炼 qwen-vl-plus（OpenAI 兼容接口），支持传入图片 content。
    """
    return ChatOpenAI(
        api_key=config.VISION_API_KEY,
        base_url=config.VISION_BASE_URL,
        model=config.VISION_MODEL,
        temperature=temperature,
    )
