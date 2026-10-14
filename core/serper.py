# -*- coding: utf-8 -*-
"""Serper（Google）联网搜索的统一入口。

之前 agent/tools.py（web_search）和 kb/importer.py（search_preview）各写了一遍
Serper POST：超时、请求头、错误处理已经分叉，改一处漏一处；且每次都是裸
httpx.post，重建 TLS 连接白丢 100~300ms。Serper 又**按次计费**——Agent 重试、
多人搜同一热点，相同关键词短时间内不该重复扣费。

这里集中三件事：
  1. 模块级共享 httpx.Client：连接复用，省掉重复 TLS 握手；
  2. TTL LRU 缓存（10 分钟 × 64 条）：同词同数量直接命中，零费用零延迟；
  3. single-flight：并发同词只发一次真实请求，其余等待复用结果；
  4. App 类查询补「官网」的改写（boost_app_query）也从 kb/importer 挪过来，
     两个调用方共用同一份规则（此前只 importer 有，tools 是后补的拷贝）。
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict

import httpx

from core import config

_ENDPOINT = "https://google.serper.dev/search"
_TIMEOUT = 15
_CACHE_TTL = 600.0  # 10 分钟：热点窗口内重复查询不重复扣费
_CACHE_MAX = 64

# 模块级共享 Client（连接复用）；httpx.Client 本身线程安全
_client: httpx.Client | None = None
_client_lock = threading.Lock()

_cache: OrderedDict[tuple, tuple[float, dict]] = OrderedDict()
_cond = threading.Condition()
_inflight: set[tuple] = set()


def _get_client() -> httpx.Client:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = httpx.Client(
                    timeout=_TIMEOUT,
                    headers={"User-Agent": "agent-hub/1.0"},
                    follow_redirects=True,
                )
    return _client


def search_serper(query: str, count: int = 10) -> dict | None:
    """调 Serper 搜索，返回原始 JSON；未配置 key / 查询为空 / 请求失败返回 None。

    不在这一层裁剪 organic：原始 JSON 里还有 knowledgeGraph / answerBox，
    调用方（如 web_search）需要它们做"无结果时的简介兜底"。
    返回对象按只读使用（缓存共享，调用方请勿修改）。
    """
    q = boost_app_query(query)
    if not q or not config.SERPER_API_KEY:
        return None
    key = (q, count)
    now = time.monotonic()
    with _cond:
        while True:
            hit = _cache.get(key)
            if hit is not None:
                ts, data = hit
                if now - ts < _CACHE_TTL:
                    _cache.move_to_end(key)
                    return data
                del _cache[key]
            if key not in _inflight:
                _inflight.add(key)
                break  # 由本线程发请求
            _cond.wait()  # 别的线程正在请求同一个词，等它完成后从缓存拿
    try:
        resp = _get_client().post(
            _ENDPOINT,
            headers={"X-API-KEY": config.SERPER_API_KEY, "Content-Type": "application/json"},
            # gl/hl 锚定中国区 + 中文：面向国内用户压低国外结果，
            # 否则 Google 全球接口会返回大量国内打不开的外站链接（承接 main edc781b 的意图）。
            json={"q": q, "num": count, "gl": "cn", "hl": "zh-cn"},
        )
        resp.raise_for_status()
        data = resp.json() or {}
        with _cond:
            _cache[key] = (time.monotonic(), data)
            _cache.move_to_end(key)
            while len(_cache) > _CACHE_MAX:
                _cache.popitem(last=False)
        return data
    except Exception:
        return None
    finally:
        with _cond:
            _inflight.discard(key)
            _cond.notify_all()


# ==================== App 类查询补「官网」====================
# 从 kb/importer.py 挪来：web_search 与 search_preview 共用同一份改写规则。

_APP_SIGNALS = ("apk", "app", "android", "安卓", "ios", "软件", "应用", "客户端",
                "安装包", "官网", "官方", "下载", "安装")

_APP_STOP = ("什么", "如何", "怎么", "为什么", "教程", "论文", "文档", "文章", "新闻",
             "天气", "介绍", "意思", "区别", "对比", "评价", "推荐", "好处", "原理")

_DOWNLOAD_INTENT = ("下载", "安装", "apk", "安装包")

# 裸词里会被误判为 App 的非 App 短词（精确匹配，避免把「学习强国」这类真 App 拦掉）
_APP_NAME_BLOCK = {
    "深度学习", "机器学习", "人工智能", "神经网络", "数据科学", "大数据",
    "计算机", "编程", "前端", "后端", "算法", "数据库",
    "数学", "物理", "化学", "历史", "地理", "生物", "语文", "英语", "政治",
    "区块链", "元宇宙", "云计算", "微服务", "容器",
    "python", "java", "linux", "mysql",
}


def boost_app_query(keyword: str) -> str:
    """把 App / 软件类搜索补上「官网」，让官方下载页排到前面。

    触发条件：关键词含 App 相关信号，或是一个很短的裸词（大概率是 App 名）。
    含「什么 / 如何 / 教程…」等泛问词时不做改写，避免影响普通搜索。
    """
    kw = (keyword or "").strip()
    if not kw:
        return kw
    low = kw.lower()
    if any(s in low for s in _APP_STOP):
        return kw
    has_signal = any(s in low for s in _APP_SIGNALS)
    is_bare = len(kw) <= 6 and not any(ch.isspace() for ch in kw) \
        and not any(ch in kw for ch in "？?！!，,。.：:") \
        and low not in _APP_NAME_BLOCK
    if not (has_signal or is_bare):
        return kw
    if "官网" in kw or "官方" in kw:
        return kw
    if "下载" in kw:
        return f"{kw} 官网"
    if any(s in low for s in _DOWNLOAD_INTENT):
        return f"{kw} 官网下载"
    return f"{kw} 官网"
