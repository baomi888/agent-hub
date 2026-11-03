"""对话 → 知识库的沉淀通路（报告 v2 §4.1 P0「开通路」）。

为什么要有这个文件
------------------
用户在对话里讲清楚的业务规则、约束、偏好，此前唯一的去处是上下文压缩块
（`conversations.compressed_summary`，4000 字符预算、会被折叠）——
唯一能永久保真存放知识的知识库，没有从对话进来的门。

本模块补两件事（都不新造能力，全部复用既有组件）：
1. `extract_candidates()`：零 LLM 分流，从会话消息里挑出"值得入库的知识句"，
   分类复用 `core/context_compress.py` 的规则信号（classify_text / salience 四信号）；
2. `build_documents()`：把勾选的候选构造成带溯源 metadata 的 LangChain Document，
   交给 `RagPipeline.add_chunks()`（自动建库 + 向量指纹）入库。

设计红线（报告 §4.1「不做的事」）：
**不做自动入库**。自动入库会把寒暄、试错、错误中间态一起写进去且不可逆污染，
必须人工确认 + 可预览 + 可勾选——所以这里只产"候选清单"，入库由 API 层
（`POST /api/kb/{kb_id}/import-dialogue`）在用户勾选后执行。
"""

from __future__ import annotations

import re

from langchain_core.documents import Document

from core.context_compress import (
    KIND_WEIGHTS,
    classify_text,
    compute_salience,
    jaccard_sim,
    outlier_score,
    singleton_ratio,
)

# 与 extractive_compress 同款切句口径：中英文句末 + 换行
_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;])\s*|\n+")

# 纠正抑制的相似度门槛：被纠正的前句与纠正句 Jaccard 超过该值才判"同一件事"
_SUPERSEDE_SIM = 0.4

# ---------- 判据修补（2026-10-31）----------
#
# `classify_text` 是给「压缩保真」调的，拿来做「知识入库」的准入判据有两处偏：
#
# ① 假阳性：`_RE_MEMORIZE` 抓的是「记住|别忘了|以后都|记一下」，本意是识别
#    *用户* 要求助手记住的指令。但助手回一句「好的，我记住了」也会命中，
#    还被判成 `事实` + emotion 满格 —— 于是回声成了 salience 最高的候选。
#    实测：「好的，我记住了。」→ ('事实', 1.0)。
#
# ② 假阴性：`事实` 支的判据是 `extract_entities()`，它只认 URL / UUID / hex /
#    文件名 / 待办词，**不认业务编号**。所以「负责人是老陈，工号 KX-3301」
#    这种最该沉进库里的句子被判成 `其它`，直接出局。
#
# 两处都**不改 `classify_text`**：它同时供压缩链路的 L0 保真校验使用，
# 放宽实体识别会让压缩块的校验几乎总被拒收。修补只发生在本模块的准入层。
#
# ③ 还有一处不在分类器里、而在"收谁的话"：助手的长回答按句切分后命中率极高，
#    真实语料上占了 92% 的候选。默认角色范围改成只收 user（见 extract_candidates）。
#
# ① 的回声形态：「我记住了 / 记住了 / 已经记下啦」——动词后带完成态助词。
#    指令形态「千万记住，这账号不能注销」没有「了」，不会被误伤。
_RE_MEMO_ECHO = re.compile(r"(已经|都)?\s*(记住|记下|记好|记牢|牢记)(了|啦|咯)")

# ② 的业务编号：带业务标签的编号（工号/学号/项目号…），或裸的字母-数字代号。
#    刻意不收录「日期 / 星期 / 地点」——那些词遍地都是，一并收进来会把
#    整段对话变成候选，预览页反而没法用。宁可少收，用户还能手动挑。
_RE_BIZ_ID = re.compile(
    r"(?:工号|学号|编号|项目号|单号|账号|代号|型号|房号|座位号)"
    r"\s*[:：]?\s*[A-Za-z0-9][A-Za-z0-9\-_]{1,19}"
    # 裸代号用「非字母数字」边界而不是 \b：中文紧贴时（"找A12-3344这人"）
    # \b 在汉字与字母之间不成立，会漏。字母位数放到 1 位是为了 A12-3344、B2-07
    # 这类区号/教室号；数字下限仍是 2 位，把 UTF-8 / GPT-4 / MP3 挡在外面。
    r"|(?<![A-Za-z0-9])[A-Z]{1,6}[-_]?\d{2,6}(?![A-Za-z0-9])"
)


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text or "") if s.strip()]


