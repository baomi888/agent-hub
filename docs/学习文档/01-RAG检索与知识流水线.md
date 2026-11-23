# 01 · RAG 检索与知识流水线

> **读者画像**：能读 Java / Python，知道“RAG = 先检索再让模型回答”，但没写过 LangChain。
> **目标**：不是背架构，而是能把 `kb/rag_core.py`、`kb/pipelines.py`、`core/embed_guard.py`、`api/kb.py`、`api/chat.py` 里与 RAG 有关的代码读懂，并且能自己挑出毛病。
> **代码基线**：当前工作区（2026-11-02）。行号会随改动漂移，读的时候以函数名为锚，行号只作定位参考。
> **范围**：模块 5（知识与检索）的入库流水线 + 模块 1（RAG 检索）的双通道召回、精排、依据判定。两块共用同一份 `kb/rag_core.py`，所以合并成一份文档，分两大部分写。

---

## 0. 先看四张图：只有第三张属于这里

判断依据不是图的名字，而是**图上每个组件能否在代码里找到真实落点**。

| 图 | 图上的内容 | 代码落点 | 是否本文范围 |
|---|---|---|---|
| 图 1 | 工具元数据校验、激活状态、`needs_approval`、`prompt_guard`、`netsafe` / `urltools`、参数校验 | `core/tool_registry.py`、`core/prompt_guard`、`core/netsafe.py`、`core/urltools.py` | 否，属于工具与安全 |
| 图 2 | 落库与副作用（`add_message` / 标题 / 画像标签）、压缩与长期记忆（`context_compress` / salience / 抽取式摘要 / 安全网 / `commit_summary`） | `api/chat.py` 收尾段、`core/context_compress.py`、`core/session.py` | 否，属于对话与上下文 |
| 图 3 | 解析与切片、向量化、写入向量库、对外可见 | `kb/rag_core.py`、`kb/pipelines.py`、`core/embed_guard.py`、`api/kb.py` | **是，本文核心** |
| 图 4 | systemd 进程编排、安全加固、备份恢复 | 部署脚本 / 运维文档 | 否，属于部署运维 |

所以下面只对**图 3** 的四个圈点逐格回应。图 1 / 2 / 4 的疑问不在本文展开，否则会把“学 RAG”变成“学全项目”。

---

## 0.1 图 3 的四个圈点：这一段为什么这么写

### A. 解析与切片

图上写了四格：`文本抽取` → `噪声清洗（页眉页脚/空行）` → `切片（500 字，重叠 50）` → `元数据（topic / 文件名 / 序号）`。

**这一段为什么这么写：**

- **文本抽取**：PDF 是二进制容器，模型和向量化接口都只吃文本，所以第一步必须把“页”变成字符串。代码用 LangChain 的 `PyPDFLoader`；TXT / Markdown 用 `TextLoader`，并且先试 `utf-8`、失败再试 `gbk`——这是 Windows 记事本用户的兜底（`kb/rag_core.py#L784-L788`）。PDF 解析失败时不是抛一句 `500`，而是明确提示“可能是扫描件，需要先 OCR”（`#L790-L797`）。这一步不可省：没有它，后面所有步骤都没有输入。
- **噪声清洗**：**代码里没有独立的清洗函数。** 图上这一格是设计意图，不是已实现能力。实际只做了两件事：加载器把 PDF 按页拆开；切分器把空行当作分隔符（`separators` 的第一项就是 `"\n\n"`）。页眉页脚不会被删掉，空行也不会被删除，只是被当作“优先在这里切”的信号。为什么不写通用清洗：页眉页脚没有统一格式，按规则删很容易把正文里的标题、表格标题一起删掉；而这个项目的资料以课程笔记、复习资料为主，页眉污染对检索的影响小于误删正文。代价是：如果某本 PDF 每页都有大段页眉，切片里会反复出现同一句噪声，BM25 可能把它当成高频词——这是已知但可接受的损失。
- **切片 500 / 重叠 50**：500 是字符数，不是 token 数（`length_function=len`，`kb/rag_core.py#L941-L946`）。500 字大约是一段话到两段话，既能装下一个完整论点，又不至于让一个向量“什么都沾一点”。重叠 50 是为了不让一个定义被切断在边界上：上一片结尾的 50 字会出现在下一片开头，检索时无论命中哪一片，都能读到完整句子。没有切片会怎样：整本 30 万字的 PDF 变成一个向量，语义被平均掉，问“极限的 ε-δ 定义”会命中整本书，但答不出具体定义；而且 embedding 接口对单条文本有长度上限，整本根本发不出去。
- **元数据**：每个切片挂 `source`（展示用文件名）、`source_path`（原始文件路径）、`page`（PDF 页码）、`chunk_index`（同一来源内从 0 递增）、`topics`（`|t1|t2|`）。`chunk_index` 是为了预览时按原顺序拼回正文；`source` 而不是 `source_path` 作为删除键，是因为临时文件会被清掉，路径不稳定（`#L775-L776`）；`topics` 首尾加 `|` 是为了精确成员匹配，`"|数学|"` 不会误中 `"|数学分析|"`（`#L883-L889`）。没有元数据会怎样：引用卡片只能显示“某段”，无法定位到文件和页码；删除文件只能删整库；画像偏置也没有主题可匹配。

### B. 向量化

图上写了三格：`批量 embedding（batch 10 / 并发 4）` → `指纹守卫（模型 / 维度 / host 一致）` → `日配额（1000 片段/日）`。

**这一段为什么这么写：**

- **batch 10**：阿里百炼 `text-embedding-v3` 单次请求最多 10 条，这是接口硬限制，不是拍脑袋（`core/config.py#L48-L49`）。`EMBED_BATCH_SIZE=10` 正好卡在上限；调到 50 不会更快，只会收到 `400` 或 `429`。
- **并发 4**：入库的真正瓶颈是网络往返。10 条一批，1000 条就是 100 个 RTT；串行等就是 100 个往返叠加。代码把“窗口”内的 4 批同时发出去，等回来再串行写库（`kb/rag_core.py#L1005-L1021`）。为什么不是全并发：一次把 100 批全发出去，要把几千个向量同时留在内存里；按“窗口 = 并发度”处理，峰值内存被钉死在 `4 × 10` 条向量的量级。为什么写入不并发：多个线程同时写同一个 Chroma collection 会竞争 HNSW 索引和底层 SQLite，结果不确定；embedding 是纯网络调用，才可以并发。
- **指纹守卫**：只换 `EMBEDDING_MODEL` 不会报错，但旧库里的向量和新查询向量不在同一个语义空间，余弦照样算得出来，只是排序接近随机——这是最典型的“静默变坏”。所以入库完成后把 `model + base host + dim` 写进 collection metadata（`core/embed_guard.py#L79-L87`、`#L123-L154`），检索最前面校验（`#L157-L189`）。`base` 只取 host 不取完整 URL，是因为同一个服务换路径前缀（`/compatible-mode/v1` vs `/v1`）仍是同一批模型，算进去会造成假警报（`#L65-L76`）。`dim` 只在两边都有值时比较，因为老库可能没记维度。
- **日配额 1000**：embedding 按 token 计费，一次建库几千片就是真实账单。入口先 `quota.guard(uid, "embed")` 只检查“还没超”，等真正切完再用 `quota.bump_usage(uid, "embed", total)` 记**真实切片数**（`api/kb.py#L64-L67`、`#L180-L183`）。为什么分两步：切之前不知道会切出多少片，只能先卡一道“今天还有额度”；切完才知道实际数量，再按实际记账。只检查不记账，等于每次都能再进来一次。

### C. 写入向量库

图上写了四格：`collection（kb_{kb_id}）` → `upsert（id + 文本 + 序号）` → `文件清单（原子写 / 文件早删）` → `重建（删旧片段后重写）`。

**这一段为什么这么写：**

- **一个库一个 collection**：名字是 `kb_{kb_id}`（`kb/pipelines.py#L315-L323`），`kb_id = md5(f"{owner}|{name}")[:12]`，owner 混进哈希（`#L286-L293`）。不混 owner 的话，两个人各建一个“我的库”会算出同一个 id，后一个直接写进前一个的 collection。为什么不把所有库放一个 collection 再用 `where` 过滤：Chroma 支持过滤，但“删整个库”“按库建 BM25 索引”“按库做缓存”“按库做隔离”都要额外写条件；一库一 collection 让这些操作变成天然边界。代价是 collection 数量会随用户增长，启动 `warmup` 要扫一遍（`#L586-L605`）。
- **upsert 而不是 add**：每条切片在入库前生成 `uuid4` 作为 id（`kb/rag_core.py#L1001`）。`upsert` 的语义是“有则覆盖、无则插入”。为什么不用“文本哈希”做 id：文本哈希能天然去重，但也会把两份文件里合法的相同段落合并成一条，按文件删除时会少删/多删；uuid 允许重复内容各自存在，删除时按 `metadata["source"]` 精确清理（`#L1123-L1138`）。
- **文件清单原子写**：`kb_files.json` 记录每个库有哪些文件、多少片、什么时候进来、来源是 upload 还是 web。写入用“同目录临时文件 + `fsync` + `os.replace`”（`kb/pipelines.py#L107-L130`）。直接 `open(path, "w")` 写到一半被杀，会留下截断的 JSON，下次启动整个清单读不出来——片段还在 Chroma，界面却成了空壳。为什么不用 SQLite：这份数据很小、读多写少、要和 `data/` 目录一起搬走；但代价是每次写都要“重读磁盘 → 改 → 整份覆盖”，所以 `add_file_records` 必须先 `_load_files()` 再改，否则会把别的进程刚写进去的记录冲掉（`#L490-L502`）。
- **删除单个文件要有顺序**：先按 `metadata["source"]` 删切片，再删 `kb_files.json` 记录，最后删原始文件（`api/kb.py#L306-L317`）。顺序反了，一旦删切片失败，清单里已经没有这个文件，片段却还在库里——用户看不见，也没法再按文件名删，只能整库重建。
- **重建先删旧片段**：换切片参数后，旧片段的粒度和新片段不一致，混在一起会让检索同时命中两种粒度。所以 `mode="rebuild"` 先 `delete_collection`，再重新 `_embed_and_store`（`kb/rag_core.py#L1103-L1117`）。这里的风险要如实说：删除发生在 embedding 之前，如果 embedding 中途失败（429 / 断网），旧数据已经没了，库会变成空库或半成品。代码没有做“先写新 collection 再原子切换”，这是已知取舍，不是没考虑到。

### D. 对外可见

图上写了三格：`状态流转（processing → ready / failed）` → `检索可达（kb_search 立即可用）` → `引用溯源（来源文件 + 片段序号）`。

**这一段为什么这么写：**

- **状态流转**：**代码里没有 `processing / ready / failed` 状态机。** 上传接口是同步阻塞的：请求进来，落盘、切片、embedding、写库全部跑完才返回 JSON（`api/kb.py#L139-L183`）。`pipelines.get_status` 只返回 `kb_id / chunks / chunk_size / chunk_overlap / files`，没有状态字段（`kb/pipelines.py#L564-L580`）。所以图上这一格是“未来想要的交互”，不是现状。现状的代价：上传 100 页 PDF 时，前端只能等；没有进度条，也没有“失败后重试”的任务 id。好处是实现简单，不会出现“任务排队中但进程重启后永远 processing”的僵尸状态。
- **检索可达**：`get_or_open()` 只在 `peek() > 0` 时才真正打开 collection（`kb/rag_core.py#L1283-L1291`），空库不会创建。上传成功后，collection 里已经有向量，下一次检索就能命中；没有额外的“发布”步骤。`warmup` 在启动时扫描所有 `kb_*` collection 并注册进内存，但它不验归属，真正的门禁在每次访问时的 `owner_of` / `_must_own`。
- **引用溯源**：检索返回的每条结果是 `(Document, distance)`，`build_sources` 把它整理成 `id / source / page / score / strength / preview / text`（`kb/rag_core.py#L1400-L1421`）。`id` 就是回答里 `[1][2]` 的编号，顺序与检索排名一致；`score` 是**相似度**不是距离（`_to_similarity` 换算），`strength` 是 `high / medium / low`。没有这层会怎样：模型即使写了 `[1]`，前端也无法把 `[1]` 映射回具体文件和页码，用户没法核对，引用就变成装饰。

### 0.2 两处“图上写了、代码没有”的事实

