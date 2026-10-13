# AI 检索开发指南（林宏亮 / `sakasa-code`）

> 覆盖：知识库写入流水线、向量检索、混合检索、联网搜索、导入抓取、`api/kb.py` 端点契约。
> 前置：[01_架构与模块职责.md](../01_架构与模块职责.md)（权威归属）、[02_核心数据结构定义.md](../02_核心数据结构定义.md) §6、[03_流程规范.md](../03_流程规范.md)。
> 事实基线：当前 `main`（`96e0dda`）。标注「待确认」处不要当结论用。

---

## 1. 模块边界

**你的地盘**：`kb/rag_core.py`（718 行，检索/切片/embedding/依据判定）、`kb/pipelines.py`（427 行，注册表/归属/文件清单/锁）、`kb/importer.py`（252 行，抓取/导入/下载）、`core/serper.py`（**联网搜索唯一入口**，见 §6；⚠️ 当前工作区尚未合入，见 §6.2）、`api/kb.py`（474 行，14 个端点）。

> 📌 **行数口径**：上面是**本工作区（main, `96e0dda`）的实测值**。
> `规范/01_架构与模块职责.md` 里 `core/scheduler.py (+853)`、`kb/rag_core.py (+387)` 这类
> **带 `+` 号的是队友分支相对 main 的增量**，不是文件总行数——两者别混用。


**不要动的**：`agent/**` 归刘佳林（`huashuotianxuan`，内含 `agent/tools.py` 的**旧搜索实现**，见 §6.2）；`frontend/**` 归申请人（`baomi888`，`refs` 的 `JSON.parse` 在前端做，你只保证它是 JSON 字符串）；`core/config.py` 归申请人——**新增配置项必须先通知申请人**。

交叉区（`api/*.py`、`main.py`、`core/config.py`、`core/session.py`）走 `01` §6 流程：**先通知 → 独立分支 → 小步提交 → 拉对方 review**。

> ⚠️ `kb/rag_core.py:108-109`、`:121` 的注释引用了「2026-10-03 实测」（百炼 `text-embedding-v3` 返回单位向量、本地库 11 条 L2 范数全为 1.0000）。
> **换 embedding 模型这段结论就失效**，必须重跑 `tests/test_retrieve_select.py` 并更新注释——注释是这段逻辑唯一的推导依据。

---

## 2. 知识库标识与归属

### 2.1 `kb_id = md5(owner|name)[:12]`

```python
# kb/pipelines.py:269-276
def new_kb_id(owner: str, name: str) -> str:
    raw = "%s|%s" % (owner, (name or "").strip())
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]
```

推论：① **同名即同库**——同一人用同名建库必然拿到同一 `kb_id`，**不会新建**；② **同名不同主是两个库**，因为 owner 混进了哈希；
③ ASCII 名走另一条路——`safe_kb_id()`（`:257-266`）对合法 ASCII 名直接复用原名，落进 md5 分支的只有中文/非法字符名。
会 `strip()` 但**不归一大小写**（`"Kaoyan"` ≠ `"kaoyan"`）；前端 `page.tsx` 额外做了 `trim().toLowerCase()` 匹配，
所以「按名复用」在客户端比服务端更宽——那是前端行为，不在你的范围。

### 2.2 三张落盘表与归属门禁

| 文件 | 全局变量 | 作用 |
| --- | --- | --- |
| `data/kb_owners.json` | `_KB_OWNERS` | `kb_id → owner`。**没有记录 = 无主，谁都访问不到** |
| `data/kb_names.json` | `_KB_NAMES` | `kb_id → 中文展示名`（Chroma collection 名只允许 ASCII） |
| `data/kb_files.json` | `_KB_FILES` | `kb_id → [{name, chunks, added_at, origin}]` |

三者都用 `_atomic_write_json`（`:108-131`）原子写：写同目录 `.tmp` → `fsync` → `_replace_with_retry`（`:88-105`）换上去。
Windows 上目标文件被任何句柄开着都会 `WinError 5`，所以有 6 次指数退避重试（≈0.6s）。**持续失败就抛出，不静默 pass**——
宁可丢一次登记，也不能写出截断 JSON 让下次启动读不出整个清单。

**归属门禁唯一入口**：`get_or_create(owner, kb_id)`（`:308-316`）第一件事就是 `owner_of(kb_id) != owner → raise NotOwned`。
`owner` 放第一位且**无默认值**，用意是「漏改的调用点直接 `TypeError`，而不是悄悄拿到别人的库」。
`NotOwned` 与「不存在」**对外表现完全一致**（`:60-64`）；HTTP 层同理，`_must_own` 统一返回 **404 而非 403**：

```python
# api/kb.py:47-54
"""刻意不返回 403：403 等于告诉对方"这个 kb_id 是真的，只是你没权限"，
那是一条可以用来枚举别人知识库的线索。不存在与无权，对外长得一样。"""
if pipelines.owner_of(kb_id) != uid:
    raise HTTPException(status_code=404, detail=f"知识库不存在：{kb_id}")
```

改造前的无主老库同理**谁都访问不到**（刻意的「默认拒绝」，防止第一个登录的人顺手认领别人的数据），
恢复只能靠显式 `python tools/migrate_owner.py`（内部调 `claim_orphan_kbs`，`:217-239`，扫磁盘上所有 `kb_*` collection 认领无主项）。

### 2.3 为什么「导入时按名复用已有库」是正确行为

这是**被当成 bug 报过、但结论是「不该改」**的一条：

1. `create_kb(owner, name)` 天然**幂等**——同名必然算出同一 `kb_id`，`_KB_NAMES`/`_KB_OWNERS` 覆盖写，`_get_or_create_raw` 复用已有 pipeline。
2. 所以「新建一个同名库」在服务端**根本不可能**。反过来改成「每次新建一个 id」才会产生同名重复库，`list_all` 里出现两条一模一样的名字，用户无法分辨。
3. `tests/test_kb_isolation.py:59-60` 把这条语义钉死了：
   ```python
   check("同一个人重建同名库 -> 同一个 id（幂等）",
         pipelines.create_kb(ALICE, "我的库")[0] == ka)
   ```

**改这条语义会破坏契约**：`02` §6.1 明写「同名即同库」；仓库里也有历史提交
`fix: 联网搜索偏向中国结果 + 资料库导入按名复用已有库避免建同名库`。真要改就走 `04` §4 规则 8 的流程。
**真正该补的是「复用要说出来」**：`create_kb` 命中已存在 id 时返回 `reused: true` + 现有文件数，
前端 toast 才能说清「已复用「X」（库内现有 N 个文件）」。这条涉及 `frontend/**`，**需先与申请人协商**。

---