def _is_memo_echo(sent: str) -> bool:
    """「我记住了」这类应答回声——它在复述指令，本身不含任何知识。"""
    return bool(_RE_MEMO_ECHO.search(sent))


def _biz_fact_kind(sent: str) -> bool:
    """含业务编号的陈述句。用来补 `事实` 支漏掉的硬实体。"""
    return bool(_RE_BIZ_ID.search(sent))


def _suppress_superseded(cands: list[dict]) -> list[dict]:
    """纠正信号的后处理（报告 §5.2 的第一版实现）。

    纠正句（"不对，我说的是 X"）入库时，同批里与它相似的**更早**句子
    不再入库——否则新旧两个说法都在库里，检索时互相冲突且用户无法分辨。
    跨批次（更早压缩段里已入库的句子）的作废标记依赖检索侧过滤，
    本版不实现（如实标注，见文档"未做"清单）。
    """
    kept: list[dict] = []
    for c in cands:
        if c["kind"] == "纠正":
            for prev in kept:
                if jaccard_sim(prev["text"], c["text"]) >= _SUPERSEDE_SIM:
                    prev["superseded"] = True
        kept.append(c)
    return [c for c in kept if not c.get("superseded")]


def extract_candidates(
    messages: list[dict], *, roles: tuple[str, ...] = ("user",)
) -> list[dict]:
    """零 LLM 分流：从会话消息里挑出知识候选句。

    判据（报告 §4.3 的规则表，命中任一即候选）：
      - kind != 其它：纠正 / 约束（绝对化或否定）/ 待办 / 偏好 / 目标 / 事实（硬实体）
    "显式记住"在 classify_text 里映射为 kind=事实 + emotion 顶格，天然是候选。

    在这条基线之外补三道（2026-10-31，全部只在本模块，不动共享分类器）：
      - **角色范围**：默认只收 user。助手的长回答按句切分后命中率极高——
        本地真实库 181 条消息跑出来 194 条候选，其中 179 条来自助手
        （每条助手消息平均 2.03 条），抽样是「## 🗓️ 国庆广州游计划」
        「**今日待办**」这种整段生成的排版垃圾。压缩链路要保全两种角色，
        由调用方显式传 `roles=("user","assistant")`。

        ★ 2026-11-01 补记：这个默认值**只对压缩链路成立**。沉淀场景不要用它 ——
          它会把助手整理好的内容一并丢掉（实测 754 字的听力提分回答提出 0 条）。
          沉淀入口请用 `extract_blocks()`：user 按句、assistant 按结构块。
      - 挡掉「我记住了」这类**应答回声**（复述指令，不是知识）；
      - 补上含**业务编号**的陈述句（`extract_entities` 不认 KX-3301 这种代号）。

    salience 按全会话语料计算 singleton（"只出现一次但重要"）与离群度。
    返回字段：{text, kind, refs, role, ts, salience}，text 为**原句不改写**
    （抽象会不可逆损失原文，知识库的价值恰恰在可溯源）。
    """
    valid = [
        m
        for m in (messages or [])
        if m.get("role") in roles and (m.get("content") or "").strip()
    ]
    if not valid:
        return []
    corpus = "\n".join(m.get("content") or "" for m in valid)
    all_texts = [m.get("content") or "" for m in valid]

    out: list[dict] = []
    for m in valid:
        role = m["role"]
        mid = int(m.get("id") or 0)
        ts = int(m.get("created_at") or 0)
        for sent in _split_sentences(m.get("content") or ""):
            # 回声优先级最高：它恰好会被 classify_text 判成「事实 + 满格 emotion」，
            # 不先挡掉的话，它会稳定排在预览列表最前面。
            if _is_memo_echo(sent):
                continue
            kind, emotion = classify_text(sent)
            if kind == "其它":
                if not _biz_fact_kind(sent):
                    continue
                # 补判：按「事实」入场，emotion 取 classify_text 里事实支的原值 0.3，
                # 让它排在真正的约束/纠正之后，不抢注意力。
                kind, emotion = "事实", 0.3
            salience = compute_salience(
                kind,
                emotion,
                singleton_ratio(sent, corpus),
                outlier_score(sent, all_texts),
            )
            out.append(
                {
                    "text": sent,
                    "kind": kind,
                    "refs": [mid],
                    "role": role,
                    "ts": ts,
                    "salience": salience,
                }
            )
    return _suppress_superseded(out)