| 图上 | 代码现状 | 结论 |
|---|---|---|
| 噪声清洗（页眉页脚/空行） | 没有独立函数；只有 loader 解析 + splitter 的 `separators` | 设计意图，未实现通用清洗 |
| 状态流转 `processing → ready/failed` | 上传同步阻塞，`get_status` 无状态字段 | 图上与代码不一致，文档/答辩时不要声称有状态机 |

这两条不是“挑刺”，而是读代码时最容易先入为主的地方：看到图上有，就以为代码里有。学习时先把图和代码对齐，后面才不会把不存在的函数当成理解前提。

---

# 第一部分 模块 5：知识入库流水线

## 1.1 先建立直觉

场景：学生小陈上传了一份 100 页的《考研数学复习全书》PDF，想在聊天里问“极限的 ε-δ 定义怎么理解”。

**如果不做入库，会发生什么：**

1. **不解析**：PDF 是二进制，模型看不见。你只能把文件路径给它，它打不开。
2. **不切片**：把整本书塞进一次请求，30 万字远超上下文窗口；就算塞得进去，embedding 也会把整本书压成一个向量，“极限定义”和“多元微积分”被平均成同一团语义，检索命中率接近随机。
3. **不向量化**：只能做关键词匹配。用户问“高数里的极限”，资料里写的是“微积分基础”，字面一个词都不重合，匹配不到。
4. **不写库**：每次提问都要重新读文件、重新算向量，慢且贵；也没法删除单个文件、没法引用到页码。

四段流水线解决的就是这四件事：

```text
上传 → 解析（PDF/TXT → 文本）
     → 切片（长文本 → 500 字左右的小块）
     → 向量化（小块 → 数字向量）
     → 写库（向量 + 元数据 → Chroma，可查/可删/可重建）
```

每一段都在缩小“不可检索”的范围。下面按代码走。

---

## 1.2 LangChain 零基础补丁

先把本模块出现的 LangChain 概念逐个说清。**不是 API 手册**，只讲“它替我们做了什么、本项目用不用、为什么”。

| 概念 | 一句白话 | 本项目用法 | 位置 | 替我们做了什么 | 用了吗 |
|---|---|---|---|---|---|
| `Document` | 一张“纸”：正文 + 标签字典 | `load_document` 返回 `list[Document]`；BM25 也手工造 `Document` | `kb/rag_core.py#L26`、`#L256-L259` | 把“文本”和“来源/页码/序号”绑在一起，后面所有函数都只搬这个对象 | 是 |
| `TextLoader` / `PyPDFLoader` | 把文件读成 `Document` 的加载器 | TXT/MD 用 `TextLoader`，PDF 用 `PyPDFLoader` | `#L784-L797` | 处理编码、PDF 分页，省掉自己写解析器 | 是 |
| `RecursiveCharacterTextSplitter` | 按“段落 → 句子 → 字符”的顺序递归切分 | `_splitter()` 按 `(chunk_size, chunk_overlap)` 缓存 | `#L931-L948` | 尽量在自然边界切，切不动才按字符硬切 | 是 |
| `Embeddings` / `OpenAIEmbeddings` | 把文本变成向量的客户端 | 百炼 `text-embedding-v3`，OpenAI 兼容 | `core/llm.py#L69-L87` | 封装 HTTP 请求、批处理、重试、超时 | 是 |
| `Chroma` | 向量数据库的 LangChain 壳 | `Chroma(client=..., collection_name="kb_xxx")` | `kb/rag_core.py#L1044-L1051` | 建集合、写入、按向量查询、按 metadata 删除 | 是 |
| `ChatOpenAI` | 聊天模型客户端 | 抽主题标签、生成答案 | `core/llm.py#L53-L61`；调用点 `rag_core.py#L833`、`#L1461` | 封装 DeepSeek 的 OpenAI 兼容接口 | 是 |
| `Retriever` | “给它一个问题，还你一组 Document”的接口 | **完全没用**；项目自己写 `retrieve_with_score` | `kb/rag_core.py#L1309-L1366` | 框架的检索器会把“召回 + 过滤 + 排序”收进一个黑盒 | 否 |
| `Runnable` / `Chain`（LCEL） | 把多个步骤用 `|` 串成管道 | **完全没用**；项目手写每一步 | `retrieve_with_score` / `select_diverse` | 组合 prompt、模型、解析器，自动传递数据 | 否 |
| `Tool` | 给 Agent 调用的函数包装 | 本模块**不用**；工具在 `core/tool_registry.py` 那套 | — | 让模型决定“调哪个函数、传什么参数” | 否 |

### 如果改用框架写法，会长什么样

入库最“LangChain”的写法是：

```python
from langchain_chroma import Chroma
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings

docs = PyPDFLoader(path).load()
chunks = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50).split_documents(docs)
vs = Chroma.from_documents(
    documents=chunks,
    embedding=OpenAIEmbeddings(model="text-embedding-v3", base_url=...),
    collection_name=f"kb_{kb_id}",
    persist_directory=...,
)
```

这段能跑，但会丢掉本项目需要的四样东西：

1. **批量控制**：`from_documents` 默认按内部批次发，遇到百炼“单次最多 10 条”会直接失败；项目的 `_embed_and_store` 是自己按 10 条切、4 批并发。
2. **指纹**：`from_documents` 不会记“这批向量是哪个模型算的”；项目在 `embed_guard.stamp` 里写 `model / base / dim`。
3. **文件清单**：`from_documents` 不知道“这个库有哪些文件、每文件多少片”；项目的 `kb_files.json` 负责这件事。
4. **可重入的 id**：`from_documents` 让 Chroma 自己生成 id，重复上传会叠加；项目用 `uuid4` + `upsert`，并且按 `source` 删除。

所以这里不是“为了不用框架而不用”，而是**框架的默认路径不满足四个硬约束**。这也是后面模块 1 不用 `Retriever` 的同一个原因：一旦把召回、过滤、排序收进黑盒，就拿不到 `distance`、拿不到 `distance=None`、也没法在中间插缓存和画像偏置。

---

## 1.3 核心代码逐段吃透

### 1.3.1 上传入口：先卡额度，再动磁盘

`api/kb.py#L64-L137`

```python
_must_own(uid, kb_id)
await asyncio.to_thread(quota.guard, uid, "embed")
if not files:
    raise HTTPException(status_code=400, detail="未上传任何文件")
...
for f in files:
    ext = os.path.splitext(f.filename or "")[1].lower()
    if ext not in (".txt", ".md", ".pdf"):
        raise HTTPException(status_code=400, detail=f"不支持的文件格式：{ext}")
    upload.check_declared_size(f, limit)

try:
    for f in files:
        name = pipelines.safe_source_name(f.filename or "", fallback_ext=ext)
        dest = pipelines.source_path(kb_id, name)
        await upload.save_stream(f, dest, limit)
        saved.append((dest, name))
except Exception:
    upload.cleanup(p for p, _ in saved)
    raise
```

**这段在干什么**：先验归属，再卡“今天还能不能向量化”，再验文件类型和大小；全部通过后才真正写盘。写盘中途失败会把已经落的文件清掉。

**为什么这样写**：注释里写得很直白——超限时一个字节都不该落盘。更隐蔽的是“两趟循环”：如果校验和写盘混在一个 `for` 里，第 3 个文件格式不对时，前两个已经躺在磁盘上，既没登记进 `kb_files.json`，也没进 Chroma，只有删整个库才清得掉。分成“先全验、再全写”就不会产生这种孤儿文件。

**改成另一种写法会怎样**：把 `quota.guard` 放到写盘之后——用户超额度时，磁盘已经被写满，然后才拒绝；每次重试都重复写一遍。把两趟合并成一趟——遇到坏文件就留下前几个孤儿文件，`list_sources` 看不到它们，但磁盘一直涨。

### 1.3.2 解析：编码兜底与 PDF 的 OCR 提示

`kb/rag_core.py#L769-L808`

```python
if ext in (".txt", ".md"):
    try:
        docs = TextLoader(file_path, encoding="utf-8").load()
    except UnicodeDecodeError:
        docs = TextLoader(file_path, encoding="gbk").load()
else:
    try:
        docs = PyPDFLoader(file_path).load()
    except Exception as e:
        raise ValueError(
            f"PDF 解析失败：{e}。可能原因：① 扫描版 PDF（无文字层）需先 OCR；"
            f"② 文件加密或损坏；③ 文件过大。建议转换为 TXT/Markdown 后再上传。"
        ) from e
...
for d in docs:
    d.metadata["source_path"] = file_path
    if source_name:
        d.metadata["source"] = source_name
```

**这段在干什么**：把文件读成 `Document`，统一来源字段；TXT 先 UTF-8 再 GBK，PDF 失败给可操作的错误。

**为什么这样写**：国内用户大量 TXT 是 Windows 记事本存的 GBK，不兜底就会在 `TextLoader` 里抛 `UnicodeDecodeError`，用户只看到 500。PDF 的扫描件没有文字层，`PyPDFLoader` 可能“成功返回空文本”或直接报错；这里既捕获异常，又在后面检查“所有页都是空”并给出 OCR 提示。`source_path` 和 `source` 分开：路径用于重建时回读原始文件，文件名用于展示和删除。

**改成另一种写法会怎样**：只试 UTF-8——GBK 文件直接失败；只用 `source_path` 当展示名——临时文件被清掉后，引用卡片显示一串 `/tmp/...`，按文件名删除也找不到。PDF 不检查空文本——扫描件会“成功入库 0 片”，用户以为传上去了，检索永远为空。

### 1.3.3 切片与序号

`kb/rag_core.py#L931-L948`、`#L1086-L1101`

```python
got = RecursiveCharacterTextSplitter(
    chunk_size=chunk_size,
    chunk_overlap=chunk_overlap,
    separators=["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""],
    length_function=len,
)
```

```python
all_docs = []
for i, p in enumerate(paths):
    name = source_names[i] if source_names and i < len(source_names) else None
    all_docs.extend(load_document(p, source_name=name))
chunks = split_documents(all_docs, chunk_size, chunk_overlap)
...
counter: dict[str, int] = {}
for c in chunks:
    src = c.metadata.get("source") or ""
    c.metadata["chunk_index"] = counter.get(src, 0)
    counter[src] = counter.get(src, 0) + 1
```

**这段在干什么**：按“空行 → 换行 → 中文句号/感叹号/问号/分号/逗号 → 空格 → 字符”的顺序递归切；切完给每个切片标上“在本文件里是第几片”。

**为什么这样写**：中文没有空格，先按语义边界切，能保证一个定义、一个步骤尽量落在同一片；实在切不动才按字符硬切。`length_function=len` 让“500”是 500 个字符，不是 500 个 token——这样切片大小可预测，不依赖分词器。`chunk_index` 按 `source` 单独计数，而不是全局计数，因为预览时要“按文件还原顺序”，全局序号会把两个文件混在一起。

**改成另一种写法会怎样**：只按固定字符切——句子被拦腰截断，“ε-δ 定义”可能前半句在这片、后半句在那片，检索命中后模型看到半句。不做 `chunk_index`——`get_source_chunks` 只能按页码或插入顺序拼，同一页内顺序会乱，预览像被洗牌。

### 1.3.4 主题标签：每个来源一次 LLM，失败静默

`kb/rag_core.py#L819-L861`、`#L883-L907`

```python
for _src, group in by_source.items():
    text = _join_head(group)          # 只取该来源前 4000 字
    tags = _extract_topics_one(text)
    if not tags:
        continue
    tstr = "|" + "|".join(tags) + "|"
    for c in group:
        c.metadata["topics"] = tstr
```

```python
llm = get_llm(temperature=0, max_tokens=64)
prompt = ("...提取 3~6 个简短中文标签...只输出一个 JSON 数组...")
resp = llm.invoke([HumanMessage(content=prompt)])
raw = (resp.content or "").strip()
...
tags = _json.loads(raw)
```

**这段在干什么**：对每个来源文件调用一次 LLM，从“全文前 4000 字”抽 3~6 个主题标签，然后把这个标签串挂到该来源的**所有**切片上。

**为什么这样写**：主题标签是给“画像偏置”用的：用户画像里有“考研数学”，文档 topics 里有“考研数学”，就减一点距离。按来源抽一次而不是按切片抽，是因为切片太多，每片一次 LLM 会让入库费用和耗时爆炸；同一个文件主题通常一致，抽一次够用。`max_tokens=64` 是因为只要一个短 JSON 数组，给大值会让模型有机会“解释”。失败静默是因为标签是锦上添花，不能因为抽标签失败就让整个入库 500。

