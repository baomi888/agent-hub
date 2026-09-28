# -*- coding: utf-8 -*-
"""Pydantic 请求/响应模型。"""

from pydantic import BaseModel, Field

from core import config


class KBCreateRequest(BaseModel):
    """新建知识库请求（可选：指定切片参数，默认用全局值）。"""
    kb_id: str = Field(..., description="知识库唯一标识（如 nba / science）")


class KBBuildRequest(BaseModel):
    """上传并构建索引请求。"""
    chunk_size: int = Field(default=config.CHUNK_SIZE_DEFAULT, gt=0)
    chunk_overlap: int = Field(default=config.CHUNK_OVERLAP_DEFAULT, ge=0)


class KBQueryRequest(BaseModel):
    """同步问答请求。"""
    question: str = Field(..., description="用户问题")
    top_k: int = Field(default=config.TOP_K_DEFAULT, gt=0)


class KBQueryResponse(BaseModel):
    """同步问答响应。"""
    answer: str
    refs: str


class ErrorResponse(BaseModel):
    """统一错误响应。"""
    detail: str
