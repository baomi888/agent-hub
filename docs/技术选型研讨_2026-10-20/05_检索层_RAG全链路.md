# 卷五 · 检索层：RAG 全链路

> 本卷解决的问题：**如何从一堆切片里，挑出"够格当依据"的那几条，并且在不够格的时候诚实地承认。**
> 这一层是整个系统里"技术的深度"最集中的地方，因为检索错误几乎全部是静默的——不会报错，只会让回答变得似是而非。

---

## 5.0 为什么这一层最难

先明确难度来源。

**生成层的错误是显性的**：模型胡说，用户能看出来（"这个答案跟我的问题没关系"）。

**检索层的错误是隐性的**：

- 检索到 3 条片段，2 条不相关 → 模型仍然会"基于这 3 条"给出一个通顺的回答 → 用户看不出哪里错了；
- 检索到 3 条片段，全是同一段话的相邻切片 → 模型以为"有 3 个来源支持"，实际只有 1 个 → 用户看到 `[1][2][3]` 三个角标，以为是三个独立依据；
- 检索不到任何相关片段 → 模型仍然会顺着"沾边的"内容编一个连贯答案 → 用户满意离开，学到的却是错的。

第三种最危险。项目用户调研（56 份）里"答案不准还装自信"占 **83.9%**，是压倒性第一担忧（`core/config.py:103`）。

所以这一层的设计目标不是"检索更准"，而是：

> **在检索不准的时候，让系统知道"我不准"，并且让用户知道。**

这个目标决定了后面所有的结构：四段流水线（为了"准"）、置信度分级（为了"知道不准"）、grounding note（为了"告诉用户"）。

---

## 5.1 Chroma：候选面与被否决的理由

### 候选面

| 方案 | 形态 | 优势 | 在本项目的问题 |
|---|---|---|---|
| **Chroma（PersistentClient）** | 嵌入式（进程内），sqlite + hnsw 索引 | 零运维、零额外进程、支持 metadata 过滤、pip 装完即用 | 单进程写入有锁竞争；不支持复杂的聚合查询（无 GROUP BY）；1.x 与 0.x API 不兼容 |
| **FAISS** | 纯库，索引文件 | 性能最强、算法最全 | **无 metadata 过滤**（要么自己建 id→meta 映射，要么用 IDSelector）；无持久化封装；无删除语义 |
| **pgvector** | PostgreSQL 扩展 | 与业务数据同库，可 JOIN | 需要 PostgreSQL（2G 内存跑不动） |
| **Milvus** | 独立服务（Go + etcd + MinIO） | 大规模、分布式 | 至少 3 个容器，2G 内存直接不可能 |
| **Qdrant** | 独立服务（Rust） | 性能好、过滤强、单二进制 | 多一个进程（约 100~200MB）；本项目不需要它的性能 |
| **Weaviate** | 独立服务 + 内置向量化模块 | 功能全（混合检索内建） | 同上，且更重 |
| **sqlite-vec / 手写暴力检索** | 扩展或纯 Python | 极简 | 无 ANN 索引，万级以上片段线性扫描太慢 |

### 判据

在本项目的约束场下（卷一 §1.2），区分度最高的三个维度是：

1. **是否引入额外进程**（内存约束）
2. **是否支持 metadata 过滤**（每库隔离、按来源删文件都依赖它）
3. **删除语义是否清晰**（知识库要支持"删掉某个文件"）

### 逐项对照

**第 1 点直接淘汰**：Milvus、Qdrant、Weaviate（都要独立进程）。pgvector 淘汰（要 PG）。

剩下 Chroma 与 FAISS。

**第 2 点决定性地淘汰 FAISS**。

本项目需要的过滤是：

```python
# 按来源删文件
self._vs._collection.get(where={"source": source})
# 判断文件是否已入库
self._vs._collection.get(where={"source": source}, limit=1, include=[])
# 按主题标签过滤（预留）
{"topics": {"$contains": "|数学|"}}
```

FAISS 的 `IDSelector` 只支持"按 id 集合选"，要按 `source` 选，必须：
1. 维护一个 `source → [id]` 的外部索引；
2. 该索引要与 FAISS 索引保持一致（删除时同步更新）；
3. 崩溃后要能从磁盘恢复这个映射。

**这等于自己实现一遍 metadata store**，而这正是 Chroma 已经做的事。用 FAISS 会得到一个"性能更好但代码多 500 行、且多一个一致性风险"的系统——在本项目的性能需求下（万级片段、3 并发），这个交易完全不划算。

**第 3 点**：Chroma 的 `collection.delete(ids=[...])` 语义清晰，且删 collection 就是删一个目录。FAISS 需要自己管理 `index.remove_ids()` 与磁盘文件。

### 决策

Chroma `PersistentClient`，进程级单例，每库一个 collection。

### 代价 1：单例是必须的，不是优化

`kb/rag_core.py:41-57` 的注释给了实测数据：

> 实测：new 一个 PersistentClient 首次 0.588s、之后稳定 0.098s/次（重开 sqlite + 重建内部缓存）。而 exists / list_all / get_status / peek 这几个"只读一下"的接口每次调用都新建了一个——它们还全在 async 端点里，等于每条请求都要卡住事件循环几十到几百毫秒。

首次 588ms 是一个足以让"打开知识库面板"明显卡顿的数字。而 `peek()`（看库存在不存在）如果每次新建 client，就是每 100ms 一次无用功。

单例的副产品："多个 client 指向同一个 sqlite 文件的锁竞争隐患也一并消掉。"

### 代价 2：`_ensure_vs` 必须显式传 `client`

```python
# kb/rag_core.py:1005-1008
# ⚠️ 必须传 client=chroma_client()。不传的话 langchain 会按 persist_directory
# 自己 new 一个 PersistentClient —— 单例就白做了（实测 163.5ms vs 73.1ms，
# `client is 单例` 判 False）
```

这是一个**极易踩的坑**：`Chroma(persist_directory=..., embedding_function=...)` 看起来是"打开这个目录的库"，实际上它会内部 new 一个 client。单例在这里被静默绕过。

而且症状是"慢"，不是"错"——又一个静默失败。所以 `tests/test_chroma_singleton.py` 存在，它断言的是 `client is 单例`。

### 代价 3：版本锁

```
langchain-chroma==0.2.3
chromadb==0.5.23
```

注释说"langchain-chroma 1.x 要 chromadb 1.x（沙箱限制无法安装）"。这个"沙箱限制"很可能指的是 Python 版本或编译依赖。后果是 README 明确写了"手上没有 Python 3.12 会装不上"。

**这是一条跨越四层的依赖链**：代码 → langchain-chroma 0.2.3 → chromadb 0.5.23 → Python 版本。任何一层变动都会锁死。

### 代价 4：没有 GROUP BY

`list_sources()` 要统计"每个来源文件有多少片段"。SQL 里这是一句 `GROUP BY`，但 Chroma 没有。只能：

```python
offset = 0
while True:
    got = self._vs._collection.get(include=["metadatas"], limit=SOURCES_PAGE, offset=offset)
    ...
```

分页遍历整个库的 metadata，在 Python 里 `Counter` 累加。

注释承认了这一点：

> Chroma 没有 distinct/GROUP BY，只能遍历元数据做聚合，无法真正免扫描；因此这里做两件事把代价压下来：1) 分页拉取（每页 1000 条）——峰值内存只跟页大小有关；2) 结果按库缓存。

**"峰值内存与库大小解耦"** —— 这句话是关键。一次性拉 100 万条 metadata 会吃掉几十 MB；分页拉 1000 条只占几十 KB。**在 2G 机器上，这是能不能跑的区别。**

### 反转条件

- 片段数超过 100 万，或需要多机部署 → 换 Qdrant 或 Chroma Server 模式；
- 需要与业务数据 JOIN（比如"只检索这个用户最近 7 天上传的文档"）→ pgvector 更自然；
- 合规要求元数据可审计 → 关系库 + 向量库双写。