**改成另一种写法会怎样**：每个切片调一次 LLM——1000 片就是 1000 次调用，入库从几十秒变成几十分钟，费用也上去。抽标签失败就抛异常——没配 key 的机器上建库直接失败；代码注释里专门记了这个历史 bug（`get_llm()` 必须在 `try` 之内，否则构造时就抛 `Missing credentials`）。标签不首尾加 `|`——`"|数学|"` 会误中 `"|数学分析|"`，偏置会把“数学分析”的片段当成“数学”的片段。

### 1.3.5 向量化：窗口并发 + 串行 upsert

`kb/rag_core.py#L967-L1022`

```python
_inject_topics(chunks)
bs = max(1, config.EMBED_BATCH_SIZE)
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
pool = _embed_pool() if workers > 1 else None
dim = None
for w in range(0, len(batches), workers):
    window = batches[w : w + workers]
    if pool is None:
        vectors = [emb.embed_documents(texts) for texts, _md, _ids in window]
    else:
        vectors = list(pool.map(lambda b: emb.embed_documents(b[0]), window))
    for (texts, metadatas, ids), vecs in zip(window, vectors, strict=False):
        if dim is None and vecs and vecs[0]:
            dim = len(vecs[0])
        vs._collection.upsert(ids=ids, embeddings=vecs, documents=texts, metadatas=metadatas)
return dim
```

**这段在干什么**：把切片按 10 条一批打包，每批带好文本、元数据、uuid；按“窗口 = 并发度”把窗口内的批同时发出去；等向量回来，按提交顺序串行 `upsert`。返回真实向量维度。

**为什么这样写**：`list(pool.map(...))` 会等齐窗口内所有批，并按提交顺序返回——所以“并发”只发生在网络请求，写入顺序仍然是确定的。`upsert` 串行是为了避开 HNSW/SQLite 的写竞争（注释 `#L978-L980`）。`dim` 取自 `len(vecs[0])` 而不是模型文档，是因为“文档写 1024 维、接口实际返回别的维度”真的会发生；指纹记错会让校验形同虚设。

**改成另一种写法会怎样**：所有批一次性 `pool.map` 发出去——快，但峰值内存是“全部向量”，几千片 × 1024 维 × 4 字节就是几十 MB，大库会 OOM。写入也并发——Chroma 的 HNSW 索引可能损坏或丢写，而且错误是随机的，最难查。用 `add` 而不是 `upsert`——同一个 uuid 不存在时没问题，但重建/重试时会产生重复条目。

### 1.3.6 指纹：换模型必须能被发现

`core/embed_guard.py#L79-L104`、`#L157-L189`

```python
fp = {
    "model": str(getattr(config, "EMBEDDING_MODEL", "") or ""),
    "base": _host(str(getattr(config, "EMBEDDING_BASE_URL", "") or "")),
}
if dim:
    fp["dim"] = int(dim)

def _same_space(a, b):
    if (a.get("model") or "") != (b.get("model") or ""):
        return False
    if (a.get("base") or "") != (b.get("base") or ""):
        return False
    da, db = a.get("dim"), b.get("dim")
    if da and db and int(da) != int(db):
        return False
    return True
```

```python
if not _enabled():
    return
with _LOCK:
    if collection in _VERIFIED:
        return
stored = _read(collection)
current = fingerprint()
if stored is None:
    stamp(collection, None)
    _VERIFIED.add(collection)
    return
if not _same_space(stored, current):
    metrics.inc("embed_guard_mismatch", {"kb": collection})
    raise EmbeddingMismatch(collection, stored, current)
_VERIFIED.add(collection)
```

**这段在干什么**：入库后把“这批向量是哪个模型、哪个 host、多少维”写进 collection metadata；检索前读出来和当前配置比，不一致就抛 `EmbeddingMismatch`。

**为什么这样写**：换模型不会报错，只会让排序变随机。`model` 和 `base` 必须都比：同名模型在不同供应商那里可能是不同的东西，只比 model 会漏掉“换了供应商但名字没换”的情况。`base` 只取 host，避免路径前缀变化造成假警报。`dim` 只在两边都有值时比，因为老库可能没记维度。`_VERIFIED` 是进程内缓存：检索在热路径上，不能每次读 metadata。老库没有指纹时放行并补写——这是刻意的乐观，否则升级瞬间所有现网库全部不可用；代价是“如果老库本来就不一致，会被这次补写认证成一致，之后再也查不出来”。

**改成另一种写法会怎样**：检测到不一致就自动全量重建——会触发一次未预期的批量 embedding 计费，而且重建要删旧 collection，中途失败库就没了。只比 `model` 不比 `base`——换供应商的同名模型直接漏检。每次都读 metadata 不缓存——每次检索多一次 SQLite 读，热路径变慢。

### 1.3.7 写库：Chroma 单例与重建

`kb/rag_core.py#L1034-L1051`、`#L1103-L1121`

```python
if self._vs is None:
    self._vs = Chroma(
        client=chroma_client(),
        persist_directory=config.PERSIST_DIR,
        embedding_function=get_embeddings(),
        collection_name=self.collection_name,
    )
```

```python
if mode == "rebuild":
    client = chroma_client()
    try:
        client.delete_collection(self.collection_name)
    except Exception:
        pass
    self._vs = None
vs = self._ensure_vs()
dim = _embed_and_store(vs, chunks)
embed_guard.invalidate(self.collection_name)
embed_guard.stamp(self.collection_name, dim)
```

**这段在干什么**：拿到（必要时创建）Chroma 实例；重建模式下先删整个 collection，再重新切片、embedding、写入；最后清掉指纹缓存并盖新指纹。

**为什么这样写**：必须传 `client=chroma_client()`。不传的话 LangChain 会按 `persist_directory` 自己 `new` 一个 `PersistentClient`，单例白做，而且多个 client 指向同一个 SQLite 文件有锁竞争（注释 `#L1037-L1040`，实测 163.5ms vs 73.1ms）。重建时 `invalidate` 必须在 `stamp` 之前：delete collection 已经把旧 metadata 一起删了，但进程内 `_VERIFIED` 里还记着“这个库校验过”，不先清掉就不会重新盖章。

**改成另一种写法会怎样**：每个 `RagPipeline` 自己 `new Chroma(...)` 不传 client——同一进程里多个 PersistentClient，第一次打开几百毫秒，之后每次请求都重复建；并发写时 SQLite 锁竞争。重建不删旧 collection 只 `append`——旧切片（500 字粒度）和新切片（300 字粒度）混在一起，同一段内容被检索两次，MMR 还要花力气去重。

### 1.3.8 文件清单与“状态”

`kb/pipelines.py#L107-L130`、`#L490-L516`、`#L564-L580`

```python
tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
try:
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    _replace_with_retry(tmp, path)
except Exception:
    try:
        os.unlink(tmp)
    except OSError:
        pass
    raise
```

```python
with _REGISTRY_LOCK:
    _load_files()
    _get_or_create_raw(kb_id)
    cur = _KB_FILES.setdefault(kb_id, [])
    for r in records:
        cur[:] = [x for x in cur if x.get("name") != r.get("name")]
        cur.append(r)
    _save_files()
```

```python
return {
    "kb_id": kb_id,
    "chunks": chunks,
    "chunk_size": params[0] if params else None,
    "chunk_overlap": params[1] if params else None,
    "files": get_files(owner, kb_id) or pipe.list_sources(),
}
```

**这段在干什么**：原子写文件清单；登记文件时先重读磁盘、同名覆盖、再整份写；状态查询只汇总“有多少片、切片参数、文件列表”。

**为什么这样写**：清单是“内存 + JSON”两层，读的时候走内存，写的时候必须重读磁盘再改，否则多进程/多线程会互相覆盖。`add_file_records` 在 `_build` 的 `finally` 里调用：中途某个文件失败，前面已经入库成功的文件记录也要写进去（测试盯着这个语义）。`get_status` 没有 `processing/ready/failed`，因为上传是同步的，没有异步任务状态可报。

**改成另一种写法会怎样**：直接 `open(path, "w")` 写——进程被杀留下截断 JSON，下次启动 `_load_files` 读到异常，整个清单变空，界面显示“没有文件”但 Chroma 里还有片段。写失败静默 `pass`——界面正常，重启后记录全丢。给 `get_status` 硬加一个 `ready` 字段——它永远是 `ready`，因为函数只在建库完成后才被调用；这就是图上状态机在代码里不存在的原因。

---

## 1.4 一条端到端数据流：上传 100 页 PDF

```text
POST /api/kb/{kb_id}/upload
  └─ _must_own(uid, kb_id)                       # 不是自己的库 → 404
  └─ quota.guard(uid, "embed")                   # 日额度已满 → 429/QuotaExceeded
  └─ 校验扩展名 / 单文件大小 / 本次总大小          # 不合法 → 400
  └─ save_stream → data/kb_sources/{kb_id}/xxx.pdf
  └─ quota.consume(uid, "upload", upload_mb)     # 上传流量额度
  └─ asyncio.to_thread(_build)
       └─ pipelines.kb_lock(kb_id)               # 同库入库串行
       └─ pipe.build_index(..., mode=append)
            └─ load_document → list[Document]     # PDF 每页一个 Document
            └─ split_documents → list[Document]   # 500/50，带 chunk_index
            └─ _embed_and_store
                 └─ _inject_topics                # 每来源一次 LLM，失败静默
                 └─ 10 条/批，4 批并发 embed_documents
                 └─ 逐批 upsert 到 Chroma
            └─ embed_guard.invalidate + stamp     # 写 model/base/dim
            └─ _cache_invalidate                  # 清检索/来源/BM25 缓存
       └─ finally: add_file_records               # 已成功的文件也要登记
       └─ rebuild 时 retain_file_records          # 清掉没再上传的旧记录
  └─ quota.bump_usage(uid, "embed", total)        # 按真实切片数记账
  └─ 返回 {kb_id, chunks, files}
```

### 失败与降级

| 失败点 | 代码位置 | 用户看到 | 系统行为 |
|---|---|---|---|
| 不是自己的库 | `api/kb.py#L64` | 404 `知识库不存在` | 不泄露“库存在但无权” |
| 日向量化额度满 | `#L66` | 429 / 配额提示 | 一个字节都不落盘 |
| 文件格式不支持 | `#L96-L104` | 400 + 支持的格式 | 第一趟校验就拒，不写盘 |
| 写盘中途失败 | `#L115-L122` | 500 | `upload.cleanup` 清掉已落文件 |
| PDF 是扫描件 | `rag_core.py#L790-L801` | 400/500 + OCR 提示 | 不产生空库 |
| embedding 401/429 | `rag_core.py#L681` / `#L1016` | 中文错误 | 入库中断；已写入的批留在库里（没有事务回滚） |
| 指纹不一致 | `embed_guard.py#L183-L187` | 检索时 `EmbeddingMismatch` | 不自动重建，要求人工点“重建索引” |
| 清单写失败 | `pipelines.py#L151-L159` | 上传仍返回成功 | 只记 warning；重启后文件记录可能丢 |
| 重建中途失败 | `rag_core.py#L1103-L1117` | 500 | 旧 collection 已删，库可能为空/半成品 |

---

## 1.5 工程坑点（入库侧）

1. **`EMBED_BATCH_SIZE=10` 不是性能参数，是接口上限。** 调到 50 会 400/429；如果想加速，调的是 `EMBED_CONCURRENCY`，不是 batch。
2. **并发调太高会撞 429。** 百炼对并发/TPM 有限制，4 是折中；调到 16 可能“看起来更快”，实际大量重试，反而更慢。
3. **upsert 必须串行。** 写入并发会竞争 HNSW/SQLite；embedding 可以并发，因为它是纯网络调用。
4. **指纹只报警，不重建。** 换模型后第一次检索报 `EmbeddingMismatch`，但 `/api/chat/stream` 会把它交给 `describe_api_error`，最终显示“服务暂时不可用”，真正的“请重建索引”提示只在同步 `/api/kb/{id}/query` 的 `RuntimeError` 分支里保留。这是提示口径的不一致，排障时要先看日志里的 `EmbeddingMismatch`。
5. **老库没有指纹会被“认证”。** `check()` 在 `stored is None` 时直接 `stamp` 当前指纹并放行。如果这个老库本来就不一致，之后再也查不出来；唯一补救是全量重建。
6. **重建是破坏性的。** 先 `delete_collection` 再 embedding；中途失败旧数据已经没了。生产上应先备份 `chroma_db/`，或者改成“写新 collection 再切换”。
7. **图上“噪声清洗”没有实现。** 页眉页脚会留在切片里；如果资料页眉很长，BM25 会把它当高频词，影响关键词通道。
8. **图上“状态机”没有实现。** 上传是同步阻塞；大文件会让请求等很久，前端没有进度可显示。

