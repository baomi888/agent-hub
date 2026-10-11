# -*- coding: utf-8 -*-
from __future__ import annotations
"""RAG 核心逻辑（文档加载 → 切片 → Embedding → Chroma → 检索 → 提示词组装 → LLM 生成）

与界面完全分离，可被 Agent / FastAPI / 后续前端复用。
迁移自 rag-knowledge-base-demo/rag_core.py，适配 LangChain v1.x：
  - import config → from core import config
  - get_llm / get_embeddings → from core.llm import get_llm / get_embeddings
  - 其余 LangChain API（ChatOpenAI / OpenAIEmbeddings / Chroma / TextSplitter）
    在 v1.x 中签名不变，直接复用。
"""

import math
import os
import re
import threading
import uuid
from collections import Counter, OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor

import chromadb
from langchain_chroma import Chroma
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_text_splitters import RecursiveCharacterTextSplitter

from core import config
from core.llm import get_embeddings, get_llm

SUPPORTED_EXT = (".txt", ".md", ".pdf")  # .md 按纯文本加载，Markdown 语法不影响切片


# ==================== Chroma 客户端单例 ====================

_CHROMA_LOCK = threading.Lock()
_CHROMA_CLIENT = None


def chroma_client():
    """全局共用一个 PersistentClient。

    实测：new 一个 PersistentClient 首次 0.588s、之后稳定 0.098s/次（重开 sqlite +
    重建内部缓存）。而 exists / list_all / get_status / peek 这几个"只读一下"的接口
    每次调用都新建了一个——它们还全在 async 端点里，等于每条请求都要卡住事件循环
    几十到几百毫秒。

    进程级单例足够：config.PERSIST_DIR 是常量，运行期不会变。
    副产品：多个 client 指向同一个 sqlite 文件的锁竞争隐患也一并消掉。
    """
    global _CHROMA_CLIENT
    if _CHROMA_CLIENT is None:
        with _CHROMA_LOCK:
            if _CHROMA_CLIENT is None:
                _CHROMA_CLIENT = chromadb.PersistentClient(path=config.PERSIST_DIR)
    return _CHROMA_CLIENT

# RAG 系统提示词：仅依据参考资料回答；资料不足时说明情况并给出下一步，不做冷拒答
SYSTEM_PROMPT = """你是一个严谨的问答助手，必须遵守以下规则：
1. 仅依据【参考资料】回答用户问题，不得使用资料之外的任何知识；
2. 回答末尾注明依据的片段编号，例如（依据：[1][3]）；
3. 如果参考资料不足以回答问题，不要只甩一句冷冰冰的拒答。先一句话说明「知识库里暂时没有这段内容，所以我不能按资料乱答」，再给出下一步（把相关文档放进这个知识库，或者换成联网搜索），语气自然、简短；
4. 用中文简洁作答，不要编造任何资料中没有的信息；
5. 如果用户输入的是词语或短语（而非完整问题），则根据参考资料围绕该主题做简要介绍。"""


def describe_api_error(e: Exception) -> str:
    """把异常翻译成中文提示（兼容 openai SDK + HTTP 层异常）。"""
    cls_name = type(e).__name__
    # openai 认证/网络/限额类异常
    if cls_name == "AuthenticationError" or "401" in str(e):
        return "401 认证失败：密钥无效，请检查 .env 中各接口对应的 Key 与 BASE_URL"
    if cls_name in ("APIConnectionError", "ConnectError", "ConnectionError") or "connect" in str(e).lower():
        return "网络连接失败：请检查网络/代理以及各 BASE_URL 配置"
    if cls_name == "RateLimitError" or "429" in str(e):
        return "429 请求受限：请求过于频繁或额度不足，请稍后再试"
    if "402" in str(e):
        return "402 余额不足：请到对应平台充值"
    if cls_name == "NotFoundError" or "404" in str(e):
        return "404 模型不存在：请检查 EMBEDDING_MODEL / DEEPSEEK_MODEL 名称"
    return f"{cls_name}: {e}"


# ==================== 检索结果挑选 ====================


def _shingles(text: str) -> set[str]:
    """文本转字符 2-gram 集合。

    中文没有空格可分词，用字符 bigram 近似"两段话有多大重合"够用了——
    切片重叠 50 字时，相邻片段的 bigram 重合度会非常高，正好是我们要去重的对象。
    """
    t = re.sub(r"\s+", "", text or "")
    if len(t) < 2:
        return {t} if t else set()
    return {t[i : i + 2] for i in range(len(t) - 1)}