## 3. 写入流水线

### 3.1 全链路

```
文件落盘         data/kb_sources/{kb_id}/{md5前8位}_{安全名}   pipelines.source_path / safe_source_name
   ▼
load_document    rag_core.py:402   txt → utf-8，失败退 gbk；md 按纯文本；pdf → PyPDFLoader
   │              统一写 metadata["source"] = 原始文件名（不能给临时路径，会做文件级删除）
   ▼
split_documents  rag_core.py:444   RecursiveCharacterTextSplitter
   │              separators=["\n\n","\n","。","！","？","；","，"," ",""]
   ▼
补 chunk_index   rag_core.py:577   记录片段在来源文件内的顺序，预览时按它还原正文
   ▼
_embed_and_store rag_core.py:458   并发 embedding + 串行 upsert（分窗）
   ▼
add_file_records pipelines.py:467  登记 {name, chunks, added_at, origin} 并原子落盘
```

**入口函数怎么选**（踩过的坑，别选错）：往**可能还是空**的库追加用 `pipe.add_chunks(chunks)`（`rag_core.py:537`，内部走 `_ensure_vs()`）；
只想「打开已有库」做判定用 `pipe.get_or_open()`（`:713`，**空库时 `_vs` 保持 `None`**，`exists()`/`peek()` 靠这个语义）；
上传建索引用 `pipe.build_index(...)`（`:553`，内部走 `_ensure_vs()`，`mode="rebuild"` 先删整个 collection）。

> ⚠️ **空库上 `get_or_open()` 拿到 `None`**，接着 `_vs.add_documents()` 直接 `AttributeError`，
> 且失败发生在建 collection **之前** ⇒ 重试多少次都不可能成功。这就是 2026-10-04 定位、10-05 修的「联网建库 100% 失败」，
> 回归用例 `tests/test_web_import_creates_index.py`。该用例第 4 节还做**源码静态约束**：`kb/importer.py` 里不许再出现 `.get_or_open()`。

### 3.2 参数与并发

| 常量 | `config.py` | 默认 | 语义 |
| --- | --- | --- | --- |
| `CHUNK_SIZE_DEFAULT`（键 `CHUNK_SIZE`） | `:43` | 500 | 单片长度上限（`length_function=len`，按字符） |
| `CHUNK_OVERLAP_DEFAULT` | `:44` | 50 | 相邻切片重叠字数 |
| `TOP_K_DEFAULT` | `:45` | 3 | 默认召回条数 |
| `EMBED_BATCH_SIZE` | `:48` | 10 | **单次请求**最多发几条文本；`10` 是阿里百炼**接口硬上限**，改大直接报错 |
| `EMBED_CONCURRENCY` | `:52` | 4 | 同时在途的**批次数**（不是条数）；太高撞 429，`1` = 退回串行 |

```python
# core/config.py:47-52
# Embedding 接口单次批量上限（阿里百炼为 10）
EMBED_BATCH_SIZE = int(os.getenv("EMBED_BATCH_SIZE", "10"))
# 入库时并发发出的 embedding 批次数（每批仍是 EMBED_BATCH_SIZE 条）。
# 瓶颈在网络往返，串行等 N 批 = N 个 RTT 叠加；并发 4 基本能把 RTT 藏起来。
# 调太高会撞服务端限流（429），一般 3~4 足够；1 = 退回串行。
EMBED_CONCURRENCY = int(os.getenv("EMBED_CONCURRENCY", "4"))
```

在途向量峰值 = `4 × 10 = 40` 条。重叠 50 字的直接后果是**相邻片段字符 bigram 重合度非常高**——这正是 MMR 要去重的对象（§4.2）。
`_embed_and_store` 的注释写清了两个「为什么」：**embedding 并发、写入不并发**（多线程同时往同一 collection 写会竞争 hnsw 索引与底层 sqlite，
结果不确定；embedding 是纯网络调用天然可并发，写入留单线程不构成瓶颈）；**分窗**（一次把全部批发出去要把整份文档的向量留在内存，
几千片 × 上千维就是几十 MB；按「窗口 = 并发度」逐窗处理，**并发不变、峰值内存钉死**）。

### 3.3 三把锁

| 锁 | 位置 | 粒度 | 为什么 |
| --- | --- | --- | --- |
| `_REGISTRY_LOCK`（**RLock**） | `pipelines.py:54` | 全局注册表 | 保护五个容器。**必须可重入**：`add_file_records` 内部会调 `get_or_create`，普通 `Lock` 第二次 acquire 直接死锁 |
| `kb_lock(kb_id)`（RLock） | `pipelines.py:242` | 按库 | 同库的入库/重建/删文件**串行**；不同库互不影响 |
| `_CHROMA_LOCK` | `rag_core.py:34` | 单例首次构造 | 双重检查锁，8 线程并发首调只建一个 client |

**Chroma 客户端必须传单例**——`_ensure_vs()`（`:518-535`）注释：不传的话 langchain 会按 `persist_directory`
自己 new 一个 `PersistentClient`，单例就白做了（实测 163.5ms vs 73.1ms），且多个 client 指向同一 sqlite 还有锁竞争隐患。
`test_chroma_singleton.py:73-76` 用**源码扫描**钉死：全项目 `chromadb.PersistentClient` 只允许出现 **1 次**。

---

## 4. 检索链路

```
query
  ├─ 0. 查询向量缓存  _embed_query()        rag_core.py:384   省一次约 250ms 的 embedding 请求
  ├─ 1. 候选池        fetch_k = max(top_k*mult, top_k+6, RETRIEVE_MIN_FETCH)     :751
  ├─ 2. 离群过滤      sim < RETRIEVE_MIN_SIM 丢弃（一条不剩时强留最相关那条）   :252-260
  ├─ 3. MMR 多样性    lam*relevance - (1-lam)*duplicate                          :266-303
  └─ 4. 低置信判定    assess_grounding()    rag_core.py:139   **不参与挑选，只决定示弱**
```

```python
# rag_core.py:746-755  候选池
mult = config.RETRIEVE_FETCH_MULT
if top_k <= 1 or mult <= 1:
    result = self._vs.similarity_search_by_vector_with_relevance_scores(vec, k=top_k)
else:
    fetch_k = max(top_k * mult, top_k + 6, config.RETRIEVE_MIN_FETCH)
    cands = self._vs.similarity_search_by_vector_with_relevance_scores(vec, k=min(fetch_k, total))
    result = select_diverse(cands, top_k)

# rag_core.py:292-301  MMR 打分
val = lam * relevance(scores[i]) - (1 - lam) * max(_overlap(shingles[i], shingles[j]) for j in order)
```