---

## 1.6 三层自检题（入库）

### 第一层：回忆

**Q1**：默认切片参数是多少？`EMBED_BATCH_SIZE`、`EMBED_CONCURRENCY` 默认多少？
**答对标准**：能说出 `chunk_size=500`、`chunk_overlap=50`、batch=10、并发=4，并知道这些在 `core/config.py#L42-L53`。

**Q2**：`load_document` 返回什么？每个 `Document` 至少带哪些 metadata？
**答对标准**：返回 `list[Document]`；至少带 `source_path`，有 `source_name` 时带 `source`，PDF 还有 `page`；入库后追加 `chunk_index` 和 `topics`。

**Q3**：`pipelines.get_status` 返回哪些字段？有没有 `status` / `processing`？
**答对标准**：`kb_id / chunks / chunk_size / chunk_overlap / files`；没有状态字段。

### 第二层：理解

**Q4**：为什么 `embed_guard.stamp` 必须在 `_embed_and_store` 之后调用，而不是之前？
**答对标准**：因为指纹里的 `dim` 要来自**真实向量长度**，不是模型文档上写的维度；接口截断/兼容层补齐时，文档和实际会不一致，记错了校验就失效。

**Q5**：为什么上传要“两趟循环”（先全验、再全写）？
**答对标准**：避免第 3 个文件格式错误时，前两个已经落盘但没登记、没入库，成为只能删库才能清的孤儿文件。

**Q6**：为什么 embedding 可以并发，upsert 必须串行？
**答对标准**：embedding 是纯网络请求、批与批无共享状态；upsert 会竞争同一个 Chroma collection 的 HNSW 索引和底层 SQLite，并发写结果不确定。

### 第三层：审查（故意写错的入库代码）

下面这段“看起来能跑”，请找出至少 4 个问题，并说明影响。答案在题后。

```python
def build_index(self, chunk_size, chunk_overlap, paths, mode="append", source_names=None):
    docs = []
    for p in paths:
        docs.extend(load_document(p))                 # ①
    chunks = split_documents(docs, chunk_size, chunk_overlap)

    if mode == "rebuild":
        pass                                          # ②

    vs = self._ensure_vs()
    vectors = get_embeddings().embed_documents([c.page_content for c in chunks])  # ③
    vs._collection.upsert(
        ids=[str(i) for i in range(len(chunks))],     # ④
        embeddings=vectors,
        documents=[c.page_content for c in chunks],
        metadatas=[c.metadata for c in chunks],
    )
    embed_guard.stamp(self.collection_name, len(vectors[0]))
    return len(chunks)
```

**参考答案与影响**：

1. **① 没传 `source_name`**：`metadata["source"]` 会保留加载器写入的文件路径（临时路径或 `data/kb_sources/...`），而不是 `kb_files.json` 里的展示名。影响：引用卡片来源显示成路径；`delete_by_source(name)` 用展示名删不到；重建时 `retain_file_records` 也匹配不上。
2. **② rebuild 没删旧 collection**：旧切片和新切片混在一起。影响：换切片参数后同一段内容出现两种粒度，检索重复、MMR 被重复片段占名额。
3. **③ 一次性 `embed_documents` 全量**：超过百炼单次 10 条上限，直接 400；即使没超，峰值内存也随切片数线性增长。
4. **④ id 用 `0..n-1`**：每次 append 都从 0 开始，`upsert` 会覆盖上一次的 0..n-1，旧文件片段被新文件“顶掉”。影响：库越传越少、内容错乱。项目用 `uuid4`。
5. **没有 `chunk_index`**：预览无法按文件内顺序拼回正文。
6. **没有 `_cache_invalidate`**：检索继续命中旧缓存，上传后问新内容答不出来。
7. **没有 `try/finally` 登记文件清单**：入库成功但清单没记录，界面显示“没有文件”。

**答对标准**：找出至少 4 个，且能说出“会导致覆盖/重复/超限/缓存不失效/删不掉”中的具体后果；只说出“不规范”不算。

---

## 1.7 小实验：切片重叠到底有没有 50 字（30 分钟）

**目标**：不改源码，用现成的 `load_document` / `split_documents` 验证 `chunk_overlap` 的真实效果。

**步骤**：

1. 在系统临时目录建一个 `demo.txt`，复制一段 1500 字左右的中文（比如从项目 README 里取）。
2. 在项目根目录运行下面脚本（只读调用，不写库）：

```python
import sys, tempfile, os
sys.path.insert(0, r"c:\biancheng\agent-hub")
from kb.rag_core import load_document, split_documents

path = r"C:\Users\...\demo.txt"   # 换成你的临时文件
docs = load_document(path, source_name="demo.txt")

for overlap in (50, 0):
    chunks = split_documents(docs, 500, overlap)
    print("overlap =", overlap, "chunks =", len(chunks),
          "max_len =", max(len(c.page_content) for c in chunks))
    shared = 0
    for a, b in zip(chunks, chunks[1:]):
        n = 0
        for k in range(1, min(80, len(a.page_content), len(b.page_content)) + 1):
            if a.page_content[-k:] == b.page_content[:k]:
                n = k
        shared += n
    print("相邻片段平均共享字符数 =", shared / max(1, len(chunks) - 1))
```

**判据（预期 vs 实际）**：

| 观察项 | 预期 | 实际（你填） |
|---|---|---|
| `chunk_size=500` 时单片最大长度 | ≤ 500 字符 | |
| `overlap=50` 时相邻片段共享字符数 | 约 50（±10） | |
| `overlap=0` 时相邻片段共享字符数 | 接近 0 | |
| 切片数量 | overlap=50 比 0 略多 | |

**判定**：如果 `overlap=50` 的平均共享字符数明显小于 40，说明文本里没有足够的自然边界，splitter 在硬切；如果大于 60，检查是不是把“共享后缀/前缀”的检测写错了（只比较了 1 个字符也会得 1）。这个实验不需要网络、不需要 API key，30 分钟内能跑完。

---

# 第二部分 模块 1：检索与依据判定

## 2.1 先建立直觉

场景：小陈的知识库里有三个文件：《考研数学复习规划.md》《高数笔记.pdf》《英语单词.txt》。他问：“考研数学怎么复习？”

**如果只做“向量 top-3”，会发生什么：**

1. 向量检索按语义相似度取前 3。`《高数笔记.pdf》` 里相邻的三片都讲“极限”，重叠 50 字，三片内容几乎一样——top-3 名额被同一段话占满，真正讲“复习顺序”的规划文件排在第 4，进不来。
2. 问句里有“考研数学”这个专有组合，向量可能把“考研英语怎么复习”判得很近（句式相同、语义场相近）；BM25 按词面命中“考研数学”，能把英语片段压下去。
3. 取回来的片段里可能有一条相似度只有 0.05（完全不相关）。如果直接塞进 prompt，模型看到“有资料”，就会硬答。
4. 如果 top-1 只有 0.31，模型仍然会顺着写一段“看起来很像”的答案。需要在生成前告诉它“这轮依据不够，别硬答”。

所以检索不是“查一下”，而是四件事：

```text
扩召回（多捞几倍，给后面挑）
  → 双通道（向量 + BM25，互补）
  → 融合（RRF，不需要调分数尺度）
  → 精排（丢离群 + MMR 去重）
  → 依据判定（够不够格，决定要不要示弱）
```

---

## 2.2 LangChain 零基础补丁（检索视角）

| 概念 | 一句白话 | 本项目用法 | 位置 | 替我们做了什么 | 用了吗 |
|---|---|---|---|---|---|
| `Document` | 正文 + metadata 的“纸” | 检索返回的每条都是 `Document`；`id` 用于 RRF 去重 | `kb/rag_core.py#L290-L300` | 让“片段”能带来源、页码、主题、id | 是 |
| `Embeddings` | 文本 → 向量 | `embed_query` 给问题算向量；`embed_documents` 给切片算 | `core/llm.py#L69-L87`；`rag_core.py#L681`、`#L1016` | 封装 HTTP、重试、超时 | 是 |
| `Chroma` | 向量库的 LangChain 壳 | `similarity_search_by_vector_with_relevance_scores` 返回 `[(Document, distance)]` | `rag_core.py#L1343`、`#L1346` | 执行 HNSW 近邻查询，带回距离 | 是 |
| `ChatOpenAI` | 聊天模型客户端 | 查询改写、生成答案 | `rag_core.py#L754`、`#L1461` | 调 DeepSeek | 是 |
| `Retriever` | “问题 → Document 列表”的接口 | **没用**；项目自己写 `retrieve_with_score` | `rag_core.py#L1309-L1366` | 把召回/过滤/排序收进一个对象 | 否 |
| `Runnable` / `Chain` | LCEL 管道 | **没用**；每一步都是普通函数 | `retrieve_with_score` / `select_diverse` | 自动串联、自动传递 | 否 |
| `Tool` | Agent 工具包装 | 本模块**不用** | — | 让模型选择工具 | 否 |
| `TextSplitter` | 切片器 | 入库用；检索不直接用 | `#L931-L948` | 只影响“片段长什么样” | 入库用 |

### 如果改用框架写法

```python
retriever = vs.as_retriever(
    search_type="mmr",
    search_kwargs={"k": 3, "fetch_k": 12, "lambda_mult": 0.65},
)
docs = retriever.invoke("考研数学怎么复习")
```

这段能返回 3 条 `Document`，但会丢掉：

1. **距离/相似度**：`Retriever` 只给 `Document`，不给 `distance`。没有距离就算不出 `strength`，`assess_grounding` 和引用卡片上的“强/中/弱”都做不了。
2. **`distance=None` 的语义**：BM25 独有条目没有向量距离；框架的 MMR 检索器默认只走向量，不会告诉你“这条是关键词召回的”。
3. **owner 隔离**：缓存键要含 `owner`，否则 A 的画像偏置结果会被 B 复用；`Retriever` 不管这件事。
4. **SSE 时序**：项目要在正文 token 之前先发 `sources` 和 `grounding` 两帧；LCEL 链只能最后给一个字符串。
5. **降级**：BM25 出错要退回纯向量；框架链里一个组件抛异常，整条链就断。

LCEL 的写法大概是这样：

```python
chain = (
    {"context": retriever, "question": RunnablePassthrough()}
    | prompt
    | llm
    | StrOutputParser()
)
answer = chain.invoke("考研数学怎么复习")
```

它能工作，但 `context` 是黑盒：你拿不到“这 3 条的距离是多少、哪条是 BM25 独有、top1 够不够 0.45”。对于“要让用户看到引用强度、要能拒答”的产品，这些中间量不是调试信息，是产品的一部分。所以这里手写。

---

## 2.3 核心代码逐段吃透

### 2.3.1 查询向量缓存：同题并发只发一次

`kb/rag_core.py#L668-L691`

```python
def _embed_query(question: str) -> list[float]:
    with _EMBED_COND:
        while True:
            hit = _EMBED_CACHE.get(question)
            if hit is not None:
                _EMBED_CACHE.move_to_end(question)
                return hit
            if question not in _EMBED_INFLIGHT:
                _EMBED_INFLIGHT.add(question)
                break
            _EMBED_COND.wait()
    try:
        vec = get_embeddings().embed_query(question)
        with _EMBED_COND:
            _EMBED_CACHE[question] = vec
            _EMBED_CACHE.move_to_end(question)
            while len(_EMBED_CACHE) > _EMBED_CACHE_MAX:
                _EMBED_CACHE.popitem(last=False)
        return vec
    finally:
        with _EMBED_COND:
            _EMBED_INFLIGHT.discard(question)
            _EMBED_COND.notify_all()
```

**这段在干什么**：用 LRU（最近最少使用淘汰）缓存问题向量；同一个问题并发进来时，只让一个线程去发 embedding 请求，其他线程等它回来复用结果。

**为什么这样写**：`embed_query` 是一次约 250ms 的网络请求，也是真实费用。用户连点两次、或者两个标签页问同一句，没必要发两次。`_EMBED_INFLIGHT` + `Condition` 是“单飞”（single-flight）：缓存 miss 时不是所有线程都冲出去，而是登记一个“我在取”，其他线程 `wait()`。

**改成另一种写法会怎样**：不做缓存——每次检索都多一次 250ms 的 embedding，费用翻倍。不做 single-flight——缓存 miss 的瞬间 10 个并发请求各发一次，正好撞上游限流。把 key 改成 `(collection, question)`——同一个问题在不同库会各算一次，省不了；项目选择“只按问题缓存”，因为向量只跟文本和模型有关。