# ---------- 助手回答的结构分块（2026-11-01）----------
#
# 为什么要另起一条路，而不是把 `roles` 改回双角色
# ----------------------------------------------
# 2026-10-31 把准入范围收成只收 user，理由是「助手长回答占了 92% 的候选，
# 抽样是『## 🗓️ 国庆广州游计划』『**今日待办**』这种整段生成的排版垃圾」。
# 这个判断错了一半：
#   · 对的：助手的**回声**（「好的，记下了：周会每周四 15:00」）和**追问**
#     （「需要我再展开吗」）确实是垃圾，不该进库；
#   · 错的：助手整理过的内容恰恰是整个沉淀功能最该收的。实测用户那张截图里
#     754 字的「六级听力提分」回答 —— 只收 user 时提出 **0 条**；
#     就算把角色改回双角色也只提出 **1 条**（「不要纠结上一题」，
#     仅仅因为含"不要"命中否定规则），精听真题 / 跟读模仿 / 同义替换
#     这些一条都出不来。
#
# 所以根因不是角色，是**粒度和判据**：
#   ① `classify_text` 只认 纠正 / 约束 / 待办 / 偏好 / 目标 / 硬实体 七类信号。
#      「精听真题：选一段 1-2 分钟的真题音频，逐句听写」不含任何一类 ——
#      它是知识，只是不属于那七类「对话里的事实」。问它等于拿错尺子。
#   ② 按句切会把一个小节拆成孤立碎片：检索命中"逐句听写"时，
#      不知道它属于「听力基础训练」这个主题。
#
# 因此助手消息改用**结构分块**：按 markdown 标题成块，块自带标题与列表；
# 准入判据换成「这块是不是整理过的内容」（有列表 / 有表格 / 有代码 / 够长），
# 不再问 classify_text。
#
# 为什么不直接改 `extract_candidates`：它在 `core/context_compress.py` 里
# 给压缩段打 kind/salience（决定折叠顺序），那套语义依赖「按句 + 七类权重」。
# 两个场景本来就不是一回事，拆成两个函数，谁也不会误用。
#
# 为什么不改 `KIND_WEIGHTS`：压缩侧用 `KIND_WEIGHTS.get(kind, 0.0)` 取段内最大权重，
# 往共享表里塞新 kind 会悄悄改变每一段的折叠顺序。这里的 "要点"/"说明"
# 只在本模块内使用，不进那张表。

_RE_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+\S")
_RE_FENCE = re.compile(r"^\s*(?:```|~~~)")
_RE_BULLET = re.compile(r"^\s*(?:[-*+•]|\d{1,2}[.、)])\s+\S")
_RE_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_RE_HR = re.compile(r"^\s*(?:[-*_]\s*){3,}$")

# 短于此的一律不收——「好的」「明白」「需要我再展开吗」都在这一档
_A_MIN_CHARS = 25
# 没有结构（无标题/列表/表格/代码）的纯段落要够长，才算"讲清楚了一件事"
_A_PARA_MIN = 80
# 超长块再切：754 字整条进向量库，召回一条就占满 top-k，精度反而下降
_A_MAX_CHARS = 1200