| 常量 | `config.py` | 默认 | 语义 |
| --- | --- | --- | --- |
| `RETRIEVE_FETCH_MULT` | `:66` | 3 | 候选池倍数。**先多取几倍再挑，否则 top_k 名额会被相邻重复切片占满** |
| `RETRIEVE_MIN_FETCH` | `:68` | 12 | 候选池下限：`top_k` 设得小时也保证有得挑 |
| `RETRIEVE_MMR_LAMBDA` | `:70` | 0.65 | MMR 相关度权重；`1` = 完全按相关度排（等同旧行为），越小越强调多样性 |

取 max 的是**三项**：`top_k*mult`、`top_k+6`、`RETRIEVE_MIN_FETCH`。所以 `top_k=3` 时 `fetch_k = max(9,9,12) = 12`；
`top_k=10` 时 = 30。最后的 `min(fetch_k, total)` 兜住「库比候选池还小」。

**MMR 四要素**：`rel` 用 `_to_similarity(d)` 即**绝对余弦**——**刻意不用批次内 min-max 归一化**，因为重叠切片塞满时候选距离只差 0.0x，
归一化会把这点差距放大成「最相关 vs 最不相关」，重复片段靠微弱优势把名额全占了；`dup` 用 `_overlap(_shingles(a), _shingles(b))`
即字符 2-gram 的 **Jaccard**（中文无空格可分词，bigram 近似「两段话有多大重合」够用）；第一条 `min(remaining, key=lambda i: scores[i])`
**固定取最相关的**，给后面的多样性比较一个基准。

### 4.1 余弦换算的地基

```python
# rag_core.py:125-127
if distance is None or distance < 0 or distance > 4:
    return None
return 1 - distance / 2
```

**值域必须是 `[0, 4]`，不是 `[0, 2]`**。推导（注释里有完整实测）：集合创建时没指定 `hnsw:space`，走 Chroma 默认的**平方 L2**；
百炼 `text-embedding-v3` 返回的向量**已经是单位向量**（本地库 11 条实测 L2 范数全为 1.0000）；
平方 L2 在单位向量上 `d = ||a-b||² = 2 - 2cos` ⇒ `cos = 1 - d/2`，**`d` 合法值域 `[0,4]`**。

**按 `[0,2]` 判定会造成两个后果**（都有离线回归盯着）：① `select_diverse` 里 `all(s is not None ...)` 短路
⇒ **`RETRIEVE_MIN_SIM` 这道阈值整条被跳过**；② `relevance()` 退化为批次内相对排序 ⇒ **负余弦片段反而拿到虚高的相关度**，把真正相关的片段挤出名额。
复现：把守卫改成 `distance > 2` 重跑 `tests/test_retrieve_select.py`，**用例 3 与 3b 都会失败**；改回 `> 4` 即 ALL PASS。

**`refs` 里不能出现距离**：`build_sources`（`:760-792`）与 `_format_refs`（`:794-809`）都走 `_to_similarity`。
线上真出过事故——参考资料卡片把「相似度」标成 **1.089 / 1.160 / 1.237**，那其实是**距离**。换算不出来（`None`）时前端直接隐藏这一项。

---

## 5. ⚠️ `RETRIEVE_MIN_SIM` 与 `RETRIEVE_LOW_SIM` 是两件事

> **本节最重要。** 代码注释里明确写了不许合并——这不是风格问题，是产品结论。

### 5.1 原始注释（`core/config.py:71-86`，逐字引用）

```python
# 余弦相似度下限：低于它当不相关直接丢。0 = 不做离群过滤
# 0.25 是刻意留宽的：宁可多带一条弱的，也别把能用的片段误杀。
# 觉得回答还是太"硬答"就往 0.4 调；觉得资料太少就调回 0.2 或 0。
RETRIEVE_MIN_SIM = float(os.getenv("RETRIEVE_MIN_SIM", "0.25"))

# ---------- 可信度（P0：让模型"敢说知识库里没有"）----------
# ⚠️ 和上面的 RETRIEVE_MIN_SIM 是**两件事**，别合并成一个：
#   RETRIEVE_MIN_SIM 决定「这段资料丢不丢」——丢完一条不剩时会强留最相关那条；
#   RETRIEVE_LOW_SIM 决定「留下来的这段够不够格当依据」——不丢，但要在回答里示弱。
# 用户调研（56 份）里「答案不准还装自信」占 83.9%，是压倒性第一担忧，
# 这个阈值就是拿来对冲它的：相似度低于它 = 没找到足够依据。
#
# 为什么 0.45：同一份资料里的原文提问通常能到 0.6+，换个说法问大约 0.5，
# 只是"用词沾边但讲的是另一件事"落在 0.3 附近。0.45 卡在中间，
# 会漏判一部分边缘情况，但宁可多示弱一次，也别再装一次自信。
RETRIEVE_LOW_SIM = float(os.getenv("RETRIEVE_LOW_SIM", "0.45"))
```

### 5.2 对照表

| 维度 | `RETRIEVE_MIN_SIM` = 0.25 | `RETRIEVE_LOW_SIM` = 0.45 |
| --- | --- | --- |
| 回答的问题 | **这段资料丢不丢** | **留下来的够不够格当依据** |
| 作用位置 | `select_diverse`（`:252-260`）**挑选阶段** | `assess_grounding` / `build_sources` **判定阶段** |
| 是否影响 `top_k` 条数 | 影响（会砍掉候选） | **不影响**——片段照样进 prompt |
| 越过阈值后做什么 | 丢弃；**一条不剩时强留最相关那条** | **不丢**，但追加示弱提示 |
| 调高 | 资料变少，可能把能用的片段误杀 | 示弱变频繁，模型更容易说「没找到依据」 |
| 调低 | 带进更多噪声片段 | 更容易硬答，**正是 83.9% 的用户最反感的** |

### 5.3 设计权衡：为什么必须分成两道

用户调研（56 份）里 **「答案不准还装自信」占 83.9%，是压倒性第一担忧**。这个数字决定了 P0 的形态：
**问题不是「找不到」，是「找不到还装找到」**。于是需要两种**性质不同**的动作：

- **丢**是「信息取舍」——别让噪声挤占 prompt 名额。方向是**宁可留宽**（注释：「宁可多带一条弱的，也别把能用的片段误杀」）。
- **示弱**是「态度校正」——别让模型把弱证据当硬证据。方向是**宁可多示弱一次**（注释：「宁可多示弱一次，也别再装一次自信」）。

**如果合并成一个阈值**，两个方向立刻冲突：