### 2.3.2 总入口：校验 → 缓存 → 扩召回 → 双通道 → 精排 → 偏置

`kb/rag_core.py#L1309-L1366`

```python
embed_guard.check(self.collection_name)

cached = _cache_get(self.collection_name, question, top_k, owner)
if cached is not None:
    metrics.inc("retrieval_cache", {"kind": "hit"})
    return cached

if not self._vs:
    self.get_or_open()
total = self.count()
if total == 0:
    raise RuntimeError("知识库为空，请先上传文档构建索引")

vec = _embed_query(question)
mult = config.RETRIEVE_FETCH_MULT
if top_k <= 1 or mult <= 1:
    result = self._vs.similarity_search_by_vector_with_relevance_scores(vec, k=top_k)
else:
    fetch_k = min(max(top_k * mult, top_k + 6, config.RETRIEVE_MIN_FETCH), total)
    vec_cands = self._vs.similarity_search_by_vector_with_relevance_scores(vec, k=fetch_k)
    result = None
    if config.HYBRID_ENABLED:
        try:
            merged = self._hybrid_candidates(question, vec_cands, config.HYBRID_KEYWORD_TOP)
        except Exception:
            merged = None
        if merged:
            result = select_diverse(merged, top_k)
    if result is None:
        result = select_diverse(vec_cands, top_k)

if owner and config.RETRIEVAL_BIAS_ENABLED and result:
    result = _apply_profile_bias(result, owner)

if not result:
    metrics.inc("retrieval_empty")
_cache_set(self.collection_name, question, top_k, result, owner)
return result
```

**这段在干什么**：检索的总调度。顺序是固定的：指纹校验 → 结果缓存 → 打开库 → 算查询向量 → 扩召回 → 混合 → 精排 → 画像偏置 → 写缓存。

**为什么这样写**：`embed_guard.check` 必须在缓存之前。缓存里存的是“旧模型检索出来的结果”，换了模型后它一样是错的；把校验放在缓存之后，等于给错结果开了一条捷径（注释 `#L1319-L1321`）。`fetch_k` 取 `max(top_k*3, top_k+6, 12)` 再和库大小取小：多捞几倍是为了给离群过滤和 MMR 留挑选空间，但小库不能捞超过总量。`top_k<=1` 或关掉候选池时直接取 `top_k`，因为多捞没有意义。

**改成另一种写法会怎样**：不扩召回直接 `k=top_k`——相邻重复切片会把名额占满，MMR 没有候选可挑。把 `select_diverse` 放在 BM25 融合之前——BM25 独有条目没有距离，会被当成“最不相关”丢掉。不把 `owner` 放进缓存键——A 的画像偏置排序会被 B 直接复用。

### 2.3.3 向量通道与扩召回

`kb/rag_core.py#L1345-L1346`

```python
fetch_k = min(max(top_k * mult, top_k + 6, config.RETRIEVE_MIN_FETCH), total)
vec_cands = self._vs.similarity_search_by_vector_with_relevance_scores(vec, k=fetch_k)
```

**这段在干什么**：向 Chroma 要 `fetch_k` 条候选，每条带 `(Document, distance)`。`distance` 越小越相关。

**为什么这样写**：如果只要 3 条，就直接向 HNSW 要 3 条，那么第 4 条开始的信息完全看不到；而第 4 条可能正是“讲复习顺序”的那条，只是被 3 条重复的极限片段挤掉了。多要几倍，才有东西可过滤、可去重。`top_k + 6` 是给“top_k 很小时”一个额外下限；`RETRIEVE_MIN_FETCH=12` 是绝对下限；最后 `min(..., total)` 防止要超过库里的片段数。

**改成另一种写法会怎样**：`fetch_k = top_k * 10`——候选太多，MMR 的 O(n²) 去重变慢，而且把大量低相似度尾巴拉进来，离群过滤压力大。`fetch_k` 不设上限——小库（只有 5 片）会向 Chroma 要 12 条，Chroma 可能报错或返回空。`fetch_k` 小于 `top_k`——最终挑不出 `top_k` 条。

### 2.3.4 BM25 通道：为什么不用 jieba

`kb/rag_core.py#L152-L208`

```python
def _tokenize(text: str) -> list[str]:
    t = (text or "").lower()
    tokens = _ASCII_WORD.findall(t)
    for run in _CJK_RUN.findall(t):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens
```

```python
class _BM25:
    def __init__(self, tokenized):
        self.k1, self.b = 1.5, 0.75
        ...
        for i, toks in enumerate(tokenized):
            for term, f in Counter(toks).items():
                postings[term].append((i, f))
                df[term] += 1
        self.idf = {t: math.log(1 + (self.n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def top(self, query, k):
        scores = defaultdict(float)
        for term in _tokenize(query):
            idf = self.idf.get(term)
            if idf is None: continue
            for i, f in self.postings[term]:
                dl = self.doc_len[i] or 1
                denom = f + self.k1 * (1 - self.b + self.b * dl / avgdl)
                scores[i] += idf * f * (self.k1 + 1) / denom
        return sorted(scores.items(), key=lambda x: -x[1])[:k]
```

**这段在干什么**：把文本切成 token（英文/数字按词，中文按相邻两字），建倒排表 `term → [(doc_idx, tf)]`，查询时只扫命中词的文档，按 BM25 公式打分。

**为什么这样写**：纯向量对专有名词、缩写、编号（“CUDA 12.4”“第 3 条”）很弱，因为向量看的是整体语义，不是字面。BM25 按词面命中，正好补这条。不引 jieba 是因为它会拖一串依赖，而字符 bigram 对中文专有名词/编号的召回已经够用；口径也和去重用的 2-gram 一致。IDF 带 `+0.5` 平滑，恒为正，避免常见词拿到负分。

**改成另一种写法会怎样**：用 jieba 分词——多一个依赖，启动变慢；而且用户输入里的新词（“考研数学”）可能被切错。用全量打分（每个词扫全库）——O(词数 × 库大小)，大库直接卡死；倒排表只扫命中文档。`b=0`（不考虑文档长度）——长文档天然容易命中更多词，会垄断结果。

### 2.3.5 RRF：不需要调分数尺度的融合

`kb/rag_core.py#L274-L305`

```python
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
    dist.setdefault(did, None)
    by_id.setdefault(did, d)
...
order = sorted(fused.items(), key=lambda x: -x[1])
top = order[0][1] or 1.0
return [(by_id[did], dist.get(did), score / top) for did, score in order]
```

**这段在干什么**：向量通道和关键词通道各自给一个排名；同一个片段在两边的排名分别算 `1/(K + rank)` 并相加；最后按融合分降序，归一化到 `(0,1]`。关键词独有条目的 `distance` 设为 `None`。

**为什么这样写**：向量距离和 BM25 分数的量纲完全不同（前者越小越好、后者越大越好），直接加权需要调参。RRF 只看“排第几”，不看分数绝对值，所以不需要调尺度，也不用训练。`K=60` 是业界惯用值：它弱化头部名次差异，让“两个通道都排前几”比“一个通道排第一”更占优。

**一个具体例子**（`K=60`）：

| 片段 | 向量排名 | BM25 排名 | 融合分 | 归一化 |
|---|---|---|---|---|
| A | 1 | 2 | `1/61 + 1/62 = 0.03252` | 1.000 |
| B | 无 | 1 | `1/61 = 0.01639` | 0.504 |
| C | 2 | 无 | `1/62 = 0.01613` | 0.496 |

A 在两个通道都靠前，所以排第一；B/C 各只有一个通道，分数接近。注意 B 的 `distance=None`——它没有向量距离。

**改成另一种写法会怎样**：把 BM25 分数和余弦相似度直接相加——量纲不同，BM25 分数通常几十到几百，余弦在 0~1，向量通道会被完全淹没。用 `rank` 的原始名次（不减 K）——第一名 1 分、第二名 0.5 分，头部差距过大，一个通道的第一名就能压过两个通道都第二的片段。忘记 `dist.setdefault(did, None)`——关键词独有条目没有 `distance`，后面 `_to_similarity` 会 `KeyError` 或把 `None` 当 0 处理。

### 2.3.6 距离 → 相似度：`1 - d/2` 的来历

`kb/rag_core.py#L117-L140`

```python
def _to_similarity(distance: float) -> float | None:
    if distance is None or distance < 0 or distance > 4:
        return None
    return 1 - distance / 2
```

**这段在干什么**：把 Chroma 返回的平方 L2 距离换算成余弦相似度；换算不了时返回 `None`。

**为什么这样写**：collection 创建时没指定 `hnsw:space`，Chroma 默认是**平方 L2**；百炼 `text-embedding-v3` 返回的是单位向量。对单位向量，`d = ||a-b||² = 2 - 2cos`，所以 `cos = 1 - d/2`，`d` 的合法值域是 `[0,4]`，对应 `cos ∈ [-1,1]`。历史 bug：早先按余弦距离的 `[0,2]` 判定，`d ∈ (2,4]`（cos < 0）被当成“换算不了”，导致 `select_diverse` 里 `all(...)` 短路，`RETRIEVE_MIN_SIM` 整条阈值被跳过；负余弦片段反而拿到虚高的相关度。测试 `tests/test_retrieve_select.py#L65-L80` 专门盯着这个回归。

**改成另一种写法会怎样**：用 `distance > 2` 当守卫——cos 为负的离群片段不被过滤，还会把整批过滤旁路掉（测试 3 和 3b 会失败）。把 `None` 当 0 处理——BM25 独有条目会被当成“完全不相关”，在离群过滤里被误杀。用 `1 - distance`——量纲错，`d=0` 时相似度 1，`d=1` 时 0，但实际 `d=1` 对应 cos 0.5。

### 2.3.7 精排 `select_diverse`：先丢离群，再 MMR

`kb/rag_core.py#L448-L527`

```python
scored = [(c, _to_similarity(_dist(c))) for c in cands]
vec_sims = [sim for c, sim in scored if _dist(c) is not None]
if vec_sims and all(sim is not None for sim in vec_sims):
    kept = [c for c, sim in scored if _dist(c) is None or sim >= min_sim]
    dropped = len(cands) - len(kept)
    if dropped > 0:
        metrics.inc("retrieval_gate_blocked", n=dropped)
    cands = kept or [
        min(cands, key=lambda c: _dist(c) if _dist(c) is not None else float("inf"))
    ]
```

```python
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
```

**这段在干什么**：两步。第一步，把明显不相关的尾巴砍掉：只在“所有向量距离都能换算成余弦”时才过滤；BM25 独有条目（`distance=None`）一律保留；如果砍空了，至少留最相关一条。第二步，MMR 贪心：第一条固定最相关；之后每次在“相关度高、且与已选内容重复度低”的候选里挑，`val = λ * rel - (1-λ) * dup`。

**为什么这样写**：如果直接取 top-k，相似度再低的片段也会进 prompt，模型被迫硬答；相邻切片重叠的部分还会占满名额。离群过滤解决“不相关”，MMR 解决“重复”。`all(...)` 的条件是刻意的：只要有一批向量距离换算不了（比如换了没归一化的模型），就**整批跳过过滤**，宁可少滤几个，也不能把相关片段误杀。BM25 独有条目没有距离可比，但词面命中已经保证相关，所以保留。`λ=0.65` 让相关度略占主导，不至于为了多样性选进不相关的片段。

**改成另一种写法会怎样**：不管能不能换算都过滤——换成未归一化模型时，`_to_similarity` 返回 `None`，所有片段都被当“不相关”砍掉，库直接检索为空。BM25 独有条目也按 `distance=None` 过滤——关键词通道等于白做。`λ=1.0`——退化成纯相关度排序，重复片段重新占满名额（测试 5 验证的是“λ=1 时按距离排”，不是推荐配置）。

### 2.3.8 依据判定：只看 top1，medium 也算 weak

`kb/rag_core.py#L317-L373`

```python
sims = [_to_similarity(d) for _, d in items]
known = [s for s in sims if s is not None]
if not known:
    return {"strength": STRENGTH_HIGH, "best": None, "count": len(items), "weak": False}

best = max(known)
if best >= config.RETRIEVE_LOW_SIM:
    strength = STRENGTH_HIGH
elif best > config.RETRIEVE_MIN_SIM:
    strength = STRENGTH_MEDIUM
else:
    strength = STRENGTH_LOW
...
return {
    "strength": strength,
    "best": best,
    "count": len(items),
    "weak": strength in (STRENGTH_LOW, STRENGTH_MEDIUM),
}
```