def _overlap(a: set[str], b: set[str]) -> float:
    """两段文本的重合度（Jaccard），0 = 完全不像，1 = 基本同一段。"""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _to_similarity(distance: float) -> float | None:
    """Chroma 的距离换算成余弦相似度。

    距离口径（已实测确认，2026-10-03）：集合创建时没有指定 hnsw:space，
    走的是 Chroma 默认的**平方 L2**；而百炼 text-embedding-v3 返回的向量
    **已经是单位向量**（本地库 11 条实测 L2 范数全为 1.0000）。
    平方 L2 在单位向量上有 d = ||a-b||^2 = 2 - 2cos，因此 cos = 1 - d/2，
    **d 的合法值域是 [0, 4]**，对应 cos ∈ [-1, 1]。

    ⚠️ 此前这里按余弦距离的 [0, 2] 判定，导致 d ∈ (2, 4]（即 cos < 0，
    明显不相关的片段）被当成"换算不了"。后果有两个，都有离线回归盯着：
    ① select_diverse 里 `all(...)` 短路 → **RETRIEVE_MIN_SIM 这道阈值整条被跳过**；
    ② relevance() 退化为批次内相对排序 → 负余弦片段反而拿到虚高的相关度，
       把真正相关的片段挤出名额。
    把守卫改成 [0,2] 重跑 tests/test_retrieve_select.py，用例 3 与 3b 都会失败，
    改回 [0,4] 即 ALL PASS——这就是这两条后果的直接证据。
    线上表现：参考资料卡片把类目「相似度」标成 1.089 / 1.160 / 1.237（实为距离）。

    换算不成立时（例如换了没归一化的 embedding 模型）仍返回 None 让调用方
    跳过过滤——宁可少滤几个，也不能把相关片段误杀。
    """
    if distance is None or distance < 0 or distance > 4:
        return None
    return 1 - distance / 2


# ==================== 关键词通道（BM25）====================
# 纯向量检索对专有名词 / 缩写 / 编号很弱：库里写着「CUDA 12.4」「第 3 条」，
# 问「CUDA 12.4」时向量可能被语义相近但没这个词的片段挤掉。BM25 按词面命中，
# 正好补这条。两条通道用 RRF 融合，不需要调分数尺度，也不用训练。

_ASCII_WORD = re.compile(r"[a-z0-9]+")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]+")


def _tokenize(text: str) -> list[str]:
    """中英混合分词：ASCII 取词、CJK 取字符 bigram。

    不引第三方分词器（jieba 会拖一串依赖，本项目一直避免）。字符 bigram 对
    中文专有名词/编号的召回已经够用，口径也和 _shingles 一致。
    """
    t = (text or "").lower()
    tokens = _ASCII_WORD.findall(t)
    for run in _CJK_RUN.findall(t):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


class _BM25:
    """极简 BM25（k1=1.5, b=0.75），只用标准库。

    用倒排表 term → [(doc_idx, tf)]，打分只扫命中词命中的文档，
    避免 O(词数 × 库大小) 的全量打分。
    """

    __slots__ = ("k1", "b", "n", "avgdl", "doc_len", "idf", "postings")

    def __init__(self, tokenized: list[list[str]]) -> None:
        self.k1, self.b = 1.5, 0.75
        self.n = len(tokenized)
        self.doc_len = [len(t) for t in tokenized]
        self.avgdl = (sum(self.doc_len) / self.n) if self.n else 0.0
        postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        df: Counter = Counter()
        for i, toks in enumerate(tokenized):
            for term, f in Counter(toks).items():
                postings[term].append((i, f))
                df[term] += 1
        # 带 +0.5 平滑的 IDF，恒为正，避免常见词拿到负分
        self.idf = {t: math.log(1 + (self.n - d + 0.5) / (d + 0.5)) for t, d in df.items()}
        self.postings = postings

    def top(self, query: str, k: int) -> list[tuple[int, float]]:
        """返回 [(doc_idx, score)]，按分数降序取前 k 条。"""
        if self.n == 0 or k <= 0:
            return []
        scores: dict[int, float] = defaultdict(float)
        avgdl = self.avgdl or 1.0
        for term in _tokenize(query):
            idf = self.idf.get(term)
            if idf is None:
                continue
            for i, f in self.postings[term]:
                dl = self.doc_len[i] or 1
                denom = f + self.k1 * (1 - self.b + self.b * dl / avgdl)
                scores[i] += idf * f * (self.k1 + 1) / denom
        if not scores:
            return []
        return sorted(scores.items(), key=lambda x: -x[1])[:k]


# 关键词索引按库缓存：整库拉全文是重操作，只在内容变更后重建一次。
# 库内容变更时由 _cache_invalidate 清掉，下次检索懒加载重建。
_BM25_CACHE: OrderedDict[str, tuple[list, _BM25]] = OrderedDict()
_BM25_CACHE_MAX = 8
_BM25_LOCK = threading.Lock()


def _get_bm25(vs, collection_name: str):
    """取该库的 (docs, BM25) 索引（带缓存）。库太大或读取失败返回 None。"""
    with _BM25_LOCK:
        hit = _BM25_CACHE.get(collection_name)
        if hit is not None:
            _BM25_CACHE.move_to_end(collection_name)
            return hit
    got = vs._collection.get(include=["documents", "metadatas"])
    texts = got.get("documents") or []
    if len(texts) > config.HYBRID_MAX_DOCS:
        return None  # 超过闸值：放弃关键词通道，退回纯向量，别把全文堆进内存
    metas = got.get("metadatas") or []
    ids = got.get("ids") or []
    docs = [
        Document(page_content=t or "", metadata=m or {}, id=i)
        for t, m, i in zip(texts, metas, ids)
    ]
    index = _BM25([_tokenize(d.page_content) for d in docs])
    entry = (docs, index)
    with _BM25_LOCK:
        _BM25_CACHE[collection_name] = entry
        _BM25_CACHE.move_to_end(collection_name)
        while len(_BM25_CACHE) > _BM25_CACHE_MAX:
            _BM25_CACHE.popitem(last=False)
    return entry