| 合并取值 | 后果 |
| --- | --- |
| 取 0.45（按丢的口径） | 0.25~0.45 这段被**直接丢掉**。这段恰恰是「用词沾边但讲的是另一件事」（注释：落在 0.3 附近）——丢完候选可能一条不剩，只能强留最相关那条，**用户还是拿到一个 0.3 的片段，却没人告诉他这不够格** |
| 取 0.25（按示弱的口径） | 0.25 以下直接丢，但 0.25~0.45 被**当成正常依据**使用 ⇒ 灰色地带全面硬答 |

**两个方向的正确答案本就不一样**：丢东西要保守（别误杀），示弱要积极（别放过）。所以只能是两道阈值。

### 5.4 落到代码上的三个约束（全在 `assess_grounding` 的注释里）

1. **判定只看 top1，不看平均分/最低分**（`:147-152`）：用平均分/最低分判定 → 一条强相关配几条噪声也会被拖成「检索质量差」，正常回答被平白加上示弱提示。
2. **判定阈值必须和 `build_sources` 一致**（同一段注释）：和 `build_sources` 用不同阈值 → 引用卡片上标着「弱」，回答侧却认为够格，
   用户看到的就是「卡片说这条没用、正文却在用它答」。**改这里的阈值，必须同步改 `build_sources` 里的分级。**
3. **`medium` 也算 `weak`**（`:178-183`）：medium 的语义是「留下了，但都不够格」——它和 low 的区别只是「有没有沾点边」，
   不是「能不能当依据」。只让 low 示弱的话，0.26~0.44 这段最常见的灰色地带反而会被正常作答，**恰好漏掉最容易硬答的那一档**。

### 5.5 分级表（`:132-136`、`:170-189`、`:774-782`）

| `strength` | 条件 | `weak` | 追加提示（`grounding_note`） | 前端话术 |
| --- | --- | --- | --- | --- |
| `high` | `best >= 0.45` | 否 | 无 | 正常回答 |
| `medium` | `0.25 < best < 0.45` | **是** | `MARGINAL_CONFIDENCE_NOTE` | 「只找到沾边内容」 |
| `low` | `best <= 0.25` | **是** | `LOW_CONFIDENCE_NOTE` | 「没找到依据」 |
| `none` | 什么都没检索到 | 否 | 无 | — |

**换算不出来时的方向是刻意的**（`:142-145`）：相似度全为 `None` → **判为 `high`**，放行。
理由：「判定不了时宁可让模型正常回答，也不要凭空说'我找不到依据'——**后者会把一个配置问题伪装成数据问题，更难排查**」。

### 5.6 改阈值时的正确姿势

```bash
python tests/test_retrieve_select.py     # 纯函数回归，不需要网络与知识库；期望 ALL PASS
pytest tests/test_grounding.py -q        # 依据判定回归
# 端到端：确认 grounding 帧的 weak 与 sources 卡片上的 strength 标签一致
```

一键回退开关：`GROUNDING_NOTICE`（`config.py:88-90`，默认 `1`）。
**关掉后检索链路行为完全回到改造前**（不示弱、不追加提示）——留着它就是为了一键回退，**不用回滚代码**。

---

## 6. 联网搜索：全部收口到 `core/serper.py`

`core/serper.py` 是**联网搜索唯一入口**，归你（`01` §2）；`config.SERPER_API_KEY` 是唯一密钥来源（`config.py:126`）。
**所有 Serper 调用必须收口到它。** `gl=cn / hl=zh-cn` 的必要性：Serper 打的是 Google 全球接口，
**不加锚定会返回大量国内打不开的外站链接**——用户点进去是空白页，对「面向大学生的中文学习助手」等于结果不可用。

> **待确认（当前实现状态）**：截至本文写作，工作区里**没有 `core/serper.py` 这个文件**（`glob **/serper*.py` 零命中）。
> 中国区锚定目前在两处**各自实现**：`kb/importer.py:173-178`（`search_preview`）与 `agent/tools.py:139-144`（`web_search`），
> payload 完全一致，都带 `"gl": "cn", "hl": "zh-cn"`。`01` §3.2 记录该文件为 `+147` 行新增件，
> 说明它存在于 `dev` / `feat/ai-module-20261013` 分支，**尚未回流 `main`**。本节按「目标形态」描述，落地前请先确认文件已在你的分支上。

### 6.1 「搜索双轨」问题

`规范/README.md` 把它列为本套文档要根治的四类问题之一：

> 同一个「搜索」有两套实现（`agent/tools.py` 与 `core/serper.py`），**改一处漏一处**。

| 轨 | 位置 | 调用方 | 返回 |
| --- | --- | --- | --- |
| 旧轨 | `agent/tools.py:111-167` `web_search` | Agent 工具链 | Markdown 文本（给模型看） |
| 新轨 | `core/serper.py` | `api/kb.py` / `kb/importer.py` | 结构化 dict（给程序消费） |

**为什么要收口**：两轨各自维护 `gl/hl`、各自维护 `boost_app_query` 调用、各自的错误文案。
**任何一处漏改就是一次行为不一致**——而且因为两条路径的输入输出形状不同（str vs dict），
最容易出的错是「改了 A 忘了 B」，且**测不出来**。

### 6.2 收口方向（分三步，不要一次性重写）

| 步骤 | 动作 | 风险 |
| --- | --- | --- |
| ① | `core/serper.py` 提供**两个**出口：`search_raw()` 返回结构化结果、`search_text()` 返回 Markdown | 低。纯新增，不改现有调用 |
| ② | 把 `agent/tools.py::web_search` 内部实现**换成一行委托**（保留 `@tool` 装饰器与 docstring 签名不变） | 中。**必须通知刘佳林**（`agent/**` 是他的地盘），且 `_runtime(config)` 身份注入不能动 |
| ③ | 确认 ② 稳定后删除 `agent/tools.py` 里的直连实现（`httpx.post` + `SERPER_API_KEY`） | 中。**必须同步改 `kb/importer.py::search_preview`**，否则双轨变成「新轨 + 第三处」 |

两处都调用了 `kb.importer.boost_app_query`（`importer.py:136-160`，App 类搜索自动补「官网」直达下载页）。
该函数在 `kb/**` 里属于你的地盘，**收口时一起挪进 `core/serper.py` 更合理**（待确认：需与 C 线协商，因为 `agent/tools.py` 归刘佳林）。

**验收标准**：`grep -rn "google.serper.dev" --include=*.py .` 与 `grep -rn '"gl": "cn"' --include=*.py .` 都应只剩 `core/serper.py` 一处。
提交类型用 `refactor(search):`，示例见 `03` §2.3：`✅ refactor(search): 搜索调用统一收口到 core/serper.py`

---

## 7. 安全：SSRF 防护与测试占位

### 7.1 为什么必须做、怎么走

`core/netsafe.py` 文件头注释讲清了动机：