---

## 5.2 每库一个 collection，而不是一个大库加 filter

### 两种设计

| 设计 | 隔离方式 | 删库 | 跨库检索 |
|---|---|---|---|
| **每库一 collection** | 物理隔离（不同 sqlite 目录段/不同索引） | `delete_collection()` 一条命令 | 需要遍历 N 个 collection |
| **单 collection + `where: {kb_id: ...}`** | 逻辑隔离（同一个 hnsw 索引，filter 过滤） | 遍历删除该 kb_id 的所有 id | 天然支持（不 filter 就行） |

### 本项目选物理隔离

理由在代码里没明说，但从使用方式可以反推：

**① 删库要彻底。**

`pipelines.py` 的 `delete` 逻辑是"删 collection + 清注册表 + 清文件清单"。物理隔离下 `delete_collection` 是原子的（删目录），不会留下"孤儿向量"。

如果用单 collection + filter，删除需要：
1. 查出该 kb_id 的所有 id（分页，因为可能上万条）；
2. 分批 `delete(ids=[...])`；
3. 中途失败会留下部分数据，且无法回滚。

**② 重建整库要干净。**

`build_index(mode="rebuild")` 会先 `delete_collection` 再重建。物理隔离保证"换切片参数后完全重建，不会混入旧参数切出来的片段"。

如果用 filter，重建时要先删所有旧片段——同样的问题。

**③ 索引参数可以不同。**

不同知识库可以用不同的 `hnsw:space`、不同的距离度量。虽然当前都统一，但物理隔离保留了这个自由度。

**④ 归属校验天然分层。**

`owner_of(kb_id)` 校验 + collection 名含 owner 哈希 = 双重隔离。collection 名本身就是 `md5(owner|名称)[:12]`，所以即使拿到别人的 kb_id，也要过 `kb_owners` 表那一道。

### 代价

**① 跨库检索不支持。**

如果要"在所有知识库里搜"，需要遍历 N 个 collection，每个都做一次检索，再合并排序。N 大时延迟线性增长。

当前产品形态是"会话绑定一个库"，所以不需要跨库检索。但如果将来要做"全局搜索"，这是架构级的改动。

**② collection 数量无上限。**

每个 collection 在 sqlite 里有自己的段。Chroma 在 collection 数很多时（数百个）启动/列举会变慢。当前用户量下不是问题。

**③ 缓存按 collection 分片。**

`_RETRIEVE_CACHE` 的 key 是 `(collection_name, question, top_k, owner)`，`_BM25_CACHE` 是 `OrderedDict[collection_name]`（最多 8 个）。多库时缓存命中率下降。

**反转条件**：需要跨库检索，或 collection 数超过 200。

---

## 5.3 切片：500/50 与中文分隔符

### 参数

```python
CHUNK_SIZE_DEFAULT = 500
CHUNK_OVERLAP_DEFAULT = 50
```

### 分隔符表（`kb/rag_core.py:918-923`）

```python
RecursiveCharacterTextSplitter(
    chunk_size=chunk_size,
    chunk_overlap=chunk_overlap,
    separators=["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""],
    length_function=len,
)
```

**这张表是全项目最"中文特化"的一处。**

`RecursiveCharacterTextSplitter` 的默认分隔符是英文的 `["\n\n", "\n", " ", ""]`。用它切中文，结果是：

- 中文段落通常**没有空格**；
- 所以要么按 `\n` 切（段落级，粒度太粗），要么按字符硬切（破坏语义）；
- 硬切会把"矩阵的秩"切成"矩阵的"+"秩"。

加了中文标点之后，切分优先级变成：

```
段落 → 换行 → 句号 → 感叹号 → 问号 → 分号 → 逗号 → 空格 → 字符
```

这是**从粗到细的语义边界递降**：先尽量在段落边界切，切不动就在句子边界切，最后才硬切。

### 为什么 500

考虑三个约束：

1. **embedding 的语义粒度**：500 中文字符约等于 350~500 token，足够表达一个完整论点。太小（100）会失去上下文，太大（2000）会让检索精度下降（一个片段里混了多个主题，向量被"平均"掉）。
2. **top_k=3 时的上下文预算**：3 × 500 = 1500 字符，加上 system prompt 和历史，总共约 3000~4000 token，远低于模型上限。
3. **检索的"定位精度"**：用户点开角标要看到"原文片段"。500 字是一个屏幕能看完的量。如果片段是 2000 字，用户要在里面找自己关心的那句。

### 为什么 overlap=50

10% 的重叠。作用是**防止语义在边界被切断**：

> 原文："...矩阵的秩定义为非零子式的最大阶数。秩为 r 的矩阵..."
> 切成："...矩阵的秩定义为非零子式的最大阶数。" | "秩为 r 的矩阵..."
> 如果问题问"秩的定义是什么"，第二个片段命中但缺少"定义"这个词。

有 50 字重叠，第二个片段会带上"...最大阶数。秩为 r 的矩阵..."，保留了上下文。

### 但重叠带来一个新问题

**相邻片段高度重复**。这就是 MMR 要解决的事（§5.6）——`_shingles` 的注释说得很直白：

> 切片重叠 50 字时，相邻片段的 bigram 重合度会非常高，正好是我们要去重的对象。

**设计是成对的**：重叠是为了不切断语义，去重是为了不让重叠占满名额。只做一个会出问题。

### 一个未做的优化

当前切片是**纯字符级**的，不理解文档结构。对于 Markdown，理想做法是：

- 按标题层级切（`# 一级` / `## 二级`）；
- 保留标题作为每个片段的前缀（"第3章 > 3.2 矩阵的秩 > ..."）。

这样每个片段的向量会带层次信息，检索"第3章的秩"会更准。

当前 `.md` 按纯文本加载（`SUPPORTED_EXT` 的注释："`.md` 按纯文本加载，Markdown 语法不影响切片"）。这是一个**刻意的简化**，代价是丢失了结构。

反转条件：如果用户大量上传结构化文档（教材、规范），应该做结构感知切片。收益是检索准确率，成本是切片器复杂度。

---

## 5.4 距离口径：一个必须完整复原的事故

这是本报告最重要的一节。它示范了"默认值陷阱 + 静默失败 + 只影响排序不影响功能"这三者叠加时，问题有多难发现。

### 事实基础

**事实 1**：Chroma 的 collection 在创建时如果没有指定 `hnsw:space`，默认是 **`l2`（平方 L2）**，不是 cosine。

**事实 2**：阿里百炼 `text-embedding-v3` 返回的向量**已经是单位向量**（L2 范数为 1）。代码注释说"本地库 11 条实测 L2 范数全为 1.0000"。

**事实 3**：`similarity_search_by_vector_with_relevance_scores` 返回的第二个值是 **distance**（越小越相关），不是 similarity。

### 数学推导

平方 L2 距离：

```
d = ||a - b||² = Σ(aᵢ - bᵢ)²
  = Σaᵢ² - 2Σaᵢbᵢ + Σbᵢ²
  = ||a||² - 2a·b + ||b||²
```

当 `||a|| = ||b|| = 1`：

```
d = 1 - 2cos(θ) + 1 = 2 - 2cos(θ)
```

所以：

```
cos(θ) = 1 - d/2
```

**值域**：cos ∈ [-1, 1] → d ∈ [0, 4]。

### 错误版本

之前的代码假设"Chroma 返回的是余弦距离"，即 `d = 1 - cos`，值域 [0, 2]，换算公式 `cos = 1 - d` 或类似。

守卫条件写的是 `if d < 0 or d > 2: return None`。

### 三个后果（按严重程度排序）

**后果 1：阈值过滤被整条跳过。**

`select_diverse` 里的逻辑：