def _rrf_merge(vec_cands, kw_pairs, kw_docs, rrf_k: int) -> list:
    """向量候选与关键词候选用 RRF 融合。

    Args:
        vec_cands: [(Document, distance)]，已按距离升序
        kw_pairs:  [(doc_idx, bm25_score)]，已按分数降序
        kw_docs:   关键词索引对应的 Document 列表（下标与 doc_idx 对齐）
        rrf_k:     融合常数

    Returns:
        [(Document, distance_or_None, rank)]，rank ∈ (0, 1]，越大越相关。
        distance 为 None 表示该片段只被关键词通道召回（没有向量距离）。
    """
    fused: dict[str, float] = defaultdict(float)
    dist: dict[str, float | None] = {}
    by_id: dict[str, object] = {}
    for rank, (d, distance) in enumerate(vec_cands, start=1):
        did = d.id or f"v{rank}"
        fused[did] += 1.0 / (rrf_k + rank)
        dist[did] = distance
        by_id[did] = d
    for rank, (idx, _score) in enumerate(kw_pairs, start=1):
        d = kw_docs[idx]
        did = d.id or f"k{rank}"
        fused[did] += 1.0 / (rrf_k + rank)
        dist.setdefault(did, None)  # 关键词独有 → 无向量距离
        by_id.setdefault(did, d)
    if not fused:
        return []
    order = sorted(fused.items(), key=lambda x: -x[1])
    top = order[0][1] or 1.0
    return [(by_id[did], dist.get(did), score / top) for did, score in order]


def select_diverse(docs_scores, top_k: int, lambda_mult: float | None = None) -> list:
    """从候选里挑出 top_k 条：先丢明显不相关的，再按 MMR 去掉互相重复的。

    原来直接取 top-k 有两个毛病：相似度再低的片段也照样进 prompt（模型被迫硬答），
    以及相邻切片的重叠部分会把名额占满（三条里两条是同一段话）。

    Args:
        docs_scores: [(Document, distance), ...] 或 [(Document, distance, rank), ...]。
            distance 越小越相关；distance 为 None 表示仅关键词通道召回（无向量距离）；
            rank ∈ (0,1] 为融合相关度，给了就按它排序（混合检索用）。
        top_k: 最终要几条
        lambda_mult: 相关度权重，默认取 config；1 = 退化为纯相关度排序

    Returns:
        挑选后的 [(Document, distance), ...]，顺序按"先选中的在前"
    """
    cands = list(docs_scores or [])
    if top_k <= 0 or not cands:
        return []
    lam = config.RETRIEVE_MMR_LAMBDA if lambda_mult is None else lambda_mult
    lam = min(max(lam, 0.0), 1.0)

    def _dist(c):
        return c[1]

    def _rank(c):
        return c[2] if len(c) > 2 else None

    # ---- 1. 离群过滤：把明显不相关的尾巴砍掉 ----
    min_sim = config.RETRIEVE_MIN_SIM
    if min_sim > 0:
        # 只按"向量距离"判定能否换算：None 是关键词命中（本就没有向量距离），
        # 不参与这条全局判定；越界浮点才是"向量未归一化"的全局信号。
        # 全部向量距离可换算时才过滤；关键词命中一律保留（BM25 已保证词面相关）。
        vec_dists = [d for d in (_dist(c) for c in cands) if d is not None]
        if vec_dists and all(_to_similarity(d) is not None for d in vec_dists):
            kept = [
                c for c in cands
                if _dist(c) is None or _to_similarity(_dist(c)) >= min_sim
            ]
            # 别把结果砍空：一条不剩时至少留最相关的那条，让模型自己说"资料里没有"
            cands = kept or [
                min(cands, key=lambda c: _dist(c) if _dist(c) is not None else float("inf"))
            ]

    # 候选还不够挑，MMR 没意义（离群过滤已经在上面做过了）
    if len(cands) <= top_k:
        return [(c[0], c[1]) for c in cands]

    # ---- 2. MMR 贪心：相关度高、且和已选内容重复度低的优先 ----
    dists = [_dist(c) for c in cands]
    known = [d for d in dists if d is not None]
    lo, hi = (min(known), max(known)) if known else (0.0, 1.0)
    span = hi - lo

    def relevance(c) -> float:
        """相关度（0~1，越大越相关）。

        优先用融合 rank（混合检索），其次用绝对余弦相似度，而不是批次内
        min-max 归一化——被重叠切片塞满时，几条候选的距离往往只差 0.0x，
        归一化会把这点差距放大成"最相关 vs 最不相关"，结果重复片段靠微弱优势
        把名额全占了。
        """
        r = _rank(c)
        if r is not None:
            return r
        d = _dist(c)
        if d is None:
            return 0.0
        sim = _to_similarity(d)
        if sim is not None:
            return sim
        # 距离换不成余弦（向量未归一化）时，退回批次内相对排序
        return 1.0 if span < 1e-9 else 1 - (d - lo) / span

    def _first_key(i: int) -> float:
        """基准条的排序键：有 rank 按 rank（大者优先），否则按距离（小者优先）。"""
        r = _rank(cands[i])
        if r is not None:
            return -r
        d = _dist(cands[i])
        return d if d is not None else float("inf")

    shingles = [_shingles(d.page_content) for d, *_ in cands]
    order: list[int] = []
    remaining = list(range(len(cands)))
    # 第一条固定取最相关的，给后面的多样性比较一个基准
    first = min(remaining, key=_first_key)
    order.append(first)
    remaining.remove(first)

    while remaining and len(order) < top_k:
        best_i, best_val = -1, -1e9
        for i in remaining:
            rel = relevance(cands[i])
            dup = max(_overlap(shingles[i], shingles[j]) for j in order)
            val = lam * rel - (1 - lam) * dup
            if val > best_val:
                best_i, best_val = i, val
        order.append(best_i)
        remaining.remove(best_i)

    return [(cands[i][0], cands[i][1]) for i in order]