**这段在干什么**：把每条结果的距离换算成相似度，取最大值（top1）分级：`≥0.45` 高；`0.25~0.45` 中；`≤0.25` 低。`medium` 和 `low` 都算 `weak`。一条都换算不出来时判 `high` 放行。

**为什么这样写**：只用 top1，不用平均分/最低分——一条强相关配几条噪声，用平均会把整体拖成“检索质量差”，正常回答被平白加上示弱提示。`medium` 也算 weak，是因为它的语义是“留下了，但都不够格”，和 low 的区别只是“有没有沾点边”，不是“能不能当依据”；只让 low 示弱，`0.26~0.44` 这段最常见的灰色地带反而会被正常作答，恰好漏掉最容易硬答的一档。换算不出来判 high 是刻意的：判定不了时宁可让模型正常回答，也不要凭空说“我找不到依据”——后者会把配置问题伪装成数据问题。

**改成另一种写法会怎样**：用平均分——一条 0.6 配两条 0.1，平均 0.27，判 medium，本来够格的回答被加上“依据较弱”。只让 low weak——0.3 的片段被正常当依据，模型硬答。换算不出来判 low——换了个未归一化模型，全站突然开始说“我找不到依据”，排查方向会被带偏。

### 2.3.9 `grounding_note`：给模型的“别硬答”约束

`kb/rag_core.py#L399-L417`、`#L377-L396`

```python
def grounding_note(assessment: dict) -> str:
    if not config.GROUNDING_NOTICE:
        return ""
    strength = (assessment or {}).get("strength")
    if strength == STRENGTH_LOW:
        return prompt_store.resolve("rag_note_low", LOW_CONFIDENCE_NOTE)[0]
    if strength == STRENGTH_MEDIUM:
        return prompt_store.resolve("rag_note_medium", MARGINAL_CONFIDENCE_NOTE)[0]
    return ""
```

**这段在干什么**：根据 `strength` 返回一段追加到 system prompt 的中文约束；`high` / `none` 返回空串。

**为什么这样写**：通用 `SYSTEM_PROMPT` 里“资料不足要说明”太软；模型看到 prompt 里真有几段资料，哪怕相似度 0.3 也会顺着编。单独再压一遍才压得住。模板走 `core/prompt_store` 的 active 版本，没有 active 时退回常量，这样“进化提示词”时两条路径（RAG 与 Agent）共用同一个版本化入口，不会分叉。

**改成另一种写法会怎样**：只给 low 加约束——medium（0.26~0.44）仍然硬答，而这恰好是最常见的灰色地带。把约束拼在 user message 里而不是 system——模型更容易把它当成“这一段的要求”，而不是全局规则。关掉 `GROUNDING_NOTICE`——行为完全回到改造前，不示弱、不追加提示；这是排障开关，不是推荐配置。

### 2.3.10 引用条目：`build_sources` 与 `_source_meta`

`kb/rag_core.py#L1369-L1421`

```python
low = config.RETRIEVE_LOW_SIM
for d, distance in docs_scores:
    sim = _to_similarity(distance)
    if sim is None:
        strength = None
    elif sim >= low:
        strength = STRENGTH_HIGH
    elif sim > config.RETRIEVE_MIN_SIM:
        strength = STRENGTH_MEDIUM
    else:
        strength = STRENGTH_LOW
    metas.append({...})
...
out.append({
    "id": i + 1,
    "source": m["source"],
    "page": (m["page"] + 1) if isinstance(m["page"], int) else None,
    "score": round(m["sim"], 4) if m["sim"] is not None else None,
    "strength": m["strength"],
    "preview": d.page_content.replace("\n", " ")[:80],
    "text": d.page_content[:1500],
})
```

**这段在干什么**：把 `(Document, distance)` 整理成前端引用卡片要的字段；页码从 0 基转 1 基；`score` 给相似度，不给距离；`strength` 用和 `assess_grounding` 同一套阈值。

**为什么这样写**：前端那个位置的标签是“相似度”，必须给真相似度（越大越相关），不能把距离直接丢过去。历史 bug：线上卡片把类目“相似度”标成 1.089 / 1.160 / 1.237（实为距离）。`_source_meta` 把 `build_sources` 和 `_format_refs` 的计算合并成一次，阈值口径不会再漂——两处各写一遍“强/中/弱”，迟早会分叉，用户看到“卡片说这条没用、正文却在用它答”。

**改成另一种写法会怎样**：直接把 `distance` 当 `score`——前端显示 1.089，用户以为相似度超过 1，完全无法判断。`build_sources` 和 `assess_grounding` 用不同阈值——卡片标“弱”，回答侧却认为够格，自相矛盾。`page` 不 +1——PDF 第 1 页显示成第 0 页，用户翻不到。

### 2.3.11 缓存与失效：为什么没有 TTL

`kb/rag_core.py#L535-L581`

```python
_RETRIEVE_CACHE: OrderedDict[tuple, list] = OrderedDict()
_RETRIEVE_CACHE_MAX = 200
...
def _cache_invalidate(collection_name: str) -> None:
    with _RETRIEVE_CACHE_LOCK:
        for key in [k for k in _RETRIEVE_CACHE if k[0] == collection_name]:
            _RETRIEVE_CACHE.pop(key, None)
    with _SOURCES_CACHE_LOCK:
        _SOURCES_CACHE.pop(collection_name, None)
    with _BM25_COND:
        _BM25_CACHE.pop(collection_name, None)
        _BM25_NEG.pop(collection_name, None)
```

**这段在干什么**：检索结果按 `(collection, question, top_k, owner)` 缓存，LRU 200；来源清单缓存 64；BM25 索引缓存 8；库内容变更时清掉这三类。`_EMBED_CACHE` 按问题缓存向量，LRU 500。

**为什么这样写**：相同问题在库内容不变时结果确定，复用能省一次 embedding + 一次向量查询，省的是真实 API 费用和等待时间。没有 TTL 是刻意的：这些结果不是“随时间过期”的数据，而是“随库内容变化”的数据；只要入库/删除/重建时清掉对应库，就不会读到旧片段。`_RETRIEVE_CACHE` 的 key 含 `owner`，避免 A 的画像偏置结果被 B 复用；`_EMBED_CACHE` 只按问题，因为向量只跟文本和模型有关。

**改成另一种写法会怎样**：加一个 5 分钟 TTL——库刚更新时，用户最长 5 分钟问不到新内容；而缓存失效本来就能精确清掉，TTL 只是多一层不确定。缓存键不含 `owner`——跨用户串结果。`_cache_invalidate` 只清检索缓存不清 BM25——上传后 BM25 还在用旧全文，关键词通道返回已删除的片段。

### 2.3.12 画像偏置：只重排，不改距离

`kb/rag_core.py#L584-L633`

```python
profile = _session.get_user_tags(owner, limit=config.RETRIEVAL_BIAS_TOP)
if not profile:
    return docs_scores
profile_tags = {p["tag"] for p in profile}
return _bias_rerank(docs_scores, profile_tags, config.RETRIEVAL_BIAS_ALPHA)
```

```python
for idx, (d, distance) in enumerate(docs_scores):
    if distance is None:
        slots.append((d, None, None, idx))
        continue
    topics = d.metadata.get("topics", "") or ""
    segs = {s for s in topics.strip("|").split("|") if s}
    overlap = 0
    if segs and profile_tags:
        for p in profile_tags:
            if any(p in s or s in p for s in segs):
                overlap += 1
    biased = max(0.0, float(distance) - alpha * overlap) if overlap else float(distance)
    slots.append((d, float(distance), biased, idx))
...
ranked = iter(sorted((s for s in slots if s[2] is not None), key=lambda s: s[2]))
out = []
for slot in slots:
    if slot[2] is None:
        out.append((slot[0], None))
    else:
        d, dist, _biased, _idx = next(ranked)
        out.append((d, dist))
```

**这段在干什么**：读用户画像标签，和文档 `topics` 做双向包含匹配；命中一个标签就把距离减 `alpha`；只对有数值距离的条目重排；返回的 `distance` 仍是原始值。

**为什么这样写**：这是画像唯一进入检索链路的地方，作用是“同一问题下，优先展示用户长期关注的主题”。双向包含是为了兼容“数学”和“数学分析”这种粗细不一致的标签。返回原始 `distance` 是为了不污染后面的相似度/阈值判定——阈值看的是真实向量距离，不是偏置后的排序键。`distance=None` 的 BM25 独有条目没有距离可比，保持原位置，不参与重排；早先这里对 `None` 直接 `float()` 会抛 `TypeError`，只在“用户有画像 + 命中关键词独有片段”两个条件同时成立时炸成静默 500。

**改成另一种写法会怎样**：把偏置后的距离返回给调用方——`assess_grounding` 会把偏置当成真实相似度，用户标签越多，`strength` 越虚高。对 `None` 也减距离——类型错误或把关键词条目错误地排到前面。在 `select_diverse` 之前做偏置——MMR 的多样性选择会把刚被偏置上来的条目又挤掉，偏置白做。

### 2.3.13 查询改写：只在“像追问”时才花一次 LLM

`kb/rag_core.py#L720-L763`、`api/chat.py#L469-L498`

```python
def needs_rewrite(question, history):
    if not history: return False
    q = (question or "").strip()
    if not q: return False
    if len(q) <= config.QUERY_REWRITE_MAX_LEN:
        return True
    return any(cue in q for cue in _REWRITE_CUES)
```

```python
query = rewrite_query(question, history)
rewritten = query != question
if not config.ENHANCE_CHAT_ENABLED:
    return query, [], rewritten
text, changes = enhance_svc.enhance_for_retrieval(query)
return text, changes, rewritten
```

**这段在干什么**：多轮追问（“那它呢”“第二个呢”）单独看没有检索价值；先用历史把它改写成独立问题，再追加规范术语（“高数”→“高等数学”）。返回的是**检索用文本**，不回写用户原问题。

**为什么这样写**：不是每个问题都改写，只有“有历史 + 问题 ≤8 字或含指代词”时才多花一次 LLM——其余问题零额外开销。顺序不能反：改写可能冒出新的口语词，所以 `enhance` 必须排在 `rewrite` 后面。增强是**追加**而不是替换：替换在真实短句上每 3 条就有 1 条造出病句（“查重率”→“重复率检测率”）。失败一律退回原问题——改写是增益不是必需，不能因为它把检索搞挂。

**改成另一种写法会怎样**：每题都改写——多一次 LLM 调用，成本和延迟都上去，而且完整问题被改坏的风险大于收益。先 `enhance` 再 `rewrite`——术语可能被模型说回口语。把改写结果回写 `req.question`——聊天记录和界面显示的不是用户原话，用户会觉得“我说的话被改了”。

---

## 2.4 一条端到端数据流：“考研数学怎么复习”

### 正常路径

```text
POST /api/chat/stream  (mode=rag 或 auto 路由到 rag)
  │
  ├─ SSE: status {"text": "RAG 模式，正在检索知识库（kb_xxx）..."}   chat.py#L1462
  ├─ store.get_messages(...)[:-1]                                   # 历史，去掉刚落的用户句
  ├─ _retrieval_query(req.question, history)                        # chat.py#L469-L498
  │    ├─ rewrite_query：第一轮无 history → 原样返回
  │    └─ enhance_for_retrieval：追加规范术语
  ├─ pipe.retrieve_with_score(query, req.top_k, uid)                 # chat.py#L1476-L1478
  │    ├─ embed_guard.check(collection)                             # 指纹不一致 → EmbeddingMismatch
  │    ├─ _cache_get(collection, query, top_k, uid)                 # 命中 → 直接返回
  │    ├─ get_or_open / count                                       # 空库 → RuntimeError
  │    ├─ _embed_query(query)                                       # LRU/单飞
  │    ├─ 向量通道：fetch_k = max(3*top_k, top_k+6, 12) 与 total 取小
  │    ├─ BM25 通道：_get_bm25 → _BM25.top
  │    ├─ _rrf_merge → [(Document, distance|None, rank)]
  │    ├─ select_diverse：离群过滤 → MMR → top_k
  │    ├─ _apply_profile_bias(owner)                                # 仅重排，原始 distance
  │    └─ _cache_set
  ├─ build_sources(docs_scores) → SSE: sources {"items":[...]}       # chat.py#L1486-L1487
  ├─ assess_grounding(docs_scores) → SSE: grounding {...}             # chat.py#L1490-L1491
  ├─ if weak: SSE: status "检索相似度偏低，已提醒模型不要硬答"        # chat.py#L1492-L1493
  ├─ _build_messages(...)                                            # chat.py#L1630-L1696
  │    ├─ system = SYSTEM_PROMPT + grounding_note(grounding) + 场景
  │    ├─ history 经 assemble_history，按字符预算从新到旧保留
  │    └─ 最后一条 HumanMessage: 【参考资料】[1]...【用户问题】...
  ├─ _stream_llm → SSE: token {...}                                   # 正文增量
  ├─ SSE: done {...}                                                  # chat.py#L1591-L1599
  └─ BackgroundTask(_finish_turn)                                     # 关上游、还并发名额
```