```python
scored = [(c, _to_similarity(_dist(c))) for c in cands]
vec_sims = [sim for c, sim in scored if _dist(c) is not None]
if vec_sims and all(sim is not None for sim in vec_sims):
    kept = [c for c, sim in scored if _dist(c) is None or sim >= min_sim]
```

关键在 `all(sim is not None for sim in vec_sims)`。

如果候选里**有一条**的 distance > 2（即 cos < 0，完全不相关），`_to_similarity` 返回 `None`，于是 `all(...)` 为假，**整个过滤块被跳过**。

后果：`RETRIEVE_MIN_SIM=0.25` 这道阈值**完全没有生效**。所有候选（包括完全不相关的）都进入 MMR 环节。

**这是一个"一条坏数据废掉整个机制"的设计缺陷**：用 `all()` 做"全部可换算才过滤"的判断，导致一条异常就把整批放行。

注释里写明了这一点，并说明为什么保留这个语义：

> 换算不成立时（例如换了没归一化的 embedding 模型）仍返回 None 让调用方跳过过滤——宁可少滤几个，也不能把相关片段误杀。

所以这个 `all()` 是有意的保守设计。但它与错误的阈值范围叠加后，从"保守"变成了"完全失效"。

**后果 2：相关度排序被反转。**

```python
def relevance(c) -> float:
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
```

对于 `d > 2` 的候选，`sim is None`，走"批次内相对排序"分支：`1 - (d - lo) / span`。

这意味着：**距离最大的那条得到 0，距离最小的得到 1**。看起来是对的（相对排序）。

但问题是：**相对排序与绝对相似度被混在一起用了。**

候选 A：d=0.5（sim=0.75）→ relevance=0.75
候选 B：d=3.0（sim=-0.5）→ relevance = 1 - (3.0-lo)/span

假设 lo=0.5, hi=3.0，span=2.5，则 B 的 relevance = 1 - (3.0-0.5)/2.5 = 0。

这时候排序还是对的。但如果批次里**全是** d>2 的（都不相关），则 lo=2.2, hi=3.0，最"不相关"的那条（d=2.2）会得到 relevance=1.0 —— **完全不相关的片段拿到了最高分**。

注释里说得很准：

> ② `relevance()` 退化为批次内相对排序 → 负余弦片段反而拿到虚高的相关度，把真正相关的片段挤出名额。

这个 bug 的阴险之处：**它只在"候选里混有正余弦和负余弦"时发作**。如果全正或全负，排序看起来都正常。所以它不会稳定复现，取决于具体问题。

**后果 3：前端显示了假的相似度。**

`build_sources` 把 `sim` 放进 `score` 字段，前端标签是"相似度"。

错误的守卫下，`d=1.089` 不在 [0,2] 之外（1.089 < 2，通过守卫），于是 `sim = 1 - 1.089/2 = 0.4555`？不对——错误的公式是 `cos = 1 - d`（假设余弦距离），则 `sim = 1 - 1.089 = -0.089`。

但线上实际显示的是 `1.089 / 1.160 / 1.237`。这说明**当时连换算都没做，直接把 distance 当 similarity 显示了**。

注释："线上表现：参考资料卡片把类目「相似度」标成 1.089 / 1.160 / 1.237（实为距离）。"

相似度大于 1 显然不可能，但用户不会去质疑——他们只会觉得"这个分数好高，应该很相关"。**这是最坏的一种错误：错误的方向是"让用户更相信"。**

### 修复

```python
def _to_similarity(distance: float) -> float | None:
    if distance is None or distance < 0 or distance > 4:
        return None
    return 1 - distance / 2
```

值域改成 [0, 4]，公式改成 `1 - d/2`。

### 回归测试的写法（这是最值得学的部分）

`tests/test_retrieve_select.py` 的注释（在 `rag_core.py` 里被引用）：

> 把守卫改成 [0,2] 重跑 tests/test_retrieve_select.py，用例 3 与 3b 都会失败，改回 [0,4] 即 ALL PASS——这就是这两条后果的直接证据。

这是**负向验证**：不只断言"正确的实现通过"，还断言"错误的实现会失败"。

为什么这很重要？因为很多测试只验证"正确路径"，这样的测试无法区分"我的实现是对的"和"我的测试没覆盖到错误"。

负向验证的做法是：
1. 故意改坏一处；
2. 跑测试；
3. 确认**预期的用例**变红；
4. 改回来；
5. 确认全绿。

第 3 步是关键：如果改坏了但测试还是绿的，说明测试没覆盖到。

### 三条可迁移的教训

1. **任何"距离→相似度"的换算，必须把合法值域写死在代码里，并配一条负向回归。** 因为默认值（`hnsw:space`）是隐式的，没人会去读 Chroma 源码。
2. **`all()` 做批量判定是危险模式。** 一条异常数据会让整个机制失效。更好的写法是"逐条判定，异常的跳过并计数"，而不是"全部正常才执行"。
3. **展示层不能直接复用内部数值。** distance 与 similarity 是两个方向的量，一旦在 UI 上标错，错误方向恰好是"更可信"，用户无从发现。

---

## 5.5 四段流水线：为什么不能直接取 top-k

### 直接取 top-k 的两个毛病（`rag_core.py:405-407`）

> 原来直接取 top-k 有两个毛病：相似度再低的片段也照样进 prompt（模型被迫硬答），以及相邻切片的重叠部分会把名额占满（三条里两条是同一段话）。

### 完整流程

```
输入: 候选列表 [(Doc, distance)]，top_k
  ↓
① 离群过滤：丢掉 sim < MIN_SIM(0.25) 的
   └ 但如果全被丢掉，至少保留最相关的一条
  ↓
② 候选数 ≤ top_k？直接返回（MMR 没意义）
  ↓
③ MMR 贪心选择：
   - 第一条固定取最相关（给多样性比较一个基准）
   - 之后每次选 max(λ·rel - (1-λ)·dup)
     λ=0.65, dup = 与已选集合的最大 bigram Jaccard
  ↓
④ 截到 top_k
  ↓
输出: 按"先选中在前"排序
```

### ① 离群过滤的"至少留一条"设计

```python
kept = [c for c, sim in scored if _dist(c) is None or sim >= min_sim]
# 别把结果砍空：一条不剩时至少留最相关的那条，让模型自己说"资料里没有"
cands = kept or [min(cands, key=...)]
```

为什么留一条？因为"空结果"会让系统走到另一个分支（`if not docs_scores: return "知识库中未检索到相关内容。"`），那是一个**硬拒答**。

而留一条最相关的（哪怕相似度只有 0.1），配合 `grounding_note` 的"不许硬答"约束，模型会给出一个**有信息量的示弱回答**：

> "这个知识库里没有和这个问题直接相关的资料。最接近的一段讲的是……，但它和你的问题并不完全对应。"

对比硬拒答："知识库中未检索到相关内容。" 后者没有信息量，用户不知道是"没这个资料"还是"系统坏了"。

**这是一个产品级的判断：宁可让模型看到一条弱资料然后示弱，也不要让它看到空白然后冷拒。**

### ③ MMR 的实现细节

标准 MMR（Maximal Marginal Relevance）：

```
MMR = argmax[ λ · Sim(q, dᵢ) - (1-λ) · maxⱼ Sim(dᵢ, dⱼ) ]
```

本项目的实现（`rag_core.py:493-502`）：

```python
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

**三个值得注意的点**：

**a) `relevance` 用"绝对余弦相似度"，不用"批次内 min-max 归一化"**

```python
def relevance(c) -> float:
    ...
    sim = _to_similarity(d)
    if sim is not None:
        return sim