# ==================== 检索结果缓存 ====================
# 相同 (库, 问题, top_k) 在知识库内容不变时结果确定，直接复用能跳过
# 重复的 embedding + 向量查询——省的是真实 API 费用和用户等待时间。
# 知识库内容变更（导入/删除/重建）必须清掉对应库的缓存，见 _cache_invalidate。

_RETRIEVE_CACHE: OrderedDict[tuple, list] = OrderedDict()
_RETRIEVE_CACHE_MAX = 200
_RETRIEVE_CACHE_LOCK = threading.Lock()

# 来源清单缓存：list_sources 要遍历整库元数据做聚合，Chroma 没有 distinct/GROUP BY，
# 无法免扫描。但同一份内容重复调用（列表页轮询、_resolve_source 多次解析）结果不变，
# 缓存下来即可省掉重复的整库遍历；库内容变更时随检索缓存一起失效。
_SOURCES_CACHE: OrderedDict[str, list] = OrderedDict()
_SOURCES_CACHE_MAX = 64
_SOURCES_CACHE_LOCK = threading.Lock()

# list_sources 分页大小：每页只物化这么多条 metadatas，峰值内存与库大小解耦
SOURCES_PAGE = 1000


def _cache_invalidate(collection_name: str) -> None:
    """知识库内容变更后清掉该库的检索缓存、来源清单缓存与关键词索引，避免读到旧片段。"""
    with _RETRIEVE_CACHE_LOCK:
        for key in [k for k in _RETRIEVE_CACHE if k[0] == collection_name]:
            _RETRIEVE_CACHE.pop(key, None)
    with _SOURCES_CACHE_LOCK:
        _SOURCES_CACHE.pop(collection_name, None)
    with _BM25_LOCK:
        _BM25_CACHE.pop(collection_name, None)


def _cache_get(collection_name: str, question: str, top_k: int):
    """命中返回结果的浅拷贝，防止调用方误改污染缓存。"""
    key = (collection_name, question, top_k)
    with _RETRIEVE_CACHE_LOCK:
        hit = _RETRIEVE_CACHE.get(key)
        if hit is not None:
            _RETRIEVE_CACHE.move_to_end(key)
            return list(hit)
    return None


def _cache_set(collection_name: str, question: str, top_k: int, result) -> None:
    key = (collection_name, question, top_k)
    with _RETRIEVE_CACHE_LOCK:
        _RETRIEVE_CACHE[key] = list(result)
        _RETRIEVE_CACHE.move_to_end(key)
        while len(_RETRIEVE_CACHE) > _RETRIEVE_CACHE_MAX:
            _RETRIEVE_CACHE.popitem(last=False)


def _sources_cache_get(collection_name: str):
    """命中返回逐条浅拷贝，调用方改动不会污染缓存。"""
    with _SOURCES_CACHE_LOCK:
        hit = _SOURCES_CACHE.get(collection_name)
        if hit is not None:
            _SOURCES_CACHE.move_to_end(collection_name)
            return [dict(x) for x in hit]
    return None


def _sources_cache_set(collection_name: str, result: list) -> None:
    with _SOURCES_CACHE_LOCK:
        _SOURCES_CACHE[collection_name] = [dict(x) for x in result]
        _SOURCES_CACHE.move_to_end(collection_name)
        while len(_SOURCES_CACHE) > _SOURCES_CACHE_MAX:
            _SOURCES_CACHE.popitem(last=False)


# ==================== 查询向量缓存 ====================
# embedding 只取决于查询文本（与库内容无关），因此不随库内容失效。
# 检索结果缓存命中时根本走不到这里；这里是给"同一问题、不同 top_k"准备的——
# 那种情况结果缓存键不同会 miss，但查询向量可复用，省掉一次约 250ms 的
# embedding 网络请求（也是省真实 API 费用）。

_EMBED_CACHE: OrderedDict[str, list[float]] = OrderedDict()
_EMBED_CACHE_MAX = 500
_EMBED_CACHE_LOCK = threading.Lock()


def _embed_query(question: str) -> list[float]:
    """取查询向量（带 LRU 缓存）。"""
    with _EMBED_CACHE_LOCK:
        hit = _EMBED_CACHE.get(question)
        if hit is not None:
            _EMBED_CACHE.move_to_end(question)
            return hit
    vec = get_embeddings().embed_query(question)
    with _EMBED_CACHE_LOCK:
        _EMBED_CACHE[question] = vec
        _EMBED_CACHE.move_to_end(question)
        while len(_EMBED_CACHE) > _EMBED_CACHE_MAX:
            _EMBED_CACHE.popitem(last=False)
    return vec