> 单用户时「让服务器去访问一个 URL」顶多是访问自己的东西；多用户之后，任意一个登录用户都能借服务器的身份去访问
> **服务器能看到、而他自己从外网看不到**的地址——云厂商元数据服务、内网管理后台、Redis、同机的其他端口。
> 这就是典型的 SSRF，**而且是「过了登录」的 SSRF**。

**导入外部 URL 必须走它。** 会发起服务端请求的通道：`api/kb.py:443`（import-url）/ `:460`（import-batch）/ `:501`（download），
以及 `kb/importer.py:41` 的 `_safe_get`（抓网页正文、下载文件）。

**四条判断口径（宁可错杀）**：① 只放行 `http`/`https`（`netsafe.py:74-76`）；
② 主机名必须能解析，且**解析出来的每一个 IP** 都不能是内网/回环/链路本地/保留段（`:95-110`，**只看第一个 IP 不够，DNS 可以返回多个**）；
③ 拒绝带用户信息的形式 `http://user:pass@host`（`:78-79`）；④ 不跟随重定向自己判断。
`_is_bad_ip`（`:36-59`）逐项检查 `is_loopback` / `is_link_local`（`169.254.0.0/16` ← 云元数据 `169.254.169.254` 就在这）/
`is_private` / `is_reserved` / `is_unspecified` / `is_multicast`，IPv6 还会先拆 `ipv4_mapped`。

### 7.2 逐跳校验（别改成自动跟随）

```python
# kb/importer.py:34-51（节选）
"""带 SSRF 校验的 GET，**每一跳重定向都要重新校验**。
follow_redirects=False 是刻意的：交给 httpx 自动跟随时，
一个公网 URL 只要 302 到 http://169.254.169.254/latest/meta-data/，
入口那次校验就成了摆设。多用户之后这是别人能打进内网的口子。"""
target = assert_safe_url(url)
for _ in range(_MAX_REDIRECTS + 1):          # _MAX_REDIRECTS = 5
    resp = httpx.get(target, headers=_HTTP_HEADERS, timeout=timeout, follow_redirects=False)
    if resp.status_code in (301, 302, 303, 307, 308):
        loc = resp.headers.get("location")
        if not loc:
            return resp
        target = assert_safe_url(urljoin(target, loc))   # ← 每一跳都重新校验
        continue
    return resp
```

`api/kb.py:510-527` 的代理下载同理：`follow_redirects=False`，拿到 3xx 后手动 `assert_safe_url(nxt)` 再发第二次请求。

### 7.3 已知边界与返回约定

```python
# core/netsafe.py:65-68
# 注意：这里的结论只对"解析这一刻"有效。攻击者可以用 DNS rebinding
# （第一次解析给公网 IP、真正连接时给内网 IP）绕过；单机作业做到这一步
# 已经能挡掉绝大多数误伤与脚本扫描，真要防 rebinding 得在连接层用
# socket 绑定解析结果再握手，不在本文件的职责范围内。
```

**这是明写的已知边界。** 若有人报「netsafe 能被 rebinding 绕过」，引用这段注释回复，**不要当 bug 去改**。
返回约定：不合法抛 `UnsafeURLError`，合法则**原样返回**。`UnsafeURLError` 继承 `ValueError`（`:32-33`），
所以调用方一律 `except ValueError` 即可接住，`api/kb.py:502-503` 把它转成 **400**。**别改成返回 `None` 或 `bool`**，会破坏所有调用点。

### 7.4 测试占位规范

`CONTRIBUTING.md` §6 与 `规范/README.md` 红线 3：

> 🚫 不要硬编码个人信息、服务器 IP、密钥。测试占位统一用 RFC 5737 文档地址（`203.0.113.x`）。

| ✅ 正确 | ❌ 禁止 |
| --- | --- |
| `http://203.0.113.10/doc.txt` | 任何真实公网 IP / 真实服务器 IP |
| `http://example.com/...` | 真实用户数据、内网拓扑 |
| `127.0.0.1`（**仅限本机起 http.server 做离线回归**） | 把回环地址写进示例配置或文档 |

> 唯一例外是 `tests/test_web_import_creates_index.py:86-89`：本机 `127.0.0.1` 起 `http.server` 当假网站，
> 因此**显式放行**了 SSRF 检查（`importer.assert_safe_url = lambda u: u`），注释写清了理由。
> **新增离线用例可照抄这个模式，但必须在注释里写明放行理由。**

**改动 `netsafe.py` 的硬要求**（`CONTRIBUTING.md` §6 + `03` §3.3）：涉及 SSRF / 鉴权 / 配额的逻辑改动
**务必同步更新对应测试用例**——SSRF 侧是 `tests/test_agent_guard.py`。

---

## 8. 接口契约：`api/kb.py`

### 8.1 端点清单（14 个）

`router = APIRouter(dependencies=[Depends(get_current_user)])`（`api/kb.py:39`）——**鉴权挂在 router 上而不是每个函数上**，新增端点时不会漏。