```

注释解释了为什么：

> 优先用融合 rank（混合检索），其次用绝对余弦相似度，而不是批次内 min-max 归一化——被重叠切片塞满时，几条候选的距离往往只差 0.0x，归一化会把这点差距放大成"最相关 vs 最不相关"，结果重复片段靠微弱优势把名额全占了。

这是一个精妙的观察。举例：

```
候选 A: d=0.500 → sim=0.750
候选 B: d=0.505 → sim=0.7475
候选 C: d=0.510 → sim=0.745
```

min-max 归一化后：A=1.0, B=0.5, C=0.0。**实际差距只有 0.005，被放大成 1.0。**

如果 A 和 B 是相邻切片（高度重复），MMR 会算 `dup` 来惩罚 B。但如果 `rel` 的差距被放大到 1.0 vs 0.5，`0.65 × 1.0 - 0.35 × 0.9 = 0.335` vs `0.65 × 0.5 - 0.35 × 0.1 = 0.29`——A 仍然胜出，但差距很小。

而在绝对相似度下：`0.65 × 0.750 - 0.35 × 0.9 = 0.1725` vs `0.65 × 0.7475 - 0.35 × 0.1 = 0.4509`——B 明显胜出。

**绝对相似度让"多样性"这一项有真正的发言权。**

**b) `dup` 用字符 2-gram Jaccard**

```python
def _shingles(text: str) -> set[str]:
    t = re.sub(r"\s+", "", text or "")
    return {t[i:i+2] for i in range(len(t)-1)}

def _overlap(a, b) -> float:
    return len(a & b) / len(a | b)
```

为什么不用 embedding 余弦算文档间相似度？因为：
- 那需要为每个候选再算一次向量比较（候选有 12+ 条，两两比较是 O(n²) 次）；
- 字符 bigram 是 O(长度)，且不需要模型；
- 对"检测相邻切片重叠"这个任务，bigram 比语义相似度更准——因为相邻切片的重叠是**字面重复**，不是语义相似。

注释说得对："中文没有空格可分词，用字符 bigram 近似'两段话有多大重合'够用了。"

**c) 第一条固定取最相关**

```python
first = min(remaining, key=_first_key)
order.append(first)
```

为什么？因为 MMR 需要一个"已选集合"来算 `dup`。第一条没有已选集合，`max()` 会作用在空集上（ValueError）。所以固定取最相关的作为基准。

这保证了**最重要的一条一定在结果里**。

### ④ λ=0.65 的含义

`val = 0.65 × rel - 0.35 × dup`

λ 越接近 1，越偏向相关度；越接近 0，越偏向多样性。

0.65 意味着"相关度的权重是多样性的近 2 倍"。这是一个偏保守的值——优先保证相关性，只在两条片段相关度接近时才用多样性做区分。

`config.py` 的注释：

> MMR 相关度权重：1 = 完全按相关度排（等同旧行为），越小越强调多样性

**"1 = 等同旧行为"是一个重要的设计**：它给出了一个明确的回退点。如果 MMR 出问题，把 λ 设成 1 就退回纯相关度排序，不需要改代码。

### 代价

- MMR 是 O(top_k × n × len) 的（每轮遍历剩余候选，每条算 bigram 交集）。n=12、top_k=3、len=500 时，大约 18 次集合运算，每次 500 个元素——微秒级。可接受。
- bigram 集合的内存：500 字的片段约 500 个 bigram，12 条候选 = 6000 个字符串。每次检索都要构造。**这是可以优化的点**（缓存 shingles），但当前代价可忽略。

---

## 5.6 混合检索：BM25 + RRF

### 为什么纯向量不够

`rag_core.py:139-142`：

> 纯向量检索对专有名词 / 缩写 / 编号很弱：库里写着「CUDA 12.4」「第 3 条」，问「CUDA 12.4」时向量可能被语义相近但没这个词的片段挤掉。BM25 按词面命中，正好补这条。

这是 embedding 模型的固有特性：它把文本压成一个稠密向量，**稀有词的区分度被稀释**。

举例：

```
片段 A: "深度学习框架通常需要 CUDA 11.8 或更高版本"    ← 含 "CUDA 11.8"
片段 B: "GPU 加速库的版本兼容性是一个常见问题"          ← 语义相近但无具体版本
问题:  "CUDA 12.4 需要什么驱动？"
```

向量检索可能把 B 排在前面（因为"GPU 加速/版本兼容"与问题的语义更接近），但用户要的是"12.4"这个具体版本的信息。

BM25 会精确命中"CUDA"和"12.4"（如果分词正确），把 A 排上来。

### 为什么是 BM25，不是 TF-IDF

BM25 相比 TF-IDF 的改进：

1. **词频饱和**：TF-IDF 里 TF 线性增长，一个词出现 100 次就主导分数；BM25 用 `tf·(k1+1)/(tf + k1·(...))` 让 TF 增长饱和。
2. **文档长度归一化**：用 `b=0.75` 控制长文档的惩罚。

对于一个知识库里"有的片段 200 字、有的 1500 字"的场景，长度归一化很重要。

### 为什么不用 jieba 分词

```python
def _tokenize(text: str) -> list[str]:
    """中英混合分词：ASCII 取词、CJK 取字符 bigram。
    不引第三方分词器（jieba 会拖一串依赖，本项目一直避免）。
    字符 bigram 对中文专有名词/编号的召回已经够用，口径也和 _shingles 一致。
    """
    t = (text or "").lower()
    tokens = _ASCII_WORD.findall(t)          # [a-z0-9]+
    for run in _CJK_RUN.findall(t):          # 连续汉字
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i:i+2] for i in range(len(run)-1))
    return tokens
```

字符 bigram 的效果：

> "矩阵的秩" → ["矩阵", "阵的", "的秩"]

这会产生一些无意义的 bigram（"阵的"），但：
- 对于检索任务，"矩阵"和"的秩"这两个有意义的 bigram 已经足够定位；
- 无意义的 bigram 在所有文档里都出现，IDF 低，权重自动下降；
- **不需要词典**，不需要处理未登录词（"CUDA"这种）。

jieba 的代价：
- 一个纯 Python 包（约 20MB 含词典）；
- 启动时要加载词典（约 0.5~1s）；
- 对未登录词（新技术名词）仍会切错；
- 增加一个版本锁。

**判断：对于"补召回"这个辅助角色，bigram 足够。** 如果 BM25 是主检索通道，jieba 的收益才值得。

### RRF 融合：为什么不用加权求和

**问题**：向量距离是 [0, 4] 的连续值，BM25 分数是无上界的实数。直接加权求和需要：
1. 归一化两个分数（但归一化方法会影响排序）；
2. 调一个权重 α（但没有客观依据）。

**RRF（Reciprocal Rank Fusion）** 只用**排名**，不用分数：

```
score(d) = Σ_通道 1 / (k + rank_通道(d))
```

k=60（业界惯用值）。

实现（`_rrf_merge`）：

```python
for rank, (d, distance) in enumerate(vec_cands, start=1):
    did = d.id or f"v{rank}"
    fused[did] += 1.0 / (rrf_k + rank)
    dist[did] = distance
    by_id[did] = d
for rank, (idx, _score) in enumerate(kw_pairs, start=1):
    d = kw_docs[idx]
    did = d.id or f"k{rank}"
    fused[did] += 1.0 / (rrf_k + rank)
    dist.setdefault(did, None)     # 关键词独有 → 无向量距离
    by_id.setdefault(did, d)
```

**三个关键设计**：

**① 用 doc id 去重，不用内容**

`did = d.id`。Chroma 的每个片段有 uuid 作为 id。两条通道命中同一个片段时，id 相同 → 分数相加。

这依赖"向量通道和关键词通道用的是同一批文档对象"——确实是（`kw_docs` 来自 `vs._collection.get()`）。

**② `dist.setdefault(did, None)` —— 关键词独有片段没有距离**

这是后面很多 bug 的源头。一个片段只被 BM25 召回（向量通道没命中），它的 `distance` 是 `None`。

后续代码必须处理 `None`：

```python
# select_diverse
def _dist(c):
    return c[1]     # 可能是 None