# ==================== 查询改写（多轮追问 → 独立检索问题）====================
# 多轮里的追问（"那它呢""第二个呢"）单独看没有检索价值：向量与 BM25 都抓不到
# 指代对象。用对话历史把它补全成可独立检索的问题。只在"看起来像追问"时才多花
# 一次 LLM 调用，其余问题零额外开销（这是它跟"每题都改写"的关键区别）。

_REWRITE_CUES = (
    "它", "他", "她", "这个", "那个", "这些", "那些", "该", "此", "其",
    "上述", "前者", "后者", "这样", "那样", "这里", "那里",
)


def needs_rewrite(question: str, history) -> bool:
    """判断问题是否依赖上下文、值得改写。没有历史时一律不改写。"""
    if not history:
        return False
    q = (question or "").strip()
    if not q:
        return False
    if len(q) <= config.QUERY_REWRITE_MAX_LEN:
        return True
    return any(cue in q for cue in _REWRITE_CUES)


def rewrite_query(question: str, history) -> str:
    """把依赖上下文的追问改写成可独立检索的问题；不需要或失败时原样返回。

    history: [{"role": "user"/"assistant", "content": "..."}]，按时间升序。
    失败一律退回原问题——改写是增益不是必需，绝不能因为它把检索搞挂。
    """
    if not config.QUERY_REWRITE_ENABLED or not needs_rewrite(question, history):
        return question
    recent = history[-config.QUERY_REWRITE_HISTORY :]
    convo = "\n".join(
        f"{'用户' if m.get('role') == 'user' else '助手'}：{(m.get('content') or '')[:200]}"
        for m in recent
    )
    prompt = (
        "你是检索查询改写器。根据下面的对话历史，把用户的最新问题改写成一条"
        "可以脱离上下文独立检索的完整问题。\n"
        "规则：只补全指代和省略，不回答问题，不添加历史中没有的信息；"
        "若最新问题本身已经完整，就原样输出。\n"
        "只输出改写后的问题本身，不要解释，不要引号。\n\n"
        f"【对话历史】\n{convo}\n\n【最新问题】\n{question}"
    )
    try:
        resp = get_llm(temperature=0, max_tokens=config.QUERY_REWRITE_MAX_TOKENS).invoke(
            [HumanMessage(content=prompt)]
        )
        out = (resp.content or "").strip().strip('"').strip("'").splitlines()[0].strip()
    except Exception:
        return question
    # 结果不可信就退回原问题：空、过长（模型跑偏成回答）都算
    if not out or len(out) > 200:
        return question
    return out


# ==================== 文档加载 ====================