def _split_md_blocks(content: str) -> list[str]:
    """按 markdown 标题分组；代码围栏里的 `#` 不当标题。"""
    blocks: list[str] = []
    cur: list[str] = []
    in_fence = False
    for ln in (content or "").split("\n"):
        if _RE_FENCE.match(ln):
            in_fence = not in_fence
            cur.append(ln)
            continue
        if in_fence:
            cur.append(ln)
            continue
        if _RE_HR.match(ln):  # 分隔线本身不是内容
            continue
        if _RE_HEADING.match(ln):
            if cur:
                blocks.append("\n".join(cur).strip())
                cur = []
            cur.append(ln)
            continue
        cur.append(ln)
    if cur:
        blocks.append("\n".join(cur).strip())
    return [b for b in blocks if b]


def _block_has_structure(block: str) -> bool:
    lines = block.split("\n")
    return (
        bool(_RE_HEADING.match(lines[0] if lines else ""))
        or any(_RE_BULLET.match(l) for l in lines)
        or any(_RE_TABLE_ROW.match(l) for l in lines)
        or "```" in block
    )


def _refine_block(block: str) -> list[str]:
    """有结构的整块保留；纯叙述按空行再分段。

    列表项之间常有空行（`- a` 空行 `- b`），按空行切会把一个列表拆散，
    所以只对"完全没有结构"的块做二次分段。
    """
    if _block_has_structure(block):
        return [block]
    return [p.strip() for p in re.split(r"\n\s*\n", block) if p.strip()]


def _cap_block(block: str) -> list[str]:
    """超长块按行累积切开，尽量不切断一个列表项。"""
    if len(block) <= _A_MAX_CHARS:
        return [block]
    out: list[str] = []
    cur: list[str] = []
    size = 0
    for ln in block.split("\n"):
        if size + len(ln) + 1 > _A_MAX_CHARS and cur:
            out.append("\n".join(cur).strip())
            cur = []
            size = 0
        cur.append(ln)
        size += len(ln) + 1
    if cur:
        out.append("\n".join(cur).strip())
    return [b for b in out if b]


def _block_is_knowledge(block: str) -> bool:
    """这块值不值得进库。

    注意这里**不问 classify_text**：助手整理出来的内容（"精听真题：选一段…"）
    在七类信号里是"其它"，按那套判据永远出不来。
    """
    t = block.strip()
    if len(t) < _A_MIN_CHARS:
        return False
    lines = t.split("\n")
    bullets = sum(1 for l in lines if _RE_BULLET.match(l))
    if bullets >= 2:  # 整理过的清单
        return True
    if any(_RE_TABLE_ROW.match(l) for l in lines):
        return True
    if "```" in t:
        return True
    if _RE_HEADING.match(lines[0]):
        return True
    return len(t) >= _A_PARA_MIN  # 纯叙述要够长


def _block_kind(block: str) -> str:
    return "要点" if _block_has_structure(block) else "说明"


def _block_salience(block: str) -> float:
    """按信息量给分：小节越完整越靠前，但不超过手工强调的那几类。"""
    return round(min(0.9, 0.45 + min(len(block), 600) / 1500.0), 3)


def _assistant_candidates(m: dict) -> list[dict]:
    mid = int(m.get("id") or 0)
    ts = int(m.get("created_at") or 0)
    out: list[dict] = []
    for raw in _split_md_blocks(m.get("content") or ""):
        for block in _refine_block(raw):
            for piece in _cap_block(block):
                if not _block_is_knowledge(piece):
                    continue
                out.append(
                    {
                        "text": piece,
                        "kind": _block_kind(piece),
                        "refs": [mid],
                        "role": "assistant",
                        "ts": ts,
                        "salience": _block_salience(piece),
                    }
                )
    return out