...
vec_sims = [sim for c, sim in scored if _dist(c) is not None]   # 过滤掉 None
```

```python
# _bias_rerank
if distance is None:
    slots.append((d, None, None, idx))   # 无距离：占位待原样放回
```

注释里记了一次真实的事故：

> `distance is None` 是关键词通道独有的召回……（原先这里对 None 直接 float() 会抛 TypeError：只有"用户有画像标签 + 命中关键词独有片段"两个条件同时成立才会炸，线上是一条静默 500）。

**这是一个"条件组合型 bug"**：A（有画像标签）AND B（命中关键词独有片段）才触发。两个条件都不罕见，但同时发生概率低，所以能活到线上。

**③ 归一化到 (0, 1]**

```python
top = order[0][1] or 1.0
return [(by_id[did], dist.get(did), score / top) for did, score in order]
```

最高分的 rank 归一化为 1.0。这给了 `select_diverse` 的 `relevance()` 一个 [0,1] 的输入（与余弦相似度同量纲）。

**注意**：这个 `rank` 是"融合后的相对分数"，不是真实相似度。所以当混合检索启用时，`relevance()` 用的是 rank 而不是 sim：

```python
def relevance(c) -> float:
    r = _rank(c)
    if r is not None:
        return r
```

这意味着：**混合检索下，MMR 用的是相对排名，不是绝对相似度**。这与 §5.5 说的"用绝对相似度而非归一化"有点矛盾——但这是有意的：

- 纯向量检索：用绝对相似度（可比、有语义）；
- 混合检索：用融合 rank（因为两个通道的分数不可比，RRF 的结果本身就是一个相对量）。

而 `distance` 仍然被保留，用于后面的 `assess_grounding`（判定依据强度）和 `build_sources`（显示相似度）。

**这是一个"双轨数值"的设计：排序用 rank，展示与阈值判定用 sim。** 两套数值必须严格分离，否则会出现"排序靠前但显示相似度低"的矛盾。

代码里确实分离得很好：`_rank` 只用于 `relevance`，`_dist` 用于一切与 sim 相关的计算。

### 代价与闸门

**BM25 需要把整库载入内存**（算 IDF 和倒排表）。所以：

```python
HYBRID_MAX_DOCS = 20000
if len(texts) > config.HYBRID_MAX_DOCS:
    # 超过闸值：放弃关键词通道，退回纯向量，别把全文堆进内存
    _BM25_NEG[collection_name] = time.monotonic() + _BM25_NEG_TTL
    return None
```

20000 条 × 500 字 = 1000 万字 ≈ 20MB 文本 + 倒排表开销（约 2~3 倍）= **60~80MB**。在 2G 机器上，这是能承受的上限。

**负缓存（TTL 300s）** 的设计很关键：

> 超大库（超 HYBRID_MAX_DOCS）负缓存：返回 None 的库每次检索都会重新全库拉一遍才发现"还是太大"。记下短 TTL 的否定结果，期间直接放弃关键词通道。

没有负缓存，一个 3 万片的知识库每次检索都要**先拉 3 万条 metadata 才发现太大**——比不启用混合检索还慢。

**这是一个"失败的代价也要缓存"的模式**，很少见但很对。

### 反转条件

- 库的规模普遍超过 2 万片 → 应该上真正的全文索引（SQLite FTS5 或 Tantivy），而不是内存 BM25；
- 需要多语言分词 → 引 jieba 或 ICU；
- 需要更好的融合 → 学习型排序（但需要标注数据，本项目没有）。

---

## 5.7 三层缓存：省的是真钱

### 三层结构

| 缓存 | 键 | 容量 | 失效时机 | 省什么 |
|---|---|---|---|---|
| 检索结果 | `(collection, question, top_k, owner)` | 200 | 库内容变更 | embedding + 向量查询 + BM25 |
| 查询向量 | `question` | 500 | 不失效（与库无关） | 一次 embedding 请求（约 250ms + 费用） |
| 来源清单 | `collection` | 64 | 库内容变更 | 全库 metadata 遍历 |

### 为什么查询向量可以"不失效"

```python
# embedding 只取决于查询文本（与库内容无关），因此不随库内容失效。
```

这是一个正确的观察。查询向量是 `embed(question)`，与知识库里有什么无关。所以库变了，查询向量仍然有效。

**这是唯一一个可以跨库复用的缓存**：同一个问题在不同知识库上检索，查询向量相同。

### 检索结果缓存为什么必须含 `owner`

```python
def _cache_get(collection_name, question, top_k, owner=None):
    """命中返回结果的浅拷贝，防止调用方误改污染缓存。owner 纳入 key：避免不同用户串结果。"""
    key = (collection_name, question, top_k, owner)
```

因为**同一个 collection 可能被多个用户访问**？不，collection 名含 owner 哈希。

那为什么要 `owner`？因为**画像偏置**（§5.13）：

```python
if owner and config.RETRIEVAL_BIAS_ENABLED and result:
    result = _apply_profile_bias(result, owner)
```

同一个 collection + 同一个问题，不同 owner 会得到**不同的排序结果**（因为画像标签不同）。所以 owner 必须进 key。

**这是一个"缓存键必须包含所有影响输出的变量"的典型案例。** 漏了 owner 会导致"甲看到乙的排序"，而且是静默的（结果看起来正常）。

### 浅拷贝的细节

```python
return list(hit)     # 浅拷贝列表
```

只拷贝列表，不拷贝里面的 `Document` 对象。所以调用方如果修改了 `Document` 的 metadata，会污染缓存。

当前的调用方（`select_diverse`、`build_sources`）都不修改 Document，所以安全。但这是一个**隐含契约**，没有文档化风险。

`_sources_cache_get` 做得更彻底：

```python
return [dict(x) for x in hit]   # 每个 dict 也拷贝
```

因为 sources 是 dict，调用方可能修改字段。

**两个函数的拷贝深度不同，是因为数据形状不同**，不是随意。

### single-flight：防止缓存击穿

```python
_EMBED_COND = threading.Condition()
_EMBED_INFLIGHT: set[str] = set()

def _embed_query(question: str) -> list[float]:
    with _EMBED_COND:
        while True:
            hit = _EMBED_CACHE.get(question)
            if hit is not None:
                return hit
            if question not in _EMBED_INFLIGHT:
                _EMBED_INFLIGHT.add(question)
                break           # 由本线程去取
            _EMBED_COND.wait()  # 等别的线程取完
    try:
        vec = get_embeddings().embed_query(question)
        ...
    finally:
        with _EMBED_COND:
            _EMBED_INFLIGHT.discard(question)
            _EMBED_COND.notify_all()
```

**问题场景**：库内容刚变更 → 缓存全部失效 → 3 个并发请求同时问同一个问题 → 3 次 embedding 请求（3 倍费用）。

single-flight 保证"同一个问题同时只有一个在途请求"，其余等待并复用。

**为什么用 `Condition` 而不是 `Lock` + 重试**：

`Condition.wait()` 会释放锁并挂起，被 `notify_all()` 唤醒后重新检查缓存。如果用 `Lock` + `sleep` 轮询，会有忙等和延迟。

**注意 `finally` 里的 `notify_all()`**：即使取 embedding 失败（抛异常），也要唤醒等待者，否则它们会永远挂着（死锁）。

这是一个容易漏的点。`finally` 块里同时做了 `discard` 和 `notify_all`，是正确的。

### BM25 的缓存更复杂：三个机制

```python
_BM25_CACHE: OrderedDict[str, tuple[list, _BM25]] = OrderedDict()
_BM25_CACHE_MAX = 8
_BM25_COND = threading.Condition()
_BM25_INFLIGHT: set[str] = set()
_BM25_NEG: dict[str, float] = {}
_BM25_NEG_TTL = 300.0
```

1. **LRU（最多 8 个库）**：每个库的索引可能占几十 MB，8 个是内存上限。
2. **single-flight**：同上，防止并发建索引。
3. **负缓存**：库太大时记 300 秒，期间直接放弃。

三个机制叠加，才让"可选的关键词通道"不会拖慢主路径。

**这是一个重要的设计原则：辅助功能的失败路径必须比主路径更快，否则它会把主路径拖垮。**

---

## 5.8 查询改写：条件触发而不是每题都改

### 问题

多轮对话里的追问：

```
用户: "什么是矩阵的秩？"
助手: "矩阵的秩是……"
用户: "那它怎么算？"        ← "它"指矩阵的秩
```

直接用"那它怎么算？"去检索：向量抓不到指代对象，BM25 也抓不到（"它"是停用词级的存在）。

### 两种方案

| 方案 | 做法 | 成本 |
|---|---|---|
| **每题都改** | 每次都调 LLM 把当前问题改写成独立问题 | 每次都多一次 LLM 调用（约 0.5~1s + 费用） |
| **条件触发** | 只在"看起来像追问"时改 | 大部分问题零开销 |

### 判定条件（`needs_rewrite`）

```python
if not history: return False          # 没有历史，不可能是指代
q = question.strip()
if not q: return False
if len(q) <= QUERY_REWRITE_MAX_LEN:   # 默认 8 字
    return True
