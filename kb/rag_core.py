# -*- coding: utf-8 -*-
"""RAG 核心逻辑（文档加载 → 切片 → Embedding → Chroma → 检索 → 提示词组装 → LLM 生成）

与界面完全分离，可被 Agent / FastAPI / 后续前端复用。
迁移自 rag-knowledge-base-demo/rag_core.py，适配 LangChain v1.x：
  - import config → from core import config
  - get_llm / get_embeddings → from core.llm import get_llm / get_embeddings
  - 其余 LangChain API（ChatOpenAI / OpenAIEmbeddings / Chroma / TextSplitter）
    在 v1.x 中签名不变，直接复用。
"""

import os

import chromadb
from langchain_chroma import Chroma
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_text_splitters import RecursiveCharacterTextSplitter

from core import config
from core.llm import get_embeddings, get_llm

SUPPORTED_EXT = (".txt", ".md", ".pdf")  # .md 按纯文本加载，Markdown 语法不影响切片

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

    def __init__(self, collection_name: str):
        self.collection_name = collection_name  # 如 kb_001 / kb_nba
        self._vs = None          # Chroma 实例缓存
        self._params = None      # 当前库对应的 (chunk_size, chunk_overlap)

    # ---------- 建库 ----------
    def _ensure_vs(self):
        """拿到可用的 Chroma 实例（不存在则创建，不删旧数据）。"""
        if self._vs is None:
            self._vs = Chroma(
                persist_directory=config.PERSIST_DIR,
                embedding_function=get_embeddings(),
                collection_name=self.collection_name,
            )
        return self._vs

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
            client = chromadb.PersistentClient(path=config.PERSIST_DIR)
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
            client = chromadb.PersistentClient(path=config.PERSIST_DIR)
            col = client.get_collection(self.collection_name)
            return col.count()
        except Exception:
            return 0  # 库不存在

    def delete(self) -> None:
        """彻底删除这个知识库（磁盘 + 内存缓存）。"""
        try:
            client = chromadb.PersistentClient(path=config.PERSIST_DIR)
            client.delete_collection(self.collection_name)
        except Exception:
            pass
        self._vs = None
        self._params = None

    def get_or_open(self) -> None:
        """打开已有库（磁盘存在则加载，否则 self._vs 保持 None）。"""
        if self.peek() > 0:
            self._vs = Chroma(
                persist_directory=config.PERSIST_DIR,
                embedding_function=get_embeddings(),
                collection_name=self.collection_name,
            )

    # ---------- 检索 + 问答 ----------
    def retrieve_with_score(self, question: str, top_k: int):
        """top-k 相似度检索，返回 [(Document, distance), ...]。distance 越小越相关。"""
        if not self._vs:
            # 尝试从磁盘打开
            self.get_or_open()
        if not self._vs or self.count() == 0:
            raise RuntimeError("知识库为空，请先上传文档构建索引")
        return self._vs.similarity_search_with_score(question, k=top_k)

    @staticmethod
    def build_sources(docs_scores) -> list[dict]:
        """把检索结果整理成前端可展开的引用条目（含原文，供溯源查看）。"""
        out = []
        for i, (d, score) in enumerate(docs_scores):
            src = os.path.basename(d.metadata.get("source", "?"))
            page = d.metadata.get("page")
            out.append({
                "id": i + 1,
                "source": src,
                "page": (page + 1) if isinstance(page, int) else None,
                "score": round(float(score), 4),
                "preview": d.page_content.replace("\n", " ")[:80],
                "text": d.page_content[:1500],
            })
        return out

    @staticmethod
    def _format_refs(docs_scores) -> str:
        """参考片段格式化：来源文件名 + 页码 + 相似度 + 60 字预览。"""
        lines = []
        for i, (d, score) in enumerate(docs_scores):
            src = os.path.basename(d.metadata.get("source", "?"))
            page = d.metadata.get("page")
            loc = f" 第{page + 1}页" if page is not None else ""
            preview = d.page_content.replace("\n", " ")[:60]
            lines.append(f"**[{i + 1}]** {src}{loc} ｜ score={score:.3f} ｜ {preview}...")
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

    def answer_stream(self, question: str, top_k: int):
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