| 方法 | 路径 | 处理函数 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/api/kb/` | `list_kbs` | 列出**当前用户**的知识库 |
| `POST` | `/api/kb/create` | `create_kb` | 登记知识库（中文名亦可）→ `{kb_id, name}` |
| `GET` | `/api/kb/{kb_id}/` | `get_kb_status` | 单库状态（`kb_id`/`chunks`/`chunk_size`/`chunk_overlap`/`files`） |
| `DELETE` | `/api/kb/{kb_id}/` | `delete_kb` | 删除知识库（含原始文件） |
| `POST` | `/api/kb/{kb_id}/upload` | `upload_and_build` | 上传 + 建索引；参数 `chunk_size`/`chunk_overlap`/`mode` |
| `POST` | `/api/kb/{kb_id}/query` | `query_kb` | 同步问答 → `{answer, refs}` |
| `GET` | `/api/kb/{kb_id}/files` | `list_files` | 库内文件列表（记录缺失时用 Chroma 元数据兜底） |
| `DELETE` | `/api/kb/{kb_id}/files` | `delete_file` | 删单个文件（向量 + 原始文件 + 清单） |
| `GET` | `/api/kb/{kb_id}/file-content` | `get_file_content` | 预览正文（优先读留档，老库退回拼片段） |
| `POST` | `/api/kb/{kb_id}/rebuild` | `rebuild_kb` | 按新切片参数重建整库 |
| `POST` | `/api/kb/{kb_id}/import-url` | `import_single_url` | 抓单个 URL 入库 |
| `POST` | `/api/kb/{kb_id}/import-batch` | `import_batch_urls` | 批量抓 URL 入库 |
| `POST` | `/api/kb/import-search` | `preview_search` | 联网搜索预览（候选列表供勾选） |
| `GET` | `/api/kb/download` | `download_remote` | 代理下载远程文档（流式） |

> 权威索引在 `04_环境与部署.md` §4.3。**新增端点必须同步三处**（`04` 规则 6）：①`main.py` 挂载点 ②`04` §4.3 ③前端 `frontend/src/lib/api.ts`。

### 8.2 请求 / 响应与错误码

| 端点 | 请求 | 响应 |
| --- | --- | --- |
| `upload` | `files: list[UploadFile]`（multipart）+ query 参数 | `{kb_id, chunks, files}`；格式不支持 400；超限 400 |
| `query` | `question: str`、`top_k: int = 3` | `KBQueryResponse`：`{answer, refs}` |
| `import-search` | `ImportSearchRequest`：`keyword`、`max_results`（**`ge=1, le=20`**） | `{keyword, results: [{title, body, href}]}` |
| `import-url` / `import-batch` | `ImportURLRequest` / `ImportBatchRequest` | 单个 `{url, title, chunks, file}`；批量 `{total, ok, failed, details}` |
| `delete_file` | `DeleteFileRequest`：`name` | `{ok, removed, chunks}` |

**错误码约定**（`02` §11）：越权/不存在 **404**（不区分，防枚举）；参数非法 400；限流/配额 **429**；系统错误 500。
**429 由全局异常处理器产出**（`main.py`），**不要在端点里 `try/except`**——端点自己 catch 容易顺手转成 500，前端就分不清「系统出错」和「你今天用超了」。

### 8.3 `refs` 是 JSON 字符串

> ⚠️ **`refs` 是字符串形式的 JSON，前端消费前必须 `JSON.parse`。**
> `02` §6.3 与 §12 都写死了：**保持字符串，不要单方面改成数组**。

别把两个函数搞混：`_format_refs`（`rag_core.py:794-809`）产出 Markdown 文本（给模型与引用卡片看）；
`build_sources`（`:760-792`）产出结构化 dict 列表（给前端 `sources` 帧看）：

```python
# build_sources 每条的形状
{"id": 1, "source": "a.pdf", "page": 1, "score": 0.4555,
 "strength": "high", "preview": "...", "text": "..."}