return any(cue in q for cue in _REWRITE_CUES)
```

代词表：`它 / 他 / 她 / 这个 / 那个 / 这些 / 那些 / 该 / 此 / 其 / 上述 / 前者 / 后者 / 这样 / 那样 / 这里 / 那里`

### 这个判据的准确性

**会漏判的情况**：

- "第3章讲了什么"（12 字，无代词，但可能是追问"那第4章呢"的上下文）——其实这类问题自带主语，不需要改写；
- "继续"（2 字，命中长度规则，会改写）——正确；
- "为什么"（3 字，会改写）——正确；
- "矩阵秩的求法"（6 字，会被改写）——**误判**，这不是追问，但改写后应该与原问题相同（模型会原样输出），损失是一次 LLM 调用。

**会误判的情况**：

短问题被无条件改写。比如"什么是熵"（5 字），如果有历史，会被送去改写。模型看到"什么是熵"和前面的历史，可能输出"什么是信息熵"（如果前文在讲信息论）——这**可能是好事**（补全了上下文），也可能是**坏事**（引入了不该有的限定）。

### 失败降级

```python
try:
    resp = get_llm(temperature=0, max_tokens=64).invoke([HumanMessage(content=prompt)])
    out = (resp.content or "").strip().strip('"').strip("'").splitlines()[0].strip()
except Exception:
    return question
# 结果不可信就退回原问题：空、过长（模型跑偏成回答）都算
if not out or len(out) > 200:
    return question
```

三重保护：
1. 异常 → 原问题；
2. 空输出 → 原问题；
3. 超过 200 字（模型没改写而是开始回答）→ 原问题。

**"改写是增益不是必需"** —— 这句话决定了所有失败路径都必须回到原问题，而不是报错。

### 一个隐含的正确性要求

改写发生在**检索之前**，且"用改写后的问题当检索词，检索结果缓存的键也随之正确，不会跨上下文串结果"（`api/chat.py:971-972`）。

这句话点出一个关键：**如果缓存键用原问题，不同上下文的同一个"它怎么算"会命中同一份缓存**，但实际应该检索不同的东西。

所以缓存键必须是**改写后的问题**。当前实现正确（`retrieve_with_score(query, ...)` 传的是改写结果）。

**这是一个"缓存键必须用变换后的值"的例子**，很容易写反。

### 代价

- 追问场景多一跳 LLM（0.5~1s），首字延迟增加；
- 短问题的无条件改写可能引入错误的限定词；
- 改写本身可能失败，此时行为与不改写相同（安全）。

### 反转条件

如果有更强的指代检测（比如用小模型做分类），可以降低误判率。当前的关键词 + 长度规则是"零成本启发式"，在这个精度要求下够用。

---

## 5.9 置信度：本项目最有产品价值的设计

### 两套阈值，两件事

```python
RETRIEVE_MIN_SIM = 0.25    # 决定「这段资料丢不丢」
RETRIEVE_LOW_SIM = 0.45    # 决定「留下来的这段够不够格当依据」
```

`config.py:99-109` 的注释把区别讲得极清楚：

> ⚠️ 和上面的 RETRIEVE_MIN_SIM 是**两件事**，别合并成一个：
> `RETRIEVE_MIN_SIM` 决定「这段资料丢不丢」——丢完一条不剩时会强留最相关那条；
> `RETRIEVE_LOW_SIM` 决定「留下来的这段够不够格当依据」——不丢，但要在回答里示弱。

**为什么不能合并**：

- 如果只有 MIN_SIM（0.25）：0.3 的片段会被保留并当作依据，但 0.3 的相似度通常意味着"用词沾边但讲的是另一件事"。合并后，要么把阈值提到 0.45（会丢掉大量可用资料），要么保持 0.25（会硬答）。
- 拆成两个：**保留但示弱**。资料在，模型能看到；但模型被告知"这不构成依据"。

这是一个**三态逻辑**（丢弃/保留但示弱/保留可依据），而不是二态（丢/留）。

### 0.45 的取值依据

```python
# 为什么 0.45：同一份资料里的原文提问通常能到 0.6+，换个说法问大约 0.5，
# 只是"用词沾边但讲的是另一件事"落在 0.3 附近。0.45 卡在中间，
# 会漏判一部分边缘情况，但宁可多示弱一次，也别再装一次自信。
```

这段注释是**经验数据的记录**，非常宝贵。它给出了三个锚点：

| 情形 | 相似度 |
|---|---|
| 原文提问（几乎逐字） | 0.6+ |
| 换个说法问同一件事 | 约 0.5 |
| 用词沾边但讲另一件事 | 约 0.3 |

0.45 卡在"换说法"（0.5）与"沾边"（0.3）之间。

**注意这个阈值是针对 `text-embedding-v3` 的**。换 embedding 模型后，整个分布会移动，0.45 可能完全不对。这是一个**未标定的耦合**——阈值与模型绑定，但代码里没有体现。

改进方向（未做）：把阈值写进模型配置，换模型时一并换阈值；或者用相对阈值（相对于该库的历史分位数）。

### 分级逻辑

```python
best = max(known)
if best >= RETRIEVE_LOW_SIM:      # ≥ 0.45
    strength = HIGH
elif best > RETRIEVE_MIN_SIM:     # 0.25 < best < 0.45
    strength = MEDIUM
else:                             # ≤ 0.25
    strength = LOW
```

**只判 top1，不判平均。**

```python
# ⚠️ 判定只看 top1，且和 build_sources 用同一个阈值。这两条都是踩过的坑：
# · 用平均分/最低分判定 → 一条强相关配几条噪声也会被拖成"检索质量差"，
#   正常回答被平白加上示弱提示；
```

这个论证很扎实：

```
候选: [0.82, 0.20, 0.18]     ← 一条强相关，两条噪声
平均: 0.40 → 判 MEDIUM → 示弱   ← 错误：明明有强依据
top1: 0.82 → 判 HIGH → 正常回答  ← 正确
```

反过来：

```
候选: [0.30, 0.28, 0.27]     ← 全是弱相关
平均: 0.283 → MEDIUM
top1: 0.30 → MEDIUM           ← 一致
```

所以 top1 判定在"有强依据"时更准，在"全弱"时也正确。

**唯一的问题是**：如果 top1 是 0.82 但 top2-top10 全是噪声，模型可能被噪声干扰。但那是 prompt 组装的问题（只给 top3），不是判定问题。

### `medium` 也算 `weak`：最反直觉但最正确的决策

```python
# ★ medium 也算"没找到硬证据"，必须一起 weak。
# medium 的语义是"留下了，但都不够格"——它和 low 的区别只是"有没有沾点边"，
# 不是"能不能当依据"。只让 low 示弱的话，0.26~0.44 这段最常见的灰色地带
# 反而会被正常作答，恰好漏掉最容易硬答的那一档。
# 两者对用户的话术由前端按 strength 区分：low 说"没找到依据"，
# medium 说"只找到沾边内容"。
return {"strength": strength, "best": best, "count": len(items),
        "weak": strength in (STRENGTH_LOW, STRENGTH_MEDIUM)}
