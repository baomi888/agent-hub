# -*- coding: utf-8 -*-
"""P0 端到端离线自检：假 embedding + 本地 txt，验证整条链路真的会"示弱"。

前面 test_grounding.py 只测了判定函数本身。这个脚本往下再走一层：
真建一个 Chroma 库、真检索一次、真过一遍 build_sources / RAG 提示词组装，
确认"低相似度 → 提示条 + 给模型的约束"这条路是通的，而不是判定对了、
但拼 prompt 的地方把结论丢了。

不联网：embedding 换成 DeterministicFakeEmbedding，LLM 不调用（只看 prompt）。
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import config  # noqa: E402

# 必须在 import rag_core 之前换掉 embedding / 改掉持久化目录
_TMP = tempfile.mkdtemp(prefix="baomi_p0_")
config.PERSIST_DIR = os.path.join(_TMP, "chroma")

from langchain_core.embeddings import DeterministicFakeEmbedding  # noqa: E402

import kb.rag_core as rag  # noqa: E402

rag.get_embeddings = lambda: DeterministicFakeEmbedding(size=64)

from kb.rag_core import (  # noqa: E402
    RagPipeline,
    assess_grounding,
    grounding_note,
    select_diverse,
)

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILS.append(name)


# 库里只放"苞米种植"的资料，然后问"怎么申请高德地图 Key"——
# 业务上这就是"知识库里没有"，应当被判定为要示弱。
DOC = os.path.join(_TMP, "苞米种植.txt")
with open(DOC, "w", encoding="utf-8") as f:
    f.write(
        "苞米是一种在东北广泛种植的作物。\n"
        "苞米的种植密度与产量关系密切，一般每亩 3500 到 4000 株。\n"
        "秋天收割后可以直接煮着吃，也可以磨成玉米面。\n"
        "玉米螟是苞米的主要虫害之一，需要及时防治。\n"
    )

print("=" * 60)
print("P0 端到端离线自检（假 embedding + 本地库）")
print("=" * 60)

pipe = RagPipeline("kb_p0_selftest")
n = pipe.build_index(200, 30, [DOC], source_names=["苞米种植.txt"])
check("建库成功", n > 0, f"chunks={n}")

# ---- 1. 问一个库里有的问题 ----
docs_scores = pipe.retrieve_with_score("苞米的种植密度是多少", 3)
g = assess_grounding(docs_scores)
print(f"\n[命中] 检索 {len(docs_scores)} 条，top1={g['best']}，判定={g['strength']}")
check("库内问题能检索到片段", len(docs_scores) > 0)

srcs = pipe.build_sources(docs_scores)
check("build_sources 每条都带 strength 字段", all("strength" in s for s in srcs), str(srcs[:1]))
check("每条 strength 取值合法", all(s["strength"] in ("high", "medium", "low", None) for s in srcs))

# ---- 2. ★ 强制低相似度场景：直接喂远离的向量，确认提示条会亮 ----
# 用假 embedding 很难稳定造出"低分"，所以这里直接构造距离，走真实判定 + 组装
class _D:
    def __init__(self, t):
        self.page_content = t
        self.metadata = {"source": "苞米种植.txt"}


far = [(_D("苞米种植密度 3500 株"), 3.4)]  # d=3.4 → cos=1-1.7=-0.7，明显不相关
g_far = assess_grounding(far)
print(f"\n[不相关] top1={g_far['best']:.3f}，判定={g_far['strength']}，weak={g_far['weak']}")
check("★ 明显不相关 → 判定为 weak", g_far["weak"] is True, str(g_far))
check("★ weak 时会产生给模型的约束文本", len(grounding_note(g_far)) > 0)
check(
    "★ 约束文本明说了不许硬答",
    "不要编" in grounding_note(g_far),
    grounding_note(g_far)[:80],
)

# ---- 3. 灰色地带同样要亮 ----
gray = [(_D("沾边内容"), (1 - 0.35) * 2)]  # cos≈0.35，落在 (min_sim, low)
g_gray = assess_grounding(gray)
note_gray = grounding_note(g_gray)
print(f"\n[沾边] top1={g_gray['best']:.3f}，判定={g_gray['strength']}，weak={g_gray['weak']}")
check("★ 灰色地带也 weak", g_gray["weak"] is True, str(g_gray))
check("★ 灰色地带的约束与 low 用词不同", note_gray != grounding_note(g_far))
check("灰色地带提示提到「沾边」", "沾边" in note_gray, note_gray[:60])

# ---- 4. 真正走一遍 build_sources 的卡片强度 ----
cards = pipe.build_sources(far + gray + [(_D("强相关片段"), (1 - 0.8) * 2)])
print("\n[卡片] " + " | ".join(f"{c['score']}→{c['strength']}" for c in cards))
check("卡片强弱标注与判定一致", cards[0]["strength"] == "low" and cards[-1]["strength"] == "high", str(cards))

# ---- 5. 开关回退：关掉后不该再往 prompt 里塞约束 ----
_saved = config.GROUNDING_NOTICE
try:
    config.GROUNDING_NOTICE = False
    check("★ 关掉开关后不再追加约束（可一键回退）", grounding_note(g_far) == "")
    check("关掉开关不影响判定结果本身", assess_grounding(far)["weak"] is True)
finally:
    config.GROUNDING_NOTICE = _saved

print()
try:
    shutil.rmtree(_TMP, ignore_errors=True)
except Exception:
    pass

if FAILS:
    print(f"❌ 失败 {len(FAILS)} 项：" + "、".join(FAILS))
    sys.exit(1)
print("✅ P0 端到端离线自检全部通过")