def load_document(file_path: str, source_name: str | None = None):
    """加载单个文档，返回 Document 列表。

    支持格式：txt / md / pdf。txt 自动尝试 utf-8 和 gbk（Windows 记事本兜底）。

    Args:
        source_name: 写入 metadata["source"] 的展示名（一般用原始文件名）。
            切片入库后靠它做文件级删除，不能再用临时路径——临时文件会被清掉。
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"文件不存在：{file_path}")
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in SUPPORTED_EXT:
        raise ValueError(f"不支持的文档格式：'{ext}'（仅支持 txt/md/pdf）")

    if ext in (".txt", ".md"):
        try:
            docs = TextLoader(file_path, encoding="utf-8").load()
        except UnicodeDecodeError:
            docs = TextLoader(file_path, encoding="gbk").load()
    # .pdf：PyPDFLoader 失败时给出可读的错误提示
    else:
        try:
            docs = PyPDFLoader(file_path).load()
        except Exception as e:
            raise ValueError(
                f"PDF 解析失败：{e}。可能原因：① 扫描版 PDF（无文字层）需先 OCR；"
                f"② 文件加密或损坏；③ 文件过大。建议转换为 TXT/Markdown 后再上传。"
            )
    if not docs or all(not (d.page_content or "").strip() for d in docs):
        raise ValueError(
            "PDF 未提取到任何文字内容。若为扫描件请先 OCR，或导出为 TXT/Markdown 后再上传。"
        )

    # 统一 source：用稳定的文件名而不是临时路径，并按页码保留定位信息
    for d in docs:
        d.metadata["source_path"] = file_path
        if source_name:
            d.metadata["source"] = source_name
    return docs


def split_documents(docs, chunk_size: int, chunk_overlap: int):
    """RecursiveCharacterTextSplitter 切片。"""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""],
        length_function=len,
    )
    return splitter.split_documents(docs)


# ==================== RAG 链路 ====================


def _embed_and_store(vs, chunks) -> None:
    """并发 embedding + 串行 upsert（分窗，峰值内存受限）。

    入库的真正瓶颈是 embedding 的网络往返：每批 EMBED_BATCH_SIZE 条一次请求，
    原实现一批批串行等待，N 批就是 N 个 RTT 首尾相接。这里把各批的
    embed_documents 丢进线程池同时发出，等向量回来后再**串行** upsert。

    为什么写入不并发：多个线程同时往同一个 Chroma collection 写，会竞争
    hnsw 索引与底层 sqlite，结果不确定。embedding 是纯网络调用，天然可并发；
    写入留单线程既安全，又不构成瓶颈（本地磁盘写很快）。

    为什么分窗：一次把全部批都发出去虽然最快，但要把整份文档的向量都留在
    内存里（几千片 × 上千维就是几十 MB）。按"窗口 = 并发度"逐窗处理，在途
    向量最多 EMBED_CONCURRENCY × EMBED_BATCH_SIZE 条，并发不变、峰值内存钉死。

    EMBED_BATCH_SIZE 语义不变：每批仍按该大小切，只是批与批之间不再排队。
    """
    if not chunks:
        return
    bs = max(1, config.EMBED_BATCH_SIZE)
    # 每批打包成 (文本, 元数据, id)，窗口内并发 embedding、窗口内按序写出
    batches = []
    for i in range(0, len(chunks), bs):
        part = chunks[i : i + bs]
        batches.append((
            [c.page_content for c in part],
            [c.metadata for c in part],
            [str(uuid.uuid4()) for _ in part],
        ))
    emb = vs._embedding_function
    workers = max(1, min(config.EMBED_CONCURRENCY, len(batches)))

    pool = ThreadPoolExecutor(max_workers=workers) if workers > 1 else None
    try:
        for w in range(0, len(batches), workers):
            window = batches[w : w + workers]
            if pool is None:
                # 单批或显式关并发：退回串行，行为与旧实现一致
                vectors = [emb.embed_documents(texts) for texts, _md, _ids in window]
            else:
                # 同一窗口内的各批并发发出；list() 会等齐并按提交顺序返回
                vectors = list(pool.map(lambda b: emb.embed_documents(b[0]), window))
            for (texts, metadatas, ids), vecs in zip(window, vectors):
                vs._collection.upsert(
                    ids=ids, embeddings=vecs, documents=texts, metadatas=metadatas
                )
    finally:
        if pool is not None:
            pool.shutdown()


class RagPipeline:
    """单条 RAG 链路。每个知识库一个实例，用不同 collection 名隔离向量库。"""

    def __init__(self, collection_name: str) -> None:
        self.collection_name = collection_name  # 如 kb_001 / kb_nba
        self._vs = None          # Chroma 实例缓存
        self._params = None      # 当前库对应的 (chunk_size, chunk_overlap)

    # ---------- 建库 ----------
    def _ensure_vs(self):
        """拿到可用的 Chroma 实例（不存在则创建，不删旧数据）。

        ⚠️ 必须传 client=chroma_client()。不传的话 langchain 会按 persist_directory
        自己 new 一个 PersistentClient —— 单例就白做了（实测 163.5ms vs 73.1ms，
        `client is 单例` 判 False），而且多个 client 指向同一个 sqlite 文件还有锁竞争隐患。
        传了 client 时 langchain 直接复用它，persist_directory 只作为元信息保留。

        写入路径（建库 / 追加）一律走这里；只想"打开已有库"的用 get_or_open()。
        """
        if self._vs is None:
            self._vs = Chroma(
                client=chroma_client(),
                persist_directory=config.PERSIST_DIR,
                embedding_function=get_embeddings(),
                collection_name=self.collection_name,
            )
        return self._vs

    def add_chunks(self, chunks) -> int:
        """把切片追加入库（库还不存在就先建），返回入库条数。

        联网导入必须走这里：它做的事是"往一个可能还是空的库里追加"，
        而 get_or_open() 只在磁盘上已有片段时才构造 Chroma 实例，
        空库拿到的是 None，add_documents 直接 AttributeError。
        更要命的是失败发生在建 collection 之前，所以重试多少次都一样。
        """
        if not chunks:
            return 0
        vs = self._ensure_vs()
        # 并发 embedding、串行写入，避免超出单次批量限制（百炼为 10）
        _embed_and_store(vs, chunks)
        _cache_invalidate(self.collection_name)
        return len(chunks)

    def build_index(
        self,
        chunk_size: int,
        chunk_overlap: int,
        paths,
        mode: str = "rebuild",
        source_names: list[str] | None = None,
    ) -> int:
        """切片并入库，返回本次新增的片段数。

        mode:
          rebuild —— 先删掉整个 collection 再重建（换切片参数时用，会重新付费 embedding）
          append  —— 追加到已有库，不动旧数据（日常上传走这条）
        """
        all_docs = []
        for i, p in enumerate(paths):
            name = None
            if source_names and i < len(source_names):
                name = source_names[i]
            all_docs.extend(load_document(p, source_name=name))
        chunks = split_documents(all_docs, chunk_size, chunk_overlap)
        if not chunks:
            raise ValueError("切片结果为空，请检查文档内容与切片参数")

        # 记录片段在其来源文件内的顺序：预览时按它还原正文
        counter: dict[str, int] = {}
        for c in chunks:
            src = c.metadata.get("source") or ""
            c.metadata["chunk_index"] = counter.get(src, 0)
            counter[src] = counter.get(src, 0) + 1

        if mode == "rebuild":
            # 只有换切片参数才需要全量重建：删旧 collection，保证参数变更后完全重建
            client = chroma_client()
            try:
                client.delete_collection(self.collection_name)
            except Exception:
                pass  # 首次不存在时忽略
            self._vs = None

        vs = self._ensure_vs()
        # 并发 embedding、串行写入，避免超出单次批量限制（百炼为 10）
        _embed_and_store(vs, chunks)

        self._params = (chunk_size, chunk_overlap)
        _cache_invalidate(self.collection_name)
        return len(chunks)

    def delete_by_source(self, source: str) -> int:
        """按来源文件名删除该文件的全部片段，返回删除条数。"""
        if self._vs is None:
            self.get_or_open()
        if self._vs is None:
            return 0
        try:
            got = self._vs._collection.get(where={"source": source})
        except Exception:
            return 0
        ids = got.get("ids") or []
        if not ids:
            return 0
        self._vs._collection.delete(ids=ids)
        _cache_invalidate(self.collection_name)
        return len(ids)

    def get_source_chunks(self, source: str) -> list[dict]:
        """按来源取该文件的全部片段（按页码与片段顺序排好）。

        用途：老库没有原始文件留档时，预览只能从向量库把片段拼回来。
        """
        if self._vs is None:
            self.get_or_open()
        if self._vs is None:
            return []
        try:
            got = self._vs._collection.get(
                where={"source": source}, include=["documents", "metadatas"]
            )
        except Exception:
            return []
        items: list[dict] = []
        for text, md in zip(got.get("documents") or [], got.get("metadatas") or []):
            md = md or {}
            items.append({
                "page": md.get("page"),
                "chunk_index": md.get("chunk_index"),
                "text": text or "",
            })
        items.sort(key=lambda x: (
            x["page"] if isinstance(x["page"], int) else 0,
            x["chunk_index"] if isinstance(x["chunk_index"], int) else 0,
        ))
        return items

    def list_sources(self) -> list[dict]:
        """统计库内每个来源文件的片段数（文件列表记录的兜底数据源）。

        Chroma 没有 distinct/GROUP BY，只能遍历元数据做聚合，无法真正免扫描；
        因此这里做两件事把代价压下来：
          1) 分页拉取（每页 SOURCES_PAGE 条）——峰值内存只跟页大小有关，
             不再一次性把整库 metadatas 物化进内存，大库不会瞬间吃掉几十 MB；
          2) 结果按库缓存——列表页轮询、_resolve_source 多次解析这类重复调用
             直接命中缓存，不再重复遍历；库内容变更时由 _cache_invalidate 清掉。
        """
        cached = _sources_cache_get(self.collection_name)
        if cached is not None:
            return cached
        if self._vs is None:
            self.get_or_open()
        if self._vs is None:
            return []

        counter: dict[str, int] = {}
        try:
            offset = 0
            while True:
                got = self._vs._collection.get(
                    include=["metadatas"], limit=SOURCES_PAGE, offset=offset
                )
                metas = got.get("metadatas") or []
                if not metas:
                    break
                for md in metas:
                    src = (md or {}).get("source") or "未知来源"
                    counter[src] = counter.get(src, 0) + 1
                if len(metas) < SOURCES_PAGE:
                    break
                offset += len(metas)
        except Exception:
            return []

        result = [
            {"name": k, "chunks": v, "added_at": None}
            for k, v in sorted(counter.items(), key=lambda x: -x[1])
        ]
        _sources_cache_set(self.collection_name, result)
        return result

    def count(self) -> int:
        """当前库内片段数。"""
        return self._vs._collection.count() if self._vs else 0

    def peek(self) -> int:
        """不建库的前提下查看磁盘上已有库的片段数。"""
        try:
            col = chroma_client().get_collection(self.collection_name)
            return col.count()
        except Exception:
            return 0  # 库不存在

    def delete(self) -> None:
        """彻底删除这个知识库（磁盘 + 内存缓存）。"""
        try:
            chroma_client().delete_collection(self.collection_name)
        except Exception:
            pass
        _cache_invalidate(self.collection_name)
        self._vs = None
        self._params = None

    def get_or_open(self) -> None:
        """打开已有库（磁盘存在则加载，否则 self._vs 保持 None）。

        只打开、不创建 —— exists() / peek() 这类"这个库到底建过没有"的判定靠的就是
        「空库时 _vs 仍为 None」这个语义，别顺手改成 _ensure_vs()。
        需要"没有就建"的写入路径请直接用 _ensure_vs() 或 add_chunks()。
        """
        if self.peek() > 0:
            self._vs = self._ensure_vs()

    # ---------- 检索 + 问答 ----------
    def _hybrid_candidates(self, question: str, vec_cands, keyword_top: int):
        """向量候选 + BM25 关键词候选，RRF 融合。

        返回 [(Document, distance_or_None, rank)]；无关键词命中或索引不可用
        （库太大 / 读取失败）时返回 None，让调用方退回纯向量。
        """
        built = _get_bm25(self._vs, self.collection_name)
        if built is None:
            return None
        kw_docs, index = built
        kw_pairs = index.top(question, min(keyword_top, len(kw_docs)))
        if not kw_pairs:
            return None
        return _rrf_merge(vec_cands, kw_pairs, kw_docs, config.HYBRID_RRF_K)

    def retrieve_with_score(self, question: str, top_k: int):
        """检索 top_k 条相关片段，返回 [(Document, distance), ...]。distance 越小越相关。

        不是直接取 top-k：先多捞几倍候选（默认 3 倍），丢掉明显不相关的，
        再用 MMR 去掉互相重复的，最后截到 top_k。详见 select_diverse。
        开启混合检索时，向量候选再并上 BM25 关键词候选并用 RRF 融合
        （专有名词 / 缩写 / 编号靠这条补召回）。详见 _rrf_merge。
        相同 (问题, top_k) 的检索结果按库缓存，命中时省掉一次 embedding + 向量查询。
        """
        cached = _cache_get(self.collection_name, question, top_k)
        if cached is not None:
            return cached

        if not self._vs:
            # 尝试从磁盘打开
            self.get_or_open()
        total = self.count()  # 只查一次（原来判空与算 fetch_k 各查一次）
        if total == 0:
            raise RuntimeError("知识库为空，请先上传文档构建索引")

        # 先算查询向量（带缓存）再按向量检索：结果缓存 miss 但向量命中时，
        # 省掉一次 embedding 网络请求。用 by_vector 而非 by_text，
        # 就是为了让我们自己控制这层缓存。
        vec = _embed_query(question)
        mult = config.RETRIEVE_FETCH_MULT
        if top_k <= 1 or mult <= 1:
            # 只要一条（或关掉了候选池）时，多捞没有意义
            result = self._vs.similarity_search_by_vector_with_relevance_scores(vec, k=top_k)
        else:
            fetch_k = min(max(top_k * mult, top_k + 6, config.RETRIEVE_MIN_FETCH), total)
            vec_cands = self._vs.similarity_search_by_vector_with_relevance_scores(vec, k=fetch_k)
            result = None
            if config.HYBRID_ENABLED:
                try:
                    merged = self._hybrid_candidates(
                        question, vec_cands, config.HYBRID_KEYWORD_TOP
                    )
                except Exception:
                    merged = None  # 关键词通道是增益不是必需：出错就退回纯向量
                if merged:
                    result = select_diverse(merged, top_k)
            if result is None:
                result = select_diverse(vec_cands, top_k)

        _cache_set(self.collection_name, question, top_k, result)
        return result

    @staticmethod
    def build_sources(docs_scores) -> list[dict]:
        """把检索结果整理成前端可展开的引用条目（含原文，供溯源查看）。"""
        out = []
        for i, (d, distance) in enumerate(docs_scores):
            src = os.path.basename(d.metadata.get("source", "?"))
            page = d.metadata.get("page")
            # 前端那个位置的标签是「相似度」，所以这里必须给真相似度（越大越相关），
            # 不能直接把距离丢过去。距离越界导致换算不了时给 None，前端会隐藏这一项。
            sim = _to_similarity(distance)
            out.append({
                "id": i + 1,
                "source": src,
                "page": (page + 1) if isinstance(page, int) else None,
                "score": round(sim, 4) if sim is not None else None,
                "preview": d.page_content.replace("\n", " ")[:80],
                "text": d.page_content[:1500],
            })
        return out

    @staticmethod
    def _format_refs(docs_scores) -> str:
        """参考片段格式化：来源文件名 + 页码 + 相似度 + 60 字预览。"""
        lines = []
        for i, (d, distance) in enumerate(docs_scores):
            src = os.path.basename(d.metadata.get("source", "?"))
            page = d.metadata.get("page")
            loc = f" 第{page + 1}页" if page is not None else ""
            preview = d.page_content.replace("\n", " ")[:60]
            # 同 build_sources：给相似度而不是距离；换算不了就整段省掉，避免显示错标签
            sim = _to_similarity(distance)
            head = f"**[{i + 1}]** {src}{loc}"
            if sim is not None:
                head += f" ｜ score={sim:.3f}"
            lines.append(f"{head} ｜ {preview}...")
        return "\n\n".join(lines) if lines else "（无检索结果）"

    def answer(self, question: str, top_k: int) -> tuple[str, str]:
        """同步问答：返回 (答案正文, 参考片段 Markdown)。"""
        docs_scores = self.retrieve_with_score(question, top_k)
        if not docs_scores:
            return "知识库中未检索到相关内容。", "（无检索结果）"
        refs_md = self._format_refs(docs_scores)

        references = "\n\n".join(
            f"[{i + 1}] （来源：{d.metadata.get('source', '?')}）\n{d.page_content}"
            for i, (d, _) in enumerate(docs_scores)
        )
        user_prompt = f"【参考资料】\n{references}\n\n【用户问题】\n{question}"

        response = get_llm().invoke(
            [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=user_prompt)]
        )
        return response.content, refs_md

    def answer_stream(self, question: str, top_k: int) -> None:
        """流式问答生成器：yield (累计答案文本, 参考片段Markdown)。

        第一个 yield 的 text 为空字符串（先展示检索来源），
        之后 LLM 逐 token 追加。
        """
        docs_scores = self.retrieve_with_score(question, top_k)
        if not docs_scores:
            yield "知识库中未检索到相关内容。", "（无检索结果）"
            return
        refs_md = self._format_refs(docs_scores)
        yield "", refs_md  # 先展示检索来源

        references = "\n\n".join(
            f"[{i + 1}] （来源：{d.metadata.get('source', '?')}）\n{d.page_content}"
            for i, (d, _) in enumerate(docs_scores)
        )
        user_prompt = f"【参考资料】\n{references}\n\n【用户问题】\n{question}"

        acc = ""
        for chunk in get_llm().stream(
            [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=user_prompt)]
        ):
            acc += chunk.content or ""
            yield acc, refs_md