```

这个决策的逻辑链：

1. `weak` 的含义是"没有硬证据"。
2. `medium` 意味着"最高相似度在 0.25~0.45 之间"，即**没有一条达到 0.45**。
3. 所以 `medium` 就是"没有硬证据"。
4. 只不过它比 `low` 好一点（还有沾边的），所以话术不同。

**关键洞察："灰色地带才是最容易硬答的地方。"**

- `low`（< 0.25）：模型看到 0.2 的片段，几乎肯定会示弱——太明显了；
- `medium`（0.26~0.44）：片段"看起来相关"，模型很可能当成依据硬答——这才是危险区。

所以只让 `low` 示弱，恰好漏掉了最需要防护的区间。

### 两段不同的注入提示

```python
LOW_CONFIDENCE_NOTE = """
【本轮检索质量偏低，必须遵守】
这一轮检索到的片段相似度都很低，**没有一条达到"可以直接当依据"的水平**。
...
1. 第一句就要点明"这个知识库里没有和这个问题直接相关的资料"；
2. 如果片段里确实沾到了一点相关线索，可以补一句"最接近的一段讲的是……"；
3. 给出下一步：把相关文档补进这个知识库，或者改用联网搜索。
宁可说"不知道"，也不要编。"""

MARGINAL_CONFIDENCE_NOTE = """
【本轮检索只找到沾边内容，必须遵守】
检索到的片段和问题沾边，但相似度都不够高，**不构成直接依据**。
...
宁可说"只能找到沾边的"，也不要编。"""
```

两段的共同结构：
1. 【标题】+ "必须遵守"（强约束标记）；
2. 状态陈述（为什么这轮有问题）；
3. **具体的行为指令**（编号，可执行）；
4. 收尾的判断原则（"宁可……也不要编"）。

**为什么是中文结构化标记而不是英文大写？**

这是本项目提示词体系的一个刻意选择（用户长期记忆里也记了这一点）：用 `【】`、加粗、「必须遵守」这类中文结构式强调，而不是 `IMPORTANT: YOU MUST`。

理由：
- 中文模型对中文标记的敏感度不低于英文；
- 全大写英文在某些模型上会被"normalize"掉（tokenizer 处理）；
- 中文标记与上下文的语言一致性更好，不引入"语言切换"的干扰。

**这是一个值得记的经验：提示词的强调方式应该匹配上下文的语言，而不是套用英文模板。**

### 一处刻意的"放行"

```python
if not known:
    # 相似度换算不出来：不做判定，放行（见上面的方向说明）
    return {"strength": STRENGTH_HIGH, "best": None, "count": len(items), "weak": False}
```

当所有 distance 都无法换算（比如换了非归一化的 embedding 模型），判定为 `HIGH`（正常回答）。

方向说明：

> 这个方向是刻意选的：判定不了时宁可让模型正常回答，也不要凭空说"我找不到依据"——后者会把一个配置问题伪装成数据问题，更难排查。

这个论证很好：如果配置错了（embedding 模型没归一化），应该表现为"相似度显示不出来"，而不是"所有回答都在示弱"。前者是配置问题的正确表现，后者会让人误以为"知识库没内容"。

**"让错误暴露为它本来的样子"** —— 这是一个高级的工程原则。

---

## 5.10 主题标签：受控、分隔串、精确成员判定

### 三个设计

**① 受控词表**

（见卷四 §4.13 的用户画像部分，同样的模式。）

主题标签这里稍不同：标签是**从文档内容抽取的**，不是从固定词表选的。所以词表是开放的，但有数量与长度限制：

```python
if t and len(t) <= 12 and t not in out:
    out.append(t)
if len(out) >= 8:
    break
```

每个标签 ≤12 字，每个文档最多 8 个（提示词要求 3~6 个）。

**② 分隔串格式 `|t1|t2|`**

```python
tstr = "|" + "|".join(tags) + "|"
for c in group:
    c.metadata["topics"] = tstr
```

为什么要首尾加 `|`？

因为 Chroma 的 `$contains` 是**子串匹配**：

```python
{"topics": {"$contains": f"|{t}|"}}
```

- 如果存 `"数学|英语"`，查 `"|数学|"` → 不匹配（没有前导 `|`）；
- 如果存 `"|数学|英语|"`，查 `"|数学|"` → 匹配；
- 如果存 `"|英语语法|"`，查 `"|英语|"` → **不匹配**（因为实际是 `|英语语法|`，`|英语|` 不是它的子串）。

最后一条是关键：**首尾加 `|` 解决了"短标签误匹配长标签"的问题**。如果没有 `|`，`"英语"` 会匹配 `"英语语法"`。

这是一个用字符串编码模拟"集合成员判定"的经典技巧。

**③ 按来源分组，每源一次 LLM**

```python
by_source: dict[str, list] = {}
for c in chunks:
    src = c.metadata.get("source") or ""
    if src:
        by_source.setdefault(src, []).append(c)
for _src, group in by_source.items():
    text = _join_head(group)      # 前 4000 字
    tags = _extract_topics_one(text)
```

**为什么按来源而不是按片段？**

- 一篇文档的主题是一致的，逐片段抽标签会产生不一致（同一文档的不同片段被打上不同标签）；
- 成本：一篇文档切 20 片，逐片抽是 20 次 LLM 调用；按来源抽是 1 次。

**为什么只取前 4000 字？**

`_join_head` 的注释：

> 与 `"\n".join(正文)[:limit]` 逐字节等价：每一步累积的都是完整拼接串的前缀，一到长度就截断返回。区别只在于不会为一篇几 MB 的文档先造一个同样大的中间字符串再丢掉。

这是**内存优化 + 成本优化**双重考虑：4000 字足够判断主题，且不会因为一篇 10MB 的文档而烧 10 万 token。

### 代价

- 每篇文档入库时多一次 LLM 调用（约 1s）。对于"上传 100 篇"的场景，是 100 秒。
- 标签质量依赖模型，可能抽出"文档""内容"这类无意义标签。
- 标签是"文档级"的，不区分片段——同一文档的所有片段共享同一组标签。这意味着"按主题过滤"实际是"按文档过滤"，粒度较粗。

### 当前状态

`TOPIC_EXTRACT_ENABLED=1`（默认开），但**没有消费方**——`topic_where()` 的注释说是"D 块预留"，而 D 块（检索偏置）用的是 `_bias_rerank`（按画像标签与文档 topics 的重合度减距离），不是 `$contains` 过滤。

所以：标签在写入，偏置在读，但 `$contains` 精确过滤没有实际使用。这是一个**半成品**，但它的代价（每次入库一次 LLM 调用）已经在付。

**评估**：如果偏置的效果不明显，应该考虑关掉 `TOPIC_EXTRACT_ENABLED` 省成本。这需要 A/B 数据，当前没有。

---

## 5.11 画像偏置：只改排序，不改数值

### 机制

```python
def _bias_rerank(docs_scores, profile_tags, alpha):
    slots = []
    for idx, (d, distance) in enumerate(docs_scores):
        if distance is None:
            slots.append((d, None, None, idx))     # 无距离：占位
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
    # 只有拿到向量距离的条目参与重排
    ranked = iter(sorted((s for s in slots if s[2] is not None), key=lambda s: s[2]))
    out = []
    for slot in slots:
        if slot[2] is None:
            out.append((slot[0], None))
        else:
            d, dist, _biased, _idx = next(ranked)
            out.append((d, dist))
    return out
