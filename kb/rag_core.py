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

import os
import re
import threading

import chromadb
from langchain_chroma import Chroma
from langchain_community.document_loaders import PyPDFLoader, TextLoader
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


def select_diverse(docs_scores, top_k: int, lambda_mult: float | None = None) -> list:
    """从候选里挑出 top_k 条：先丢明显不相关的，再按 MMR 去掉互相重复的。

    原来直接取 top-k 有两个毛病：相似度再低的片段也照样进 prompt（模型被迫硬答），
    以及相邻切片的重叠部分会把名额占满（三条里两条是同一段话）。

    Args:
        docs_scores: [(Document, distance), ...]，distance 越小越相关
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

    # ---- 1. 离群过滤：把明显不相关的尾巴砍掉 ----
    min_sim = config.RETRIEVE_MIN_SIM
    if min_sim > 0:
        scored = [(c, _to_similarity(c[1])) for c in cands]
        # 只有在距离能换算成余弦时才过滤；换不了就整批保留
        if all(s is not None for _, s in scored):
            kept = [c for c, s in scored if s >= min_sim]
            # 别把结果砍空：一条不剩时至少留最相关的那条，让模型自己说"资料里没有"
            cands = kept or [min(cands, key=lambda c: c[1])]

    # 候选还不够挑，MMR 没意义（离群过滤已经在上面做过了）
    if len(cands) <= top_k:
        return cands

    # ---- 2. MMR 贪心：相关度高、且和已选内容重复度低的优先 ----
    scores = [s for _, s in cands]
    lo, hi = min(scores), max(scores)
    span = hi - lo

    def relevance(d: float) -> float:
        """相关度（0~1，越大越相关）。

        优先用绝对余弦相似度，而不是批次内 min-max 归一化——
        被重叠切片塞满时，几条候选的距离往往只差 0.0x，归一化会把这点差距放大成
        "最相关 vs 最不相关"，结果重复片段靠微弱优势把名额全占了。
        """
        sim = _to_similarity(d)
        if sim is not None:
            return sim
        # 距离换不成余弦（向量未归一化）时，退回批次内相对排序
        return 1.0 if span < 1e-9 else 1 - (d - lo) / span

    shingles = [_shingles(d.page_content) for d, _ in cands]
    order: list[int] = []
    remaining = list(range(len(cands)))
    # 第一条固定取最相关的，给后面的多样性比较一个基准
    first = min(remaining, key=lambda i: scores[i])
    order.append(first)
    remaining.remove(first)

    while remaining and len(order) < top_k:
        best_i, best_val = -1, -1e9
        for i in remaining:
            rel = relevance(scores[i])
            dup = max(_overlap(shingles[i], shingles[j]) for j in order)
            val = lam * rel - (1 - lam) * dup
            if val > best_val:
                best_i, best_val = i, val
        order.append(best_i)
        remaining.remove(best_i)

    return [cands[i] for i in order]


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
        # 分批入库，避免超出 Embedding 接口单次批量限制（百炼为 10）
        for i in range(0, len(chunks), config.EMBED_BATCH_SIZE):
            vs.add_documents(chunks[i : i + config.EMBED_BATCH_SIZE])
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
        # 分批入库，避免超出 Embedding 接口单次批量限制（百炼为 10）
        for i in range(0, len(chunks), config.EMBED_BATCH_SIZE):
            vs.add_documents(chunks[i : i + config.EMBED_BATCH_SIZE])

        self._params = (chunk_size, chunk_overlap)
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
        """统计库内每个来源文件的片段数（文件列表记录的兜底数据源）。"""
        if self._vs is None:
            self.get_or_open()
        if self._vs is None:
            return []
        try:
            got = self._vs._collection.get(include=["metadatas"])
        except Exception:
            return []
        counter: dict[str, int] = {}
        for md in got.get("metadatas") or []:
            src = (md or {}).get("source") or "未知来源"
            counter[src] = counter.get(src, 0) + 1
        return [
            {"name": k, "chunks": v, "added_at": None}
            for k, v in sorted(counter.items(), key=lambda x: -x[1])
        ]

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
    def retrieve_with_score(self, question: str, top_k: int):
        """检索 top_k 条相关片段，返回 [(Document, distance), ...]。distance 越小越相关。

        不是直接取 top-k：先多捞几倍候选（默认 3 倍），丢掉明显不相关的，
        再用 MMR 去掉互相重复的，最后截到 top_k。详见 select_diverse。
        """
        if not self._vs:
            # 尝试从磁盘打开
            self.get_or_open()
        if not self._vs or self.count() == 0:
            raise RuntimeError("知识库为空，请先上传文档构建索引")

        mult = config.RETRIEVE_FETCH_MULT
        if top_k <= 1 or mult <= 1:
            # 只要一条（或关掉了候选池）时，多捞没有意义
            return self._vs.similarity_search_with_score(question, k=top_k)

        total = self.count()
        fetch_k = max(top_k * mult, top_k + 6, config.RETRIEVE_MIN_FETCH)
        cands = self._vs.similarity_search_with_score(question, k=min(fetch_k, total))
        return select_diverse(cands, top_k)

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