### 数据结构

| 名称 | 类型 | 谁产生 | 谁消费 |
|---|---|---|---|
| `req.question` | `str` | 前端 | 改写、落库、最终 user message |
| `query` | `str` | `_retrieval_query` | `retrieve_with_score`、缓存 key |
| `vec_cands` | `list[(Document, float)]` | Chroma | RRF、纯向量回退 |
| `kw_pairs` | `list[(doc_idx, score)]` | BM25 | RRF |
| `merged` | `list[(Document, float\|None, float)]` | `_rrf_merge` | `select_diverse` |
| `docs_scores` | `list[(Document, float\|None)]` | `select_diverse` / 偏置 | `build_sources`、`assess_grounding`、`_build_messages` |
| `sources` | `list[dict]` | `build_sources` | SSE `sources` 帧、前端卡片 |
| `grounding` | `dict` | `assess_grounding` | SSE `grounding` 帧、`grounding_note` |
| `messages` | `list[HumanMessage / AIMessage / SystemMessage]` | `_build_messages` | `_stream_llm` |

### 失败与降级

| 失败点 | 代码位置 | 用户看到 | 降级 / 行为 |
|---|---|---|---|
| 库不存在 / 不是自己的 | `pipelines.get_or_create` → `NotOwned` | SSE `error` 帧 | 不检索、不生成 |
| 库为空 | `rag_core.py#L1332-L1334` | SSE `error`“知识库为空” | 不生成 |
| 指纹不一致 | `embed_guard.py#L183-L187` | SSE `error`（经 `describe_api_error` 变成“服务暂时不可用”） | 不自动重建；日志里有 `EmbeddingMismatch` |
| embedding 401 / 429 / 超时 | `rag_core.py#L681` | SSE `error` + 中文提示 | 直接失败，无检索结果 |
| BM25 建索引异常 | `rag_core.py#L1348-L1352` | 无感 | `merged=None`，退回纯向量 |
| 库超过 `HYBRID_MAX_DOCS` | `rag_core.py#L249-L253` | 无感 | 负缓存 300s，期间纯向量 |
| BM25 无命中 | `rag_core.py#L1305-L1306` | 无感 | 返回 `None`，纯向量 |
| 精排后为空 | `rag_core.py#L1362-L1363` | `status`“知识库未命中” | **不拒答**：`docs_scores=[]`，`references=""`，system 退回通用助手，模型用世界知识回答 |
| 依据 weak | `chat.py#L1492-L1493` | `grounding.weak=true` + 状态提示 | system 追加 `rag_note_low/medium`，模型被要求示弱 |
| LLM 上游失败 | `chat.py#L1520-L1522` | SSE `error` 帧 | 不落库半截回答 |
| 客户端断开 | `chat.py#L1526-L1527` | 连接断 | `acc.aborted`，不落库 |
| 长度截断 | `chat.py#L1529` 等 | `truncated=true` | 正文末尾提示“可能不完整” |

**这里有一个很容易读错的点**：检索为空时，代码**不会**触发“依据不足”的拒答话术。`grounding=None`，`_build_messages` 里 `references` 为空，system 换成了“你是一个友好、专业的 AI 助手”，模型会正常回答。也就是说，“知识库没命中”在 RAG 模式下等价于“退回普通 LLM”，而不是“告诉用户没有资料”。这是产品口径，不是 bug；但答辩时如果说“没有依据就一定拒答”，代码对不上。

---

## 2.5 工程坑点（检索侧）

### 1. 扩召回与阈值的冲突

`fetch_k` 多捞几倍，本意是给离群过滤和 MMR 留空间；但它同时把大量低相似度尾巴拉进候选。`select_diverse` 的过滤有一个“全批可换算才过滤”的条件：只要有一个向量距离落在 `[0,4]` 之外（`_to_similarity` 返回 `None`），`all(...)` 为 `False`，**整批跳过过滤**。于是扩召回拉进来的低分片段全部保留，`RETRIEVE_MIN_SIM` 这道闸失效。

这不是理论问题：换了未归一化的 embedding 模型时，距离可能超过 4；测试 `tests/test_retrieve_select.py#L53-L63` 明确要求“越界时整批跳过过滤，天气接口片段必须保留”。设计取舍是“宁可少滤，不误杀”，代价是扩召回 + 未归一化模型时，低分尾巴会进入精排。

### 2. BM25 独有条目没有 `distance`，画像偏置对它失效

`_rrf_merge` 对关键词独有条目 `dist.setdefault(did, None)`。`_bias_rerank` 遇到 `distance is None` 直接占位、不参与重排。所以：

- 用户画像再强，BM25 独有条目也不会被减距离；
- 如果结果全是 BM25 独有，偏置完全不起作用；
- 偏置重排只改变“数值距离条目”的相对顺序，`None` 条目留在原索引，可能出现“某个 BM25 条目夹在两个被重排的向量条目中间”的奇怪顺序。

更隐蔽的是：`_bias_rerank` 返回原始 `distance`，`assess_grounding` 又只看 `max`，所以偏置**不会改变** `strength`。它只改引用编号和展示顺序，不改“够不够格”的判定。读代码时不要把“偏置”误当成“提高可信度”。

### 3. 缓存没有 TTL

`_RETRIEVE_CACHE`（200）、`_SOURCES_CACHE`（64）、`_EMBED_CACHE`（500）都没有时间过期，只在库内容变更时由 `_cache_invalidate` 清掉。后果：

- **画像更新不触发失效**：用户这轮对话新增了“线性代数”标签，已有问题的缓存结果仍然按旧画像排序，直到库内容变更或 LRU 淘汰。
- **`_EMBED_CACHE` 只按问题缓存，不带模型/库**：同一个问题在所有库共用一份向量。正常情况没问题（向量只跟文本和模型有关），但如果运行期改了 `EMBEDDING_MODEL`，缓存里还是旧模型的向量，而 `embed_guard` 只检查库的指纹，不检查这个查询向量缓存。
- **重启清零**：进程重启后缓存全空，第一次检索会重新 embedding；这是预期行为，不是泄漏。

### 4. 换 embedding 模型只报错、不重建

`embed_guard.check` 在检索最前面，发现不一致抛 `EmbeddingMismatch`。它**不会**自动重建，原因是自动重建会触发未预期的批量计费，而且删旧 collection 中途失败会让库消失。老库没有指纹时更微妙：`check` 会直接 `stamp` 当前指纹并放行——如果那个老库本来就不一致，这次补写把它“认证”成一致，之后再也查不出来。

还有一个提示口径问题：`/api/chat/stream` 的 `except` 用 `describe_api_error(e)`，`EmbeddingMismatch` 不匹配任何已知类名/状态码，最终变成“服务暂时不可用，请稍后重试”；真正的“请重建索引”只在同步 `/api/kb/{id}/query` 的 `except RuntimeError` 分支里原样返回。排障时先看日志里的 `EmbeddingMismatch`。

### 5. 0.45 阈值与“依据不足”的口径

两个阈值是两件事：

- `RETRIEVE_MIN_SIM=0.25`：决定“这段资料丢不丢”。低于它，`select_diverse` 会砍；砍空时强留最相关一条。
- `RETRIEVE_LOW_SIM=0.45`：决定“留下来的这段够不够格当依据”。不丢，但 `strength` 降级，`weak=true`。

边界要精确记：

| 条件 | `strength` | `weak` |
|---|---|---|
| `best >= 0.45` | high | false |
| `0.25 < best < 0.45` | medium | **true** |
| `best <= 0.25` | low | true |
| 所有距离都换算不出 | high | false |
| 没有任何结果 | none | false |

注意 `select_diverse` 用 `sim >= min_sim` 保留，而 `assess_grounding` 用 `best > min_sim` 判 medium：`sim == 0.25` 会被保留，但判 low。`best == 0.45` 判 high。`none`（空结果）不算 weak——所以“没检索到”不会触发 `rag_note_low`，而是退回普通 LLM（见 2.4 的失败表）。

### 6. 单库隔离与跨库串味

- collection 名 `kb_{kb_id}`，`kb_id = md5(f"{owner}|{name}")[:12]`，owner 混进哈希；同名不同主是两个库。
- `pipelines.get_or_create(owner, kb_id)` 先 `owner_of(kb_id) != owner` 就抛 `NotOwned`；`api/kb.py` 每个入口先 `_must_own`，不是自己的库统一 404。
- 缓存 key 含 `collection` 和 `owner`；BM25 缓存按 collection；`_SOURCES_CACHE` 按 collection。
- `RagPipeline.answer()`（同步 `/api/kb/{id}/query`）调用 `retrieve_with_score(question, top_k)` **不传 owner**，所以这条路径不享受画像偏置，也不做查询改写。它仍然受 `_must_own` 保护，不会跨库。
- 真正的“串味”风险不在正常路径，而在：`warmup` 不验归属（只打开 collection，不做用户读写）；`_get_or_create_raw` 不验归属（只给内部 create/warmup 用）；`_EMBED_CACHE` 全局按问题共享（同文本同模型，正常无害）。只要新增入口记得先 `_must_own` / `get_or_create(owner, ...)`，就不会跨库。

---

## 2.6 三层自检题（检索）

### 第一层：回忆

**Q1**：`top_k=3` 时，`fetch_k` 是多少？公式里三个候选值分别是什么？
**答对标准**：`max(3*3, 3+6, 12) = 12`，再和库总量取小；三个值是 `top_k*RETRIEVE_FETCH_MULT`、`top_k+6`、`RETRIEVE_MIN_FETCH`。

**Q2**：`_to_similarity(2.0)`、`_to_similarity(4.0)`、`_to_similarity(4.2)` 分别返回什么？
**答对标准**：`0.0`、`-1.0`、`None`。

**Q3**：`best=0.30` 时 `strength` 和 `weak` 是什么？`best=0.45` 呢？
**答对标准**：0.30 → medium、weak=true；0.45 → high、weak=false。

**Q4**：`_RETRIEVE_CACHE` / `_SOURCES_CACHE` / `_EMBED_CACHE` / `_BM25_CACHE` 的容量上限各是多少？
**答对标准**：200 / 64 / 500 / 8；BM25 负缓存 TTL 300s。

### 第二层：理解

**Q5**：为什么 `select_diverse` 要放在 `_apply_profile_bias` 之前？
**答对标准**：`select_diverse` 负责“成员选择”（丢离群、去重、截到 top_k），偏置只负责“已选成员的重排”。反过来先偏置，MMR 的多样性打分会把刚被偏置上来的条目又挤掉；而且偏置返回原始 `distance`，先偏置也不会改变 `select_diverse` 看到的距离，等于白做。

**Q6**：为什么指纹要含 `model + host + dim`，而不是只含 `model`？
**答对标准**：只含 model 会漏掉“同名模型换了供应商/host”的情况，而不同供应商的同名模型可能不是同一个语义空间；`dim` 用于捕捉维度不一致，但只在两边都有值时比较（老库可能没记）；`host` 只取域名，避免路径前缀变化造成假警报。

**Q7**：为什么 `assess_grounding` 只看 top1，不看平均分？
**答对标准**：一条强相关配几条噪声时，平均分会把整体拖成“检索质量差”，正常回答被平白加上示弱提示；top1 才代表“有没有一条能当依据”。

### 第三层：审查（故意写错的检索代码）

下面这段是从项目里“改坏”的版本，请找出至少 5 个问题并说明影响。答案在题后。

```python
def retrieve_with_score(self, question, top_k, owner=None):
    cached = _cache_get(self.collection_name, question, top_k)      # ①
    if cached is not None:
        return cached

    if not self._vs:
        self.get_or_open()
    total = self.count()

    vec = get_embeddings().embed_query(question)                    # ②
    vec_cands = self._vs.similarity_search_by_vector_with_relevance_scores(
        vec, k=top_k                                                # ③
    )

    if config.HYBRID_ENABLED:
        merged = self._hybrid_candidates(question, vec_cands, config.HYBRID_KEYWORD_TOP)  # ④
        if merged:
            result = merged                                        # ⑤
    else:
        result = vec_cands

    if owner:
        result = _apply_profile_bias(result, owner)                 # ⑥
    _cache_set(self.collection_name, question, top_k, result)       # ⑦
    return result
```

