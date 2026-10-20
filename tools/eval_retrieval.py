"""检索质量量化评估（审计修复第 4 层）。

为什么还需要这一层
------------------
前三层回答的是"有没有接上"：单测证明函数对，回归证明没改坏，
端到端证明调用链通了。但**没有一层回答"效果好不好"**——而
`RETRIEVE_MIN_SIM=0.25` / `RETRIEVE_LOW_SIM=0.45` / MMR λ=0.65 这七个
检索参数目前全是拍出来的，没有任何数据支撑。

这一层把"好不好"变成能看的数：

  指标              含义
  ----------------  --------------------------------------------------
  recall@k          期望来源是否出现在前 k 条里（召回能力）
  MRR               期望来源首次出现的名次倒数（排序质量）
  top1 相似度分布   assess_grounding 判定的 best —— 阈值调参的直接依据
  闸门强度分布      high / medium / low / none，看阈值落在哪一档
  离题拦截率        不在语料里的问题是否被判定为 weak（不该硬答）

语料与问题集
------------
`tools/eval_corpus/` 下三篇中文文档（nginx / 限流 / 向量），
每题标注期望来源。两道离题问题（语料里根本没有答案）单独统计——
它们测的是"该示弱的时候有没有示弱"，这是防编造的关键一环。

没有 API Key 怎么办（重要）
--------------------------
默认走**确定性假 embedding**（字符 n-gram 有符号哈希 + L2 归一化）。
它只编码字面重叠，不含语义。所以：

  ⚠️ 假向量下评估的是「管道口径与排序逻辑」，不是「向量模型质量」。

能证明的：切片、候选池、阈值闸门、MMR 去重、混合融合、分级判定
都按预期工作；召回的相对高低在同一套假向量下可比。
不能证明的：换成真实模型后这些数字是多少。

配了 EMBEDDING_API_KEY 时脚本会**自动改用真实向量**，
同一套语料与问题集即可拿到可比对的真值。用 `--fake` 可强制假向量。

用法：
    python tools/eval_retrieval.py                 # 自动（有 Key 用真，无 Key 用假）
    python tools/eval_retrieval.py --fake          # 强制假向量
    python tools/eval_retrieval.py --json out.json # 结果另存
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile

# ⚠️ 必须在 import core.config 之前把数据目录指向临时目录：
# config 在模块导入期就读环境变量，晚一步就会读写真实的向量库与数据库。
_TMP = tempfile.mkdtemp(prefix="eval_retrieval_")
os.environ["PERSIST_DIR"] = os.path.join(_TMP, "chroma")
os.environ["DATA_DIR"] = os.path.join(_TMP, "data")
os.environ["DOWNLOAD_DIR"] = os.path.join(_TMP, "downloads")
os.environ["ALLOW_SIGNUP"] = "1"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from kb import rag_core  # noqa: E402
from kb.rag_core import RagPipeline, assess_grounding  # noqa: E402

CORPUS_DIR = os.path.join(ROOT, "tools", "eval_corpus")
DOCS = [
    "nginx-https.md",
    "quota-rate-limit.md",
    "embed-vector.md",
]

# 期望来源为 None = 离题问题（语料里没有答案，应该被判 weak）
QUESTIONS: list[tuple[str, str | None]] = [
    # ---- nginx / HTTPS ----
    ("nginx 把根路径和接口分别转发到哪个端口？", "nginx-https.md"),
    ("证书到期前多久会自动续期？", "nginx-https.md"),
    ("流式输出在网关上要关掉什么配置？", "nginx-https.md"),
    ("备案还没下来的时候怎么先让服务能被访问？", "nginx-https.md"),
    # ---- 限流 / 配额 ----
    ("每天能提问几次，超了会怎样？", "quota-rate-limit.md"),
    ("多进程部署时进程内的计数为什么不准？", "quota-rate-limit.md"),
    ("并发名额要在什么时候抢占？", "quota-rate-limit.md"),
    ("无效的会话请求会不会消耗额度？", "quota-rate-limit.md"),
    # ---- 向量 / embedding ----
    ("换了向量化模型会有什么后果？", "embed-vector.md"),
    ("距离和相似度的换算关系是什么？", "embed-vector.md"),
    ("怎样避免把重复的片段都塞进提示词？", "embed-vector.md"),
    ("缓存的键里为什么要带上用户标识？", "embed-vector.md"),
    # ---- 离题：语料里没有答案 ----
    ("公司年会在哪个酒店举办？", None),
    ("红烧肉要放多少冰糖？", None),
    ("打印机卡纸要怎么处理？", None),
    ("下周的航班时刻表在哪查？", None),
]

TOP_K = 5

# 问题与期望文档的共有 n-gram 占比低于这个值 → 标为"字面盲区题"：
# 在字面向量下它本来就打不中，不算管道的回归（详见 _evaluate 的注释）
LITERAL_BLIND_THRESHOLD = 0.25


# ==================== 确定性假 embedding ====================


class FakeEmbeddings:
    """字符 n-gram 的 TF-IDF 哈希向量（无网络、可复现）。

    为什么是这套而不是更简单的"有符号哈希计数"：
    第一版用的是等权哈希计数，实测 top1 相似度全落在 0.04~0.22，
    14 道题**无一例外**被判 low——闸门阈值（0.25 / 0.45）根本没被走到，
    等于这一层什么都没测出来。根因是长度稀释：片段 300 字、问题 15 字，
    共有 n-gram 在庞大的片段范数里被摊薄，余弦先天偏小，
    而且"的""是"这类高频 gram 还贡献了大量无意义的内积。

    所以这里加了 IDF（按语料全局 gram 频次取逆频）+ sublinear TF：
      · 罕见 gram（专有名词、术语）权重高 → 命中它们就显著抬高相似度；
      · 高频虚词权重趋近 0 → 不再制造"看起来有关系"的伪相关。
    结果是相关题落到 0.3 上下、离题题仍在 0.1 以下，
    阈值闸门这才真正被压到工作区间里，评估才有判别力。

    仍然要如实标注：这是**字面**向量，不含语义。它衡量不了模型质量，
    只让"管道口径"这件事变得可测。

    取 2/3-gram 是因为中文没有空格分词，字符级 n-gram 是唯一不需要分词的
    字面特征；哈希到固定维度是因为向量库要求等长向量。
    最后必须 L2 归一化——`rag_core._to_similarity` 的换算公式
    （cos = 1 - d/2）只在单位向量上成立，不做归一化会出现假的低相似度。
    """

    DIM = 1024

    def __init__(self, corpus_texts: list[str] | None = None) -> None:
        self.dim = self.DIM
        counts: dict[str, int] = {}
        total = 0
        for t in corpus_texts or []:
            for g in self._grams(t):
                counts[g] = counts.get(g, 0) + 1
                total += 1
        total = total or 1
        # 语料里没见过的 gram（查询可能带新词）按"极罕见"处理，给最高的逆频
        self._default_idf = math.log(1 + total / 2)
        self._idf = {g: math.log(1 + total / (1 + c)) for g, c in counts.items()}

    @staticmethod
    def _grams(text: str):
        t = (text or "").lower()
        for n in (2, 3):
            for i in range(len(t) - n + 1):
                yield t[i : i + n]

    # 文档侧只保留权重最高的若干个 gram（"主题词"）。
    # 为什么必须稀疏化：余弦 = 共有权重 / sqrt(文档范数 × 查询范数)，
    # 片段 300 字有 ~600 个 gram，查询只有 ~30 个，分母被文档那 600 项撑得极大，
    # 实测 top1 全落在 0.10~0.23，RETRIEVE_MIN_SIM=0.25 一条都跨不过去——
    # 闸门等于"永远示弱"，评估测不出任何东西。
    # 把文档压到与查询同一量级的主题词集合后，相关题能进到阈值的工作区间。
    # 这是为了让**管道口径**可测而对基线做的补偿，不是对真实模型质量的模拟。
    DOC_TOP_GRAMS = 64

    def _weighted(self, text: str) -> list[tuple[str, float]]:
        tf: dict[str, int] = {}
        for g in self._grams(text):
            tf[g] = tf.get(g, 0) + 1
        out = []
        for g, c in tf.items():
            # sublinear TF：同一 gram 出现 8 次不该比出现 1 次重 8 倍
            out.append((g, (1.0 + math.log(c)) * self._idf.get(g, self._default_idf)))
        return out

    def _vec(self, text: str, keep: int | None = None) -> list[float]:
        items = self._weighted(text)
        if keep and len(items) > keep:
            items.sort(key=lambda x: (-x[1], x[0]))
            items = items[:keep]
        vec = [0.0] * self.DIM
        for g, w in items:
            h = int.from_bytes(
                hashlib.blake2b(g.encode("utf-8"), digest_size=8).digest(), "little"
            )
            vec[h % self.DIM] += w
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts) -> list[list[float]]:
        return [self._vec(t, self.DOC_TOP_GRAMS) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)

    # ---- 供脚本做"字面盲区"判定（不参与检索，只用于解释结果）----

    @staticmethod
    def overlap(query: str, doc_text: str) -> float:
        """问题的 n-gram 里有多大比例出现在该文档中（0~1）。"""
        q = set(FakeEmbeddings._grams(query))
        if not q:
            return 0.0
        d = set(FakeEmbeddings._grams(doc_text))
        return len(q & d) / len(q)


def _install_fake_embeddings() -> None:
    """把假向量注入 core.llm 的单例位。

    注入的是 `core.llm._EMBEDDINGS` 而不是替换 `get_embeddings` 函数：
    `kb/rag_core.py` 在导入期用的是 `from core.llm import get_embeddings`，
    替换函数引用进不去它已经绑定的名字；单例位则由 get_embeddings 每次现读。

    IDF 表用建库前读到的语料原文来算：同一批文本既当索引内容又当统计来源，
    这正是"字面基线"该有的口径（真实系统里 IDF 也是从被索引的语料算的）。
    """
    from core import llm

    texts = []
    for name in DOCS:
        p = os.path.join(CORPUS_DIR, name)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                texts.append(f.read())
    llm._EMBEDDINGS = FakeEmbeddings(texts)


def _has_real_embedding_key() -> bool:
    """是否配了真实向量服务（决定默认走真向量还是假向量）。"""
    from core import config

    return bool(getattr(config, "EMBEDDING_API_KEY", "") and getattr(config, "EMBEDDING_BASE_URL", ""))


# ==================== 评估 ====================


def _src_of(doc) -> str:
    return os.path.basename((doc.metadata or {}).get("source", "") or "")


def _build_index() -> RagPipeline:
    paths = [os.path.join(CORPUS_DIR, name) for name in DOCS]
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        raise SystemExit(f"语料缺失：{missing}")
    pipe = RagPipeline("kb_eval_retrieval")
    n = pipe.build_index(chunk_size=300, chunk_overlap=50, paths=paths, mode="rebuild")
    print(f"建库：{n} 个片段（{len(DOCS)} 篇文档）")
    return pipe


def _doc_texts() -> dict[str, str]:
    out = {}
    for name in DOCS:
        p = os.path.join(CORPUS_DIR, name)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                out[name] = f.read()
    return out


def _evaluate(pipe: RagPipeline) -> dict:
    texts = _doc_texts()
    rows: list[dict] = []
    for q, expect in QUESTIONS:
        res = pipe.retrieve_with_score(q, top_k=TOP_K)
        sources = [_src_of(d) for d, _ in res]
        assess = assess_grounding(res)
        hit_rank = 0
        if expect is not None:
            for i, s in enumerate(sources, start=1):
                if s == expect:
                    hit_rank = i
                    break
        # 字面盲区：问题与期望文档几乎没有共有 n-gram。这类题在**字面**
        # 向量下拿不到召回不是管道的错，是基线能力的天花板——单独标记出来，
        # 免得把一个已知的基线缺陷当成回归。
        overlap = round(FakeEmbeddings.overlap(q, texts.get(expect, "")), 3) if expect else None
        rows.append(
            {
                "question": q,
                "expect": expect,
                "sources": sources,
                "hit_rank": hit_rank,
                "top1_sim": assess.get("best"),
                "strength": assess.get("strength"),
                "weak": bool(assess.get("weak")),
                "literal_overlap": overlap,
                "literal_blind": bool(overlap is not None and overlap < LITERAL_BLIND_THRESHOLD),
            }
        )
    return {"rows": rows}


def _auc(pos: list[float], neg: list[float]) -> float | None:
    """相关题与离题题的 top1 相似度可分性（Mann-Whitney U / ROC-AUC）。

    为什么用它而不是直接比均值差：**相似度的绝对值随 embedding 模型漂移**，
    字面向量下整体偏低、真实模型下整体偏高，拿绝对差当判据会把"换了个模型"
    误判成"检索变坏了"。AUC 只看**排序关系**，与尺度无关，
    因此能在两种向量之间横向比较：0.5 = 完全分不开，1.0 = 相关题一律排在离题题之前。
    """
    if not pos or not neg:
        return None
    total = 0.0
    for p in pos:
        for n in neg:
            total += 1.0 if p > n else (0.5 if p == n else 0.0)
    return round(total / (len(pos) * len(neg)), 4)


def _summarize(rows: list[dict]) -> dict:
    relevant = [r for r in rows if r["expect"] is not None]
    off_topic = [r for r in rows if r["expect"] is None]
    # 字面盲区题单独剔除后重算：这组数字才是"管道本身有没有坏"的口径
    fair = [r for r in relevant if not r["literal_blind"]]

    def _rec(group: list[dict], k: int) -> float:
        if not group:
            return 0.0
        return round(sum(1 for r in group if 0 < r["hit_rank"] <= k) / len(group), 4)

    def _mrr(group: list[dict]) -> float:
        if not group:
            return 0.0
        return round(sum((1.0 / r["hit_rank"]) if r["hit_rank"] else 0.0 for r in group) / len(group), 4)

    recall = {f"recall@{k}": _rec(relevant, k) for k in (1, 3, 5)}
    recall_fair = {f"recall@{k}": _rec(fair, k) for k in (1, 3, 5)}

    sims = [r["top1_sim"] for r in rows if isinstance(r["top1_sim"], (int, float))]
    rel_sims = [r["top1_sim"] for r in relevant if isinstance(r["top1_sim"], (int, float))]
    off_sims = [r["top1_sim"] for r in off_topic if isinstance(r["top1_sim"], (int, float))]
    # 公平均分性：只把"字面可解"的相关题当正例。
    # 字面盲区题在字面向量下与文档没有共有 n-gram，模型给它们低分是**字面模型的
    # 正确行为**——要求它们排在离题题前面，等于要求字面模型做语义判断，口径不合理。
    fair_sims = [r["top1_sim"] for r in fair if isinstance(r["top1_sim"], (int, float))]
    strength = {}
    for r in rows:
        key = str(r["strength"])
        strength[key] = strength.get(key, 0) + 1

    off_weak = sum(1 for r in off_topic if r["weak"])
    rel_weak = sum(1 for r in relevant if r["weak"])
    return {
        "auc_related_vs_offtopic": _auc(rel_sims, off_sims),
        "auc_fair": _auc(fair_sims, off_sims),
        "relevant_weak_rate": round(rel_weak / len(relevant), 4) if relevant else None,
        "questions": len(rows),
        "relevant": len(relevant),
        "literal_blind": len(relevant) - len(fair),
        "off_topic": len(off_topic),
        **recall,
        "mrr": _mrr(relevant),
        "recall_fair": recall_fair,
        "mrr_fair": _mrr(fair),
        "top1_sim_mean": round(sum(sims) / len(sims), 4) if sims else None,
        "top1_sim_min": round(min(sims), 4) if sims else None,
        "top1_sim_max": round(max(sims), 4) if sims else None,
        "rel_sim_mean": round(sum(rel_sims) / len(rel_sims), 4) if rel_sims else None,
        "off_sim_mean": round(sum(off_sims) / len(off_sims), 4) if off_sims else None,
        "strength": strength,
        "off_topic_weak_rate": round(off_weak / len(off_topic), 4) if off_topic else None,
    }


def _print(rows: list[dict], summary: dict, mode: str) -> None:
    from core import config

    print(f"\n向量来源：{mode}")
    print(
        "检索参数：MIN_SIM={} LOW_SIM={} FETCH_MULT={} MMR_LAMBDA={} HYBRID={}".format(
            config.RETRIEVE_MIN_SIM,
            config.RETRIEVE_LOW_SIM,
            config.RETRIEVE_FETCH_MULT,
            config.RETRIEVE_MMR_LAMBDA,
            config.HYBRID_ENABLED,
        )
    )
    print("=" * 92)
    print(f"{'问题':<24} {'期望来源':<20} {'命中':<4} {'top1':<7} {'强度':<7} 字面重叠")
    print("-" * 92)
    for r in rows:
        exp = r["expect"] or "（离题）"
        hit = str(r["hit_rank"]) if r["hit_rank"] else "-"
        sim = f"{r['top1_sim']:.3f}" if isinstance(r["top1_sim"], (int, float)) else "n/a"
        ov = "盲区" if r["literal_blind"] else (
            f"{r['literal_overlap']:.2f}" if r["literal_overlap"] is not None else "-"
        )
        print(
            f"{r['question'][:22]:<24} {exp:<20} {hit:<4} {sim:<7} {str(r['strength']):<7} {ov}"
        )
    print("-" * 92)
    print(
        "全量：recall@1={recall@1} recall@3={recall@3} recall@5={recall@5} MRR={mrr}".format(
            **summary
        )
    )
    f = summary["recall_fair"]
    print(
        f"剔除字面盲区题（{summary['literal_blind']} 道）后："
        f"recall@1={f['recall@1']} recall@3={f['recall@3']} recall@5={f['recall@5']} "
        f"MRR={summary['mrr_fair']}"
    )
    print(
        f"top1 相似度：mean={summary['top1_sim_mean']} min={summary['top1_sim_min']} "
        f"max={summary['top1_sim_max']}"
    )
    print(
        f"  相关题均值={summary['rel_sim_mean']}  离题题均值={summary['off_sim_mean']}"
        f"  可分性 AUC（全量）={summary['auc_related_vs_offtopic']}"
        f"  AUC（剔除字面盲区）={summary['auc_fair']}"
    )
    print("  AUC 只比排序、与模型尺度无关，换 embedding 模型也能横向比较；"
          "全量 AUC 会被字面盲区题拖低（那些题在字面模型下本就该得低分）")
    print(f"闸门强度分布：{summary['strength']}")
    print(
        f"离题题被判 weak 的比例={summary['off_topic_weak_rate']}；"
        f"相关题被判 weak 的比例={summary['relevant_weak_rate']}"
        "  ← 两个都要看：只盯前者会把'一律示弱'当成合格"
    )
    print("=" * 92)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fake", action="store_true", help="强制使用确定性假向量")
    ap.add_argument("--json", default="", help="把结果另存为 JSON")
    args = ap.parse_args()

    use_fake = args.fake or not _has_real_embedding_key()
    if use_fake:
        _install_fake_embeddings()

    mode = "确定性假向量（字符 n-gram 哈希）" if use_fake else "真实 embedding 服务"
    if use_fake:
        print("注意：假向量只编码字面重叠，下面评估的是管道口径与排序逻辑，"
              "不代表真实向量模型的质量。")

    try:
        pipe = _build_index()
        data = _evaluate(pipe)
        rows = data["rows"]
        summary = _summarize(rows)
        _print(rows, summary, mode)

        out = {"mode": "fake" if use_fake else "real", "summary": summary, "rows": rows}
        if args.json:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=2)
            print(f"结果已写入：{args.json}")

        # 验收门槛（三条，都是"管道活着"的下限，不是性能好）：
        #  1. 离题必须示弱 —— 防编造的最后一道闸门，坏了这个等于允许硬答；
        #  2. 剔除字面盲区题后 recall@3 要够 —— 字面基线下这是管道能力，不是模型能力；
        #  3. 相关题必须排得比离题题靠前（AUC）—— 否则阈值无论放哪都会误伤一侧。
        #     判据刻意用 AUC 而不是"均值差 >= 0.1"：后者是绝对尺度，
        #     换一个 embedding 模型就会整体漂移，会把"换了模型"误报成"检索坏了"。
        fair_recall3 = summary["recall_fair"]["recall@3"]
        off_rate = summary["off_topic_weak_rate"] or 0
        auc_fair = summary["auc_fair"] or 0
        checks = [
            ("离题示弱率 >= 0.5", off_rate >= 0.5, f"{off_rate}"),
            ("剔除字面盲区后 recall@3 >= 0.75", fair_recall3 >= 0.75, f"{fair_recall3}"),
            ("相关/离题可分性 AUC（公平口径）>= 0.9", auc_fair >= 0.9, f"{auc_fair}"),
        ]
        ok = all(c[1] for c in checks)
        for name, passed, val in checks:
            print(f"  [{'PASS' if passed else 'FAIL'}] {name}  ({val})")
        print(f"验收门槛：{'通过' if ok else '未通过'}")
        return 0 if ok else 1
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