```

三个设计点：

**① 减距离而不是加相似度**

`biased = distance - alpha * overlap`。distance 越小越相关，减去 alpha 让它更靠前。

`alpha=0.08`，overlap 最多是标签数（`RETRIEVAL_BIAS_TOP=8`）。所以最大减距 0.64。

距离的合法范围是 [0, 4]。减 0.64 是显著的（相当于相似度提升 0.32），但不足以让一条 0.1 相似度的片段超过 0.5 的。

这个量级的选择是对的：**偏置应该"微调"，不能"颠覆"**。

**② 返回的是原始 distance**

```python
out.append((d, dist))    # dist 是原始值，不是 biased
```

注释：

> 最终返回的 distance 仍是原始值，调用方按它换算相似度/阈值不受影响。

这是最关键的设计。如果返回 biased distance，会有三个后果：

- `assess_grounding` 会看到被改过的相似度 → 判定失真；
- `build_sources` 显示的相似度是假的；
- 前端角标会显示"这个片段因为你的兴趣被提升了"——但用户看到的是"相似度"，会产生理解偏差。

**所以偏置只影响"顺序"，不影响"数值"。** 这是一个清晰的职责分离。

**③ 双向包含匹配**

```python
if any(p in s or s in p for s in segs):
```

- `p in s`：画像标签"数学"匹配文档标签"数学分析"；
- `s in p`：文档标签"数学"匹配画像标签"考研数学"。

为什么要双向？因为标签的粒度不一致。文档抽出的标签可能是"数学"，用户画像可能是"考研数学"——单向匹配会漏。

**这是一个"宽松匹配"的设计，代价是可能误匹配**（"计算机" vs "计算机视觉"会互相匹配，但主题可能不同）。

### `distance is None` 的处理（含事故）

关键词通道独有的片段没有距离。代码用"占位 + 原样放回"：

```python
slots.append((d, None, None, idx))
...
if slot[2] is None:
    out.append((slot[0], None))
```

**为什么不把它们移到末尾或开头？**

因为它们的位置本身就是"融合排名"决定的。混合检索下，一个纯关键词命中的片段可能排第 2（RRF 分数高），如果强行移动，会破坏融合排序。

所以"占位原样放回"是对的：它们在原来的位置，只是不参与"按 biased distance 排序"。

注释里记的事故：

> 原先这里对 None 直接 `float()` 会抛 TypeError：只有"用户有画像标签 + 命中关键词独有片段"两个条件同时成立才会炸，线上是一条静默 500。

`float(None)` → TypeError。触发条件：
- 用户有画像标签（用了一段时间的用户）；
- 混合检索开启；
- BM25 命中了向量没命中的片段。

三个条件都不罕见，但叠加概率低。所以 bug 活到了线上。

**这是一个"条件组合型 bug"的典型**，也是为什么回归测试要覆盖"组合场景"而不只是"单一路径"。

---

## 5.12 老库预览：从向量片段拼回原文

### 问题

用户想预览知识库里某个文件的正文。理想情况是从 `data/kb_sources/` 读原始文件。但：

- 改造前建的老库没有留档原始文件（留档是后来加的）；
- 有些文件是"导入后删除"的（只有片段，没有原文）。

### 方案

```python
def get_source_chunks(self, source: str) -> list[dict]:
    """按来源取该文件的全部片段（按页码与片段顺序排好）。
    用途：老库没有原始文件留档时，预览只能从向量库把片段拼回来。"""
    got = self._vs._collection.get(where={"source": source},
                                   include=["documents", "metadatas"])
    items = [...]
    items.sort(key=lambda x: (
        x["page"] if isinstance(x["page"], int) else 0,
        x["chunk_index"] if isinstance(x["chunk_index"], int) else 0,
    ))
    return items
```

两个关键点：

**① `chunk_index` 是入库时记的**

```python
# build_index
counter: dict[str, int] = {}
for c in chunks:
    src = c.metadata.get("source") or ""
    c.metadata["chunk_index"] = counter.get(src, 0)
    counter[src] = counter.get(src, 0) + 1
```

按 `source` 分组，从 0 开始编号。这样拼回来时能恢复原始顺序。

**② 排序键要兼容非 int 的 page**

`isinstance(x["page"], int)` —— 因为 PDF 有 page（int），txt/md 没有（None）。用 `else 0` 兜底。

**这是一个"用 metadata 重建原文"的模式**：牺牲一点保真度（重叠的 50 字会重复出现），换取"没有原件也能看"。

### 代价

拼回来的文本会有重复（overlap=50，每个片段开头 50 字是上一个片段的结尾）。用户可以接受（毕竟是"降级预览"），但这是一个可见的瑕疵。

更好的做法：拼回时用 overlap 做"最长公共前后缀"去重。当前没做。

---

## 5.13 检索层的整体评价

### 这一层的技术密度最高，因为它要处理"不确定性"

统计一下这一层的机制数量：

- 4 种缓存（检索结果 / 查询向量 / 来源清单 / BM25 索引）
- 3 种并发控制（单例锁 / single-flight / 库级 RLock）
- 3 段流水线（离群 / MMR / 截断）
- 2 个通道（向量 / BM25）
- 2 套阈值（MIN_SIM / LOW_SIM）
- 3 种置信度（high/medium/low）+ 1 种（none）
- 2 段提示注入（LOW / MARGINAL）
- 1 个条件触发器（查询改写）
- 1 个偏置器（画像）

**这些机制没有一个是"框架自带"的。** LangChain 提供了 `Chroma` 和 `Retriever` 抽象，但：
- 它的 `similarity_search` 不支持"扩召回 + 离群 + MMR"的三段式（有 `max_marginal_relevance_search`，但不带离群过滤，且用 embedding 算 MMR）；
- 它的 `EnsembleRetriever` 支持多通道融合，但用的是加权 RRF 的可配置版本，且不支持"关键词独有片段无距离"这种混合状态；
- 它没有"置信度分级"这个概念。

所以这一层基本是**自研**的，LangChain 只提供了"Chroma 客户端 + embedding 客户端 + 文本切分器"三个零件。

**这是一个重要的观察**：RAG 的"框架"部分很薄，真正决定质量的是检索后处理。而检索后处理没有通用方案，因为它依赖具体的数据分布、具体的 embedding 模型、具体的用户期望。

### 三个未解决的缺口

**缺口 1：阈值与 embedding 模型耦合，但没有显式绑定。**

`0.25 / 0.45` 是针对 `text-embedding-v3` 调的。换模型后必须重调，但代码里没有提示。

**缺口 2：没有检索质量的离线评估。**

"检索准不准"目前只能靠人工看。没有一个标注集（问题 → 应该命中哪些片段）来量化 precision/recall。这意味着所有参数调整（MMR λ、阈值、扩召回倍数）都是**无据可依**的。

对比：`context_compress.py` 的注释明确提到"评估集建好之前没有质量信号"——说明团队知道这个问题，但压缩模块用了 L0 硬规则来规避，检索层没有对应的规避手段。

**缺口 3：主题标签在付费但未被消费。**

见 §5.10。

### 一条值得记住的设计原则

这一层反复出现同一个模式：

> **当不确定时，让系统"降低承诺"，而不是"降低质量"。**

- 检索可能不准 → 置信度分级 + 示弱提示（而不是"提高检索准度"这种无底洞）；
- BM25 可能太慢 → 直接放弃，退回纯向量（而不是"慢慢等"）；
- 改写可能失败 → 用原问题（而不是报错）；
- 标签可能抽不到 → 跳过（而不是阻断入库）。

每一条都是"功能降级但系统可用"，而不是"要么全对要么报错"。

**这就是卷一说的"静默失败最可怕"的反面用法**：降级本身可以静默，但必须保证降级后的行为是安全的。

下一卷进入编排层。检索层解决"找到什么资料"，编排层解决"谁来用这些资料、用什么工具、走几步"。