def extract_blocks(
    messages: list[dict], *, roles: tuple[str, ...] = ("user", "assistant")
) -> list[dict]:
    """沉淀入口：**按说话人用两套完全不同的策略**。

    - user：一句话就是一条事实，按句切 + `classify_text` 分类（同 extract_candidates）
    - assistant：一段回答是一个主题，按 markdown 结构切块，只看"是不是整理过的内容"

    两种角色的信息形态本来就不同，用同一把尺子必然有一边是错的 ——
    这正是 10-31 那次"收成只收 user"的由来（拿句子尺子量长文，全是碎片，
    于是判定长文是垃圾）。

    返回结构与 `extract_candidates` 一致（text/kind/refs/role/ts/salience），
    前端靠 `role` 分红绿两组展示。
    """
    msgs = [
        m
        for m in (messages or [])
        if m.get("role") in roles and (m.get("content") or "").strip()
    ]
    if not msgs:
        return []

    out: list[dict] = []
    if "user" in roles:
        out.extend(
            extract_candidates([m for m in msgs if m.get("role") == "user"], roles=("user",))
        )
    if "assistant" in roles:
        for m in msgs:
            if m.get("role") == "assistant":
                out.extend(_assistant_candidates(m))
    # 时间序：前端分组展示，但组内仍按对话先后，用户能顺着回忆找
    out.sort(key=lambda c: (int(c.get("ts") or 0), (c.get("refs") or [0])[0]))
    return out


def drop_existing(picked: list[dict], existing: set[str]) -> list[dict]:
    """剔掉这个库里已经沉淀过的原句。

    ★ 为什么需要：向量库给每条都生成 `uuid4()` 当 id，同一句话存第二次
    就是两条互不相干的片段。而沉淀入口是「用户随时能再点一次」的交互，
    不去重的话同一条知识会重复占用检索 top-k 的名额。

    ★ 为什么按原文精确比对就够：候选是**原句不改写**的（见 extract_candidates
    的注释），所以同一句话两次抽出来的字符串完全一致，不需要模糊匹配。

    ★ 为什么不在这里判空：`picked` 为空要不要报 400 是 API 层的事
    （全被跳过 ≠ 参数非法），这层只负责筛。
    """
    if not existing:
        return picked
    keep = [c for c in picked if (c.get("text") or "").strip() not in existing]
    return keep


def build_documents(items: list[dict], sid: str, conv_title: str) -> list[Document]:
    """把勾选的候选构造成入库 Document（metadata 全部可溯源）。

    source 形如 `对话·<会话标题>`——沉淀后在新会话检索到时，
    用户能判断这条知识来自哪次对话（报告 §4.1 验收口径）。
    """
    docs: list[Document] = []
    for i, it in enumerate(items):
        text = (it.get("text") or "").strip()
        if not text:
            continue
        refs = [int(r) for r in (it.get("refs") or []) if r]
        docs.append(
            Document(
                page_content=text,
                metadata={
                    "source": f"对话·{conv_title}",
                    "chunk_index": i,
                    "dialogue_sid": sid,
                    "kind": it.get("kind") or "事实",
                    "role": it.get("role") or "user",
                    "ts": int(it.get("ts") or 0),
                    "message_id": refs[0] if refs else 0,
                    "salience": float(it.get("salience") or 0.0),
                },
            )
        )
    return docs


def rank_for_preview(cands: list[dict]) -> list[dict]:
    """给预览列表排序：纠正 > 显式强调 > 类型权重 > salience。

    前端勾选页按这个顺序展示，默认勾选 salience 高于阈值的部分。

    ★ 助手的结构块**不参与重排**（2026-11-01）：它们的顺序就是原文的章节顺序
      （一、二、三、四），而上面这套键会按 salience 排 —— salience 是按块长度
      给的，于是「二、题型技巧」（160 字）会跑到「一、基础」（143 字）前面。
      用户读到第二章才发现第一章在后面，逻辑是断的。所以助手块原样保持
      `extract_blocks` 给出的时间序，整体排在用户候选之后（前端另行分组）。
    """
    mine = [c for c in cands if c.get("role") != "assistant"]
    theirs = [c for c in cands if c.get("role") == "assistant"]
    mine.sort(
        key=lambda c: (
            c.get("kind") == "纠正",
            KIND_WEIGHTS.get(c.get("kind") or "其它", 0.0),
            c.get("salience") or 0.0,
        ),
        reverse=True,
    )
    return mine + theirs


__all__ = [
    "build_documents",
    "drop_existing",
    "extract_blocks",
    "extract_candidates",
    "rank_for_preview",
]