**参考答案与影响**：

1. **① 缓存 key 缺少 `owner`**：A 用户带画像偏置的排序结果会被 B 用户直接复用。影响：B 看到按 A 的兴趣排的引用；跨用户串结果。
2. **② 没有 `_embed_query`，直接 `get_embeddings().embed_query`**：同题并发各发一次 embedding，失去 LRU 和 single-flight。影响：费用与延迟上升，峰值时更容易撞 429。
3. **③ `k=top_k` 不扩召回**：相邻重复切片占满名额，MMR 没有候选可挑。影响：top-3 可能是同一段话的三个重叠切片，真正相关的第 4 条进不来。
4. **④ 没有 `try/except` 包住 `_hybrid_candidates`**：BM25 建索引失败（大库、读库异常）会让整个检索 500，而不是退回纯向量。影响：本可降级为纯向量，现在直接失败。
5. **⑤ `result = merged` 直接使用，没经过 `select_diverse`**：没有离群过滤、没有 MMR、没有截到 `top_k`。影响：低分尾巴和重复片段全部进入 prompt；`merged` 可能比 `top_k` 多很多条。
6. **⑥ 没有检查 `config.RETRIEVAL_BIAS_ENABLED`**：用户画像存在时，即使总开关关掉也会偏置。影响：排障时关不掉偏置，行为与配置不一致。
7. **⑦ 缓存 key 缺少 `owner`**：同 ①，写入时也串。
8. **没有 `embed_guard.check`**：换模型后仍用旧模型缓存/旧库检索，返回近似随机的结果。
9. **没有 `total == 0` 检查**：空库时 `fetch_k` 可能小于 1 或向 Chroma 要 0 条，报错信息不可读；项目明确抛“知识库为空”。
10. **没有 `top_k <= 1 or mult <= 1` 分支**：`top_k=1` 时仍算 `fetch_k`，多捞没有意义；如果 `mult=0`，`fetch_k` 可能变成 12（因为 `MIN_FETCH`），与“关掉候选池”的语义不一致。

**答对标准**：找出至少 5 个，且能按“缓存 → 召回 → 融合 → 精排 → 偏置”的顺序说明影响；只说“少了 try”不算完整，要说出“BM25 失败会让整个检索失败而不是降级”。

---

## 2.7 小实验：把 `RETRIEVE_LOW_SIM` 从 0.45 调到 0.35（30–60 分钟）

**目标**：观察 `strength / weak` 的变化，以及“拒答率”是否下降。**不改源码**，用环境变量和纯函数调用。

### 第一步：纯函数验证（10 分钟）

在项目根目录运行：

```python
import sys
sys.path.insert(0, r"c:\biancheng\agent-hub")
from langchain_core.documents import Document
from core import config
from kb.rag_core import assess_grounding

def ds(sims):
    # 反推距离：cos = 1 - d/2  =>  d = 2*(1-cos)
    return [(Document(page_content=f"doc{i}"), 2 * (1 - s)) for i, s in enumerate(sims)]

for low in (0.45, 0.35):
    config.RETRIEVE_LOW_SIM = low
    print("low =", low, assess_grounding(ds([0.42, 0.30, 0.26])))
```

**预期**：

| 阈值 | top1 | strength | weak |
|---|---|---|---|
| 0.45 | 0.42 | medium | true |
| 0.35 | 0.42 | high | false |

**关键理解**：`assess_grounding` 只看 `best=max(sims)`，所以阈值调整只在“top1 跨过阈值”时才会翻转；第二、三条的相似度不影响判定。如果换成 `[0.50, 0.30, 0.26]`，两个阈值下都是 high，观察不到变化——这不是实验失败，是判定口径。

### 第二步：真实链路验证（30–40 分钟）

1. 用环境变量启动服务，不修改 `.env` 文件：

```powershell
$env:RETRIEVE_LOW_SIM="0.35"
uvicorn main:app --port 8000
```

（如果项目入口不是 `main:app`，按仓库实际启动命令替换。）

2. 准备一个小知识库：上传一份 2~3 页的 `考研数学.md`，确保里面有“极限”“复习顺序”等段落。
3. 固定问 5 个问题，其中至少 3 个是“沾边但不对口”的（例如“怎么复习考研英语”“高数教材哪本好”“概率论怎么安排时间”）。
4. 对每个问题记录 SSE 里的 `grounding` 帧（`strength / best / weak`），以及最终回答是否出现“知识库里没有直接相关资料 / 只能找到沾边内容”这类示弱话术。

**记录表（预期 vs 实际）**：

| 问题 | top1 预期区间 | 0.45 下 weak | 0.35 下 weak | 回答是否示弱（0.45） | 回答是否示弱（0.35） |
|---|---|---|---|---|---|
| Q1 对口问题 | ≥0.45 | false | false | 否 | 否 |
| Q2 沾边问题 | 0.35~0.44 | true | false | 是 | 否 |
| Q3 不相关 | ≤0.25 | true | true | 是 | 是 |
| Q4 边缘 | 0.26~0.34 | true | true（仍 low/medium） | 是 | 是 |
| Q5 对口问题 | ≥0.45 | false | false | 否 | 否 |

**判据**：

- 0.35 下，Q2 的 `weak` 应从 `true` 变 `false`；Q3/Q4 不变；Q1/Q5 不变。
- 如果 5 个问题的 `weak` 全都不变，先检查 top1 是否都远离 0.35~0.45 区间；不要急着下“阈值没用”的结论。
- 如果 0.35 下示弱话术明显减少，但用户反馈“答案变自信了”，这说明降低阈值是在用“少示弱”换“多硬答”——这正是 `RETRIEVE_LOW_SIM` 的产品取舍，不是纯技术参数。

**预期 vs 实际**（自己填）：

| 指标 | 0.45 | 0.35 | 结论 |
|---|---|---|---|
| weak=true 的问题数 | 预期 3/5 | 预期 2/5 | |
| 回答里出现示弱话术的问题数 | 预期 3/5 | 预期 2/5 | |
| 明显硬答（资料不对口却给确定答案）的问题数 | 预期 0~1/5 | 预期 1~2/5 | |

实验结束后记得清掉环境变量（新开终端即可），避免影响后续启动。

---

# 附录

## A. 参数速查

| 参数 | 默认值 | 位置 | 作用 | 调大/调小的后果 |
|---|---|---|---|---|
| `CHUNK_SIZE` | 500 | `core/config.py#L42` | 切片字符数 | 调大：片段更长、上下文更全，但检索粒度变粗；调小：更精细，但片段可能断句 |
| `CHUNK_OVERLAP` | 50 | `#L43` | 相邻切片重叠字符数 | 调大：边界更完整，但重复更多、存储更大 |
| `TOP_K` | 3 | `#L44` | 默认返回条数 | 调大：资料更多，但 prompt 更长、噪声更多 |
| `TOP_K_MAX` | 20 | `#L46` | 请求上限 | 防止超大 top_k 拉爆检索与费用 |
| `EMBED_BATCH_SIZE` | 10 | `#L49` | 单次 embedding 条数 | 超过百炼上限会 400/429 |
| `EMBED_CONCURRENCY` | 4 | `#L53` | 入库并发批数 | 调高可能更快，但容易撞 429；1 = 串行 |
| `EMBED_TIMEOUT` | 30 | `#L75` | embedding 超时秒数 | 调小：弱网易失败；调大：故障时等更久 |
| `EMBED_MAX_RETRIES` | 2 | `#L76` | embedding 重试次数 | 调大：更稳，但费用与延迟增加 |
| `RETRIEVE_FETCH_MULT` | 3 | `#L80` | 扩召回倍数 | 调大：候选更多、精排更慢；调小：重复片段容易占名额 |
| `RETRIEVE_MIN_FETCH` | 12 | `#L82` | 候选池下限 | 小 top_k 时也保证有得挑 |
| `RETRIEVE_MMR_LAMBDA` | 0.65 | `#L84` | 相关度权重 | 1 = 纯相关度；越小越强调多样性 |
| `RETRIEVE_MIN_SIM` | 0.25 | `#L88` | 离群过滤阈值 | 调高：丢更多弱片段，可能误杀；调低：保留更多噪声 |
| `HYBRID_ENABLED` | 1 | `#L93` | 是否开 BM25 | 0 = 纯向量 |
| `HYBRID_RRF_K` | 60 | `#L95` | RRF 常数 | 调大：弱化头部名次差异；调小：更看重单通道第一 |
| `HYBRID_KEYWORD_TOP` | 20 | `#L97` | 关键词通道取前几条 | 调大：更多关键词候选；调小：专有名词召回变弱 |
| `HYBRID_MAX_DOCS` | 20000 | `#L99` | 超过则放弃 BM25 | 调大：大库内存压力；调小：更早退回纯向量 |
| `QUERY_REWRITE_ENABLED` | 1 | `#L104` | 是否改写追问 | 0 = 关闭，省一次 LLM |
| `QUERY_REWRITE_MAX_LEN` | 8 | `#L106` | 判定追问的长度 | 调大：更多问题被改写，成本上升 |
| `RETRIEVE_LOW_SIM` | 0.45 | `#L122` | 依据强度高阈值 | 调低：更少示弱，更易硬答；调高：更多示弱 |
| `GROUNDING_NOTICE` | 1 | `#L126` | 是否追加示弱提示 | 0 = 一键回退到改造前 |
| `RETRIEVAL_BIAS_TOP` | 8 | `core/config.py#L333-L335` | 画像标签取前几个 | 调大：偏置更强，也更容易过拟合 |
| `RETRIEVAL_BIAS_ALPHA` | 0.08 | 同上 | 每个命中标签减多少距离 | 调大：偏置更强；过大可能把不相关片段顶上来 |
| `DAILY_EMBED_LIMIT` | 1000 | `#L159` | 每日切片数上限 | 调大：账单风险；调小：大文件传不完 |

## B. 术语表

| 术语 | 白话 |
|---|---|
| embedding / 向量化 | 把一段文本变成一串数字，语义相近的文本数字也相近 |
| 余弦相似度 | 两个向量夹角的余弦，1 = 方向完全一致，0 = 无关，-1 = 相反 |
| 平方 L2 距离 | 两个向量差的平方和；单位向量上 `d = 2 - 2cos` |
| 召回 | 从库里“捞”出一批候选，不保证排得对 |
| 精排 / rerank | 对候选重新排序、过滤、去重，选出最终几条 |
| BM25 | 按词面命中打分的经典关键词检索算法，词越稀有、命中越多分越高 |
| RRF | Reciprocal Rank Fusion，按“排名”融合多路结果，`Σ 1/(K+rank)` |
| MMR | Maximal Marginal Relevance，既看相关度又看与已选内容的重复度 |
| 离群过滤 | 把相似度低于阈值的候选直接丢掉 |
| grounding / 依据强度 | 判断检索结果够不够格当答案依据，分 high / medium / low / none |
| 指纹 | 记录“这批向量是哪个模型、哪个 host、多少维算的”，用于防换模型后静默错乱 |
| single-flight | 同一个请求并发进来时只发一次，其他等结果复用 |

## C. 一页纸调用链

```text
入库：
api/kb.py upload_and_build
  → _must_own → quota.guard → 校验 → save_stream → quota.consume
  → _build（线程 + kb_lock）
      → build_index → load_document → split_documents → chunk_index
      → _embed_and_store → _inject_topics → embed_documents（10/批，4 并发）
      → upsert → embed_guard.stamp → _cache_invalidate
      → add_file_records（finally）→ retain_file_records（rebuild）
  → quota.bump_usage → 返回

检索（流式）：
api/chat.py RAG 分支
  → _retrieval_query（rewrite_query → enhance_for_retrieval）
  → retrieve_with_score（embed_guard.check → cache → embed_query → 向量扩召回
      → BM25 → RRF → select_diverse（离群 + MMR）→ 画像偏置 → cache）
  → build_sources → SSE sources
  → assess_grounding → SSE grounding
  → _build_messages（SYSTEM_PROMPT + grounding_note + 参考资料）
  → _stream_llm → SSE token → done
```

**读这份文档的顺序建议**：先读第一部分 1.1–1.3，把“一条资料怎么进库”跑通；再读第二部分 2.1–2.3，把“一个问题怎么变成几条依据”跑通；最后用 2.4 的端到端表把两段接起来。自检题不要只看答案，先自己在纸上写一遍，再对照“答对标准”。