```

- `score`：**真相似度**（越大越相关）；换算不了给 `None`，前端隐藏这一项；
- `strength`：`high`/`medium`/`low`，**必须与 `assess_grounding` 用同一阈值**（§5.4）；
- `page`：`metadata["page"] + 1`（**存 0-based，返回 1-based**）。

### 8.4 SSE 里与检索相关的帧（`api/chat.py`）

```jsonc
event: sources    { "items": [ {id, source, page, score, strength, preview, text}, ... ] }
event: grounding  { "strength": "medium", "best": 0.31, "count": 3, "weak": true }
```

`api/chat.py:537-544` 的注释说明了为什么 `grounding` 要单独发一帧、且必须在正文之前：

> 前端要在回答上方挂「依据较弱」提示条，**不能等 done——用户是边流边读的，提示必须赶在正文前面出现**。

---

## 9. 已知缺口（如实记录，附方案）

> 五条都来自 `01` §7 缺口清单，**归属全部是 B 线**。「写进规范，避免被当成'设计如此'」。

### 9.1 入库中途失败不回滚

| 项 | 内容 |
| --- | --- |
| 位置 | `api/kb.py:127-148`（`_build` 里的文件循环） |
| 证据 | `_build` 里**没有 `delete_by_source`**；全项目 `delete_by_source` 只有 `api/kb.py:264`（删单文件）一处 |
| 后果 | 40 个切片在第 6 批失败后**库里残 8 条**（10-12 实测） |
| 放大效应 | `EMBED_CONCURRENCY=4` 让「中途失败」从偶发变常态；失败后用户第一反应是重传 ⇒ `append` 模式下**重复片段**，`rebuild` 模式下更糟（旧 collection 已删，只剩残片） |
| 严重度 | 中（`01` §7 #5） |

**方案（按成本从低到高）**：① **立刻（约 10 行）**——`_build` 里每个文件入库前先 `pipe.delete_by_source(name)`，
**同名重传 = 替换，天然幂等**，两种模式的重复问题一起解决；② **把「复用」语义显式化（约 15 行）**——`create_kb` 命中已存在 `kb_id` 时
返回 `reused: true` + 现有文件数，前端 toast 改成「已复用「X」（库内现有 N 个文件），新增 M 个片段」，
**服务端要说清发生了什么，不能让客户端猜**（涉及 `frontend/**`，**必须先与申请人协商**）；
③ **兜底**——`_embed_and_store`（`rag_core.py:458-506`）记录已 upsert 的 id，异常时回滚；失败信息带上「已入库 N 条」。

### 9.2 `top_k` / `chunk_size` 无边界校验

| 位置 | 现状 |
| --- | --- |
| `api/chat.py:68` | `top_k: int = Field(default=config.TOP_K_DEFAULT, gt=0)` —— **只有 `gt=0`，无上界** |
| `api/kb.py:63` / `:279` / `:415` / `:421` | `chunk_size: int = 500` —— **裸 `int`**（分别对应 `upload` / `RebuildRequest` / `ImportURLRequest` / `ImportBatchRequest`） |

**为什么是问题**：`top_k=100000` 会让 `fetch_k = 300000`，Chroma 侧一次性物化巨量结果；`chunk_size=1` 会把一份文档切成几十万片——
而 `DAILY_EMBED_LIMIT` 是**事后**记账（`api/kb.py:149-151` 用真实切片数 `bump_usage`），**入口那道 `quota.guard` 只知道「还没超」，不知道这一次会切多少**。

```python
# 方案：常量建议加进 core/config.py 供全项目复用（需先通知申请人）
MAX_TOP_K = 20          # 候选池 3 倍 = 60 条，足够 MMR 挑，再多只是烧 embedding
MAX_CHUNK_SIZE = 2000   # 再大就失去"切片"的意义，检索精度反而下降

top_k: int = Field(default=config.TOP_K_DEFAULT, gt=0, le=MAX_TOP_K)
chunk_size: int = Field(default=500, gt=0, le=MAX_CHUNK_SIZE)
```

> **待确认**：Pydantic 的 `le=` 触发的是 **422**，与现有「参数非法 400」的约定不一致。
> 如果前端已按 400 解析，就改成端点内手动判断抛 400。**需与前端对齐。**

### 9.3 「标签钩子」仓库零命中，接口未定义

`01` §7 #10 与 `03` §5 都写「仓库**零命中**，接口未定义」；`03` §5 待拍板事项 #3 把它列为「由谁定义、什么形状」待定。
**归属是 B 线**（`01` §5 的 B 线交付内容明写「知识库、检索、搜索、**蒸馏**、**标签钩子**」），
`docs/挑战杯_最终分工与任务清单_v1.md` T12 要求你出「导入/采集/蒸馏/检索/**标签钩子**接口」。
**已存在的接口草案**（`docs/挑战杯_接口文档_申请人x林宏亮_知识库_v0.1.md` §3，**待你确认**）：

```http
GET  /api/profile          → { "tags": ["备考-考研", "薄弱-向量检索"], "learning_goal": "...", "updated_at": "..." }
POST /api/profile/tags     ← { "tags": ["薄弱-重排序"] } → 200
POST /api/profile/distill  ← Mock 触发口（测试用），真实蒸馏逻辑归林宏亮
```

该文档 §6 还留了 4 个待你回答的问题：`kb_search` 的 `top_k` 默认值 / 检索分数要不要展示 / profile 标签是自动写还是留手动入口 / 蒸馏触发频率。

> **待确认**：这组 `/api/profile*` 路径是否最终采用、由谁落 `api/profile.py`。该文件未出现在 `01` §3.1 的接口层表中——
> **新增路由文件时先与申请人确认归属**（`01` §5：「新增路由文件：由该业务归属人创建，文件名即归属」）。

### 9.4 「蒸馏检索」实现未做

`docs/前端页面与接口映射_v0.2.md`：「后端接口文档里只有「蒸馏检索」5 条契约，**未实现**」；
`docs/团队分支分析_20261013.md` #3 把「**蒸馏检索实现**」与联网搜索协同设计 / 申报书 AI 文字 / 标签钩子定义并列为四项明写「未做」。

**纪律背景**：`docs/挑战杯_会议通知_1013.md` 写明院赛申报截止 **10/20**，本次只需产出「方案文档 + Mock 演示 Demo」，
知识库蒸馏等高难业务**编码延后院赛后**。所以 10/20 前对「蒸馏」的交付物是**方案 + Mock 样例**，不是可运行代码。
`docs/挑战杯_接口文档_申请人x林宏亮_知识库_v0.1.md` §5 的时间表：10/14 交「知识库样例 / 检索样例 / 标签样例 Mock JSON」，10/15 交「知识库技术方案文档」。

> **待确认**：蒸馏的输出契约（写进哪个 kb？走 `add_chunks` 还是独立 collection？置信度阈值多少？）在任何文档里都没有定死。
> `docs/挑战杯_会议纪要分析与任务拆解.md` R1 只写了方向：「钩子 + 样例标注；**置信度阈值**；输出可纠偏 UI（用户可改）；先方案后编码」。
> **这条属于「先出方案再编码」，本指南不预设实现。**

### 9.5 混合检索（BM25 + RRF）与条件式查询改写 — 待确认

`01` §3.3 记录 `kb/rag_core.py` 为 `718` 行、附注「+387：混合检索 BM25+RRF、条件式查询改写」。

> ⚠️ **待确认**：当前工作区 `main`（`96e0dda`）上的 `kb/rag_core.py` 是 **855 行**，但**不含** BM25 / RRF / 查询改写实现
> （`grep BM25|RRF|rerank` 在 `kb/**` 零命中）。该能力应在 `dev` / `feat/ai-module-20261013` 分支上，**尚未回流 `main`**。
> 本文 §4 描述的是**当前 `main` 的真实链路**（纯向量 + MMR + 两阈值），不代表混合检索落地后的形态。**合并收口时本节需要重写。**

---

## 10. 测试要求

分级依据：`03_流程规范.md` §4.3。

### 10.1 离线用例（**每个 PR 必须全绿，数量只增不减**）

| 用例 | 覆盖什么 | 怎么跑 |
| --- | --- | --- |
| `tests/test_retrieve_select.py` | `select_diverse` 的离群过滤 / MMR 去重 / `_to_similarity` 值域 / `build_sources` 给真相似度 | `python tests/test_retrieve_select.py` |
| `tests/test_chroma_singleton.py` | Chroma 单例、读路径全复用、`PersistentClient` 全项目只 1 处 | `python tests/test_chroma_singleton.py` |
| `tests/test_pipelines_concurrency.py` | 注册表锁、原子写、瞬时占用重试、`kb_lock` 互斥、RLock 可重入 | `python tests/test_pipelines_concurrency.py` |
| `tests/test_upload_stream.py` | 分块读不 OOM、超限即停不留半成品、`check_declared_size` 前置拦截 | `python tests/test_upload_stream.py` |

**这四条都是「脚本式自测」**（模块级执行 + `sys.exit` 结尾），设计上就是 `python tests/xxx.py` 直接跑。
它们**必须**留在 `pytest.ini` 的 `--ignore` 列表里——被 pytest 在收集阶段整段执行会导致
`core.account` 建表只跑一次、临时库里 `no such table: users`（`04` §2.6）。

**调阈值之后必跑**：`test_retrieve_select.py` 的文件头注释写明了它的定位——

> 调 `RETRIEVE_MIN_SIM` / `RETRIEVE_MMR_LAMBDA` 之后跑一遍，确认阈值没有误杀或漏筛。

它同时是 `_to_similarity` 守卫值域的**唯一回归**（用例 3 / 3b / 3c）。

### 10.2 集成用例（**CI 不跑，合并到 `dev` 前手动跑**）

| 用例 | 覆盖什么 | 前置条件 |
| --- | --- | --- |
| `tests/test_kb_isolation.py` | ①同名库在不同人名下是两个库 ②拿到别人 `kb_id` 也进不去 ③列表只出自己的 ④无主老库默认不可见、显式认领后才归属 ⑤HTTP 层跨用户访问全 404 | 无需 `.env`；自动 monkeypatch 到临时目录 |
| `tests/test_web_import_creates_index.py` | ①空库 `get_or_open()` 拿不到实例（钉死根因）②`import_url` 能写进空库 ③`download_and_index` 追加生效 ④源码约束：`importer.py` 无 `get_or_open()` 调用 | 无需 `.env`；本机起 `http.server` + `DeterministicFakeEmbedding` |

```bash
python tests/test_kb_isolation.py
python tests/test_web_import_creates_index.py
```

### 10.3 改这些地方时必须同步改测试

| 改动 | 必须同步的测试 |
| --- | --- |
| `_to_similarity` 的值域守卫 | `test_retrieve_select.py` 用例 3 / 3b / 3c |
| `RETRIEVE_MIN_SIM` / `RETRIEVE_MMR_LAMBDA` | `test_retrieve_select.py`（全部） |
| `RETRIEVE_LOW_SIM` / `assess_grounding` 分级 | `test_grounding.py` + `test_p0_grounding_e2e.py` |
| `chroma_client()` 或任何读路径 | `test_chroma_singleton.py`（含源码扫描） |
| `add_file_records` / 原子写 / `kb_lock` | `test_pipelines_concurrency.py` |
| `kb/importer.py` 的写入路径 | `test_web_import_creates_index.py`（含源码扫描） |
| 归属门禁（`owner_of` / `get_or_create` / `_must_own`） | `test_kb_isolation.py` + `test_owner_isolation.py` |
| `core/netsafe.py` 的任何判断口径 | `test_agent_guard.py`（**硬要求**，见 §7.4） |

> **不写测试的功能不算完成**（`03` §4.3）。新增端点必须带真机冒烟记录（贴命令与输出）。

---

## 11. 提交前自检

```bash
# ---- 必跑三件套（03 §2.5）----
ruff check .        # 静态检查必须无错
pytest -q           # 离线测试必须全绿
git status          # 确认没有 .env / *.db / 密钥被误加

# ---- B 线专属：四条脚本式离线用例（pytest -q 不跑它们）----
python tests/test_retrieve_select.py
python tests/test_chroma_singleton.py
python tests/test_pipelines_concurrency.py
python tests/test_upload_stream.py

pytest tests/test_grounding.py -q            # 动过阈值时加跑
python tests/test_kb_isolation.py            # 改过导入/归属时手动跑
python tests/test_web_import_creates_index.py
git diff --cached                            # 逐行看一眼暂存区
```

**B 线专项检查清单**

- [ ] `ruff check .` 无错；`pytest -q` 全绿；四条脚本式用例全过
- [ ] 改了阈值 → `test_retrieve_select.py` 仍 ALL PASS，且 `build_sources` 的分级阈值**已同步**
- [ ] 改了 `retrieve_with_score` / `select_diverse` → `test_grounding.py` 过
- [ ] 未把 `_to_similarity` 的距离值域守卫改回 `[0,2]`
- [ ] 未在 `refs` / `sources` 里透出原始距离
- [ ] 新增/修改的 URL 抓取都过了 `assert_safe_url`，且**每一跳重定向都重新校验**
- [ ] 测试里的 IP 占位是 `203.0.113.x`（RFC 5737），没有真实 IP
- [ ] 没有在 `async def` 里写同步阻塞调用（一律 `await asyncio.to_thread(...)`）
- [ ] 没有提交 `.env` / `*.db` / `chroma_db/` / `data/kb_owners.json`
- [ ] 改了 `kb/**` 对外契约 → 已同步 `02_核心数据结构定义.md` 并通知前端
- [ ] 新增配置项到 `core/config.py` → **已先通知申请人**

### 11.1 本模块分支命名

`<类型>/<模块>-<简述>-<日期>`（`03` §1.2），模块名取 `kb` 或 `search`：

```bash
git checkout dev && git pull origin dev          # 永远基于 dev，不是 main
git checkout -b feat/kb-hybrid-search-20261020
git fetch origin && git rebase origin/dev        # 开发中定期同步
git push -u origin feat/kb-hybrid-search-20261020   # PR 目标分支：dev
```

| 场景 | 分支名示例 |
| --- | --- |
| 混合检索 BM25+RRF | `feat/kb-hybrid-search-20261020` |
| 检索性能优化 | `perf/kb-retrieve-cache-20261022` |
| 搜索收口到 `core/serper.py` | `refactor/search-unify-20261025` |
| 修导入/入库缺陷 | `fix/kb-import-rollback-20261021` |

> ❌ 反例：`test`、`mybranch`、`临时`、`新分支2`。**特性分支生命周期 ≤ 5 天。**

### 11.2 提交命名

`<类型>(<作用域>): <简述>`，作用域用 `kb` / `api` / `core`，简述中文、≤ 50 字、**结尾不加句号**。

```
✅ feat(kb): 混合检索加 BM25+RRF，召回率提升 18%
✅ perf(kb): 检索结果按库缓存，省掉重复 embedding 与向量查询
✅ refactor(search): 搜索调用统一收口到 core/serper.py
✅ fix(kb): 空库导入改用 add_chunks，修联网建库 100% 失败
✅ test(kb): 补 _to_similarity 值域守卫的回归用例

❌ 改了一下      ❌ fix bug      ❌ feat: 新增功能、修了bug、改了样式、更新文档
```

**一个提交只做一件事**：改 A 时顺手修了无关的 B，拆两个提交。**不要提交半成品**：每个提交都应让 `ruff check .` 与 `pytest -q` 通过。
**四条红线**（`规范/README.md` 四）：① **`.env` 永不入库**（`git add -f` 能绕过 `.gitignore`，**永远不要用**）；
② 不提交真实密钥 / 服务器 IP / 个人信息（占位用 `203.0.113.x`）；
③ **不跨模块改动他人文件而不通知**；④ 契约破坏性变更必须升级版本并同步前端。

---

## 12. 关键位置速查

```bash
python main.py                        # 起后端（只监听回环 127.0.0.1:8000）
curl http://127.0.0.1:8000/health     # {"status":"ok","version":"0.3.0 (W3)"}
python tests/test_retrieve_select.py  # 调阈值后的一键回归
python tools/migrate_owner.py         # 认领改造前的无主知识库（必须显式执行）
```

| 想改什么 | 文件 | 关键位置 |
| --- | --- | --- |
| 检索阈值 | `core/config.py` | `:64-90` |
| 候选池 / 离群过滤 / MMR | `kb/rag_core.py` | `select_diverse` `:232-303`、`retrieve_with_score` `:724-758` |
| 依据强度判定 | `kb/rag_core.py` | `assess_grounding` `:139-189`、`grounding_note` `:215-228` |
| 距离→相似度换算 | `kb/rag_core.py` | `_to_similarity` `:104-127` |
| 并发 embedding | `kb/rag_core.py` | `_embed_and_store` `:458-506` |
| 建库 / 归属 / 锁 | `kb/pipelines.py` | `create_kb` `:281`、`get_or_create` `:308`、`kb_lock` `:242` |
| 网页抓取 / 导入 | `kb/importer.py` | `_safe_get` `:34-51`、`import_url` `:72`、`search_preview` `:163` |
| SSRF 防护 | `core/netsafe.py` | `assert_safe_url` `:62-112` |
| 知识库端点 | `api/kb.py` | 见 §8.1 |

---

## 变更记录

| 日期 | 变更 | 人 |
| --- | --- | --- |
| 2026-10-13 | 首版：模块边界、知识库标识与归属、写入流水线、检索链路、双阈值设计权衡、联网搜索收口、SSRF 防护、接口契约、已知缺口、测试要求、提交自检 | 申请人 |
