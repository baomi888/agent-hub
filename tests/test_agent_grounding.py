"""离线自测：Agent 路径 kb_search 在检索偏弱时复用 RAG 路径的 grounding_note。

验证 agent/tools.py::kb_search 与 kb/rag_core.py 共用同一套防编造注释
（单来源、措辞一致、受 GROUNDING_NOTICE 开关统一控制）：

  · 检索偏弱（low / medium）时，kb_search 返回的串**前面**带上
    grounding_note 产出的【…必须遵守】约束；
  · 强检索（high）时不吐任何 constraint，只回片段；
  · GROUNDING_NOTICE 关掉后，即便偏弱也不吐提示；
  · 返回的开头严格等于 grounding_note(assessment) —— 证明它调的是共享函数，
    而不是旧版自己硬编码的那段「⚠️ 检索到的片段相似度全部偏低…」（回归护栏）。

不联网、不碰真实数据：用桩 pipe 替换 pipelines.get_or_create，
直接构造 (Document, distance) 命中，距离按 _to_similarity(d)=1-d/2 反推，
确保稳定落在 low / medium / high 三档。

距离 → 相似度 → 档位（阈值 RETRIEVE_LOW_SIM=0.45 / RETRIEVE_MIN_SIM=0.25）：
  d=0.0  → sim 1.00 → high
  d=1.3  → sim 0.35 → medium（沾边）
  d=2.0  → sim 0.00 → low（不够格）
"""

import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="agent_grounding_")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import core.config  # noqa: E402

core.config.PROJECT_ROOT = os.path.join(_TMP, "proj")
core.config.PERSIST_DIR = os.path.join(_TMP, "proj", "chroma_db")
core.config.DATA_DIR = os.path.join(_TMP, "proj", "data")
core.config.DOWNLOAD_DIR = os.path.join(_TMP, "proj", "downloads")
os.makedirs(os.path.join(core.config.PROJECT_ROOT, "data"), exist_ok=True)

from langchain_core.documents import Document  # noqa: E402

from agent.tools import kb_search  # noqa: E402
from kb import pipelines  # noqa: E402
from kb.rag_core import assess_grounding, grounding_note  # noqa: E402


def _doc(text: str = "苞米是玉米的俗称，原产于美洲") -> Document:
    return Document(page_content=text, metadata={"source": "test.txt", "page": 0})


class _StubPipe:
    """桩：peek 非空，retrieve_with_score 直接回构造好的 (doc, distance)。"""

    def __init__(self, docs_scores):
        self._docs_scores = docs_scores

    def peek(self):
        return len(self._docs_scores)

    def retrieve_with_score(self, query, top_k, owner):
        return self._docs_scores


def _install_stub(docs_scores):
    saved = pipelines.get_or_create
    pipelines.get_or_create = lambda owner, kb_id: _StubPipe(docs_scores)
    return saved


def _restore(saved):
    pipelines.get_or_create = saved


def cfg(owner: str = "u_gnd", kb_id: str = "kb_gnd"):
    return {"configurable": {"thread_id": "s", "owner_id": owner, "kb_id": kb_id}}


PASS, FAIL = [], []


def check(name: str, cond: bool, extra: str = ""):
    (PASS if cond else FAIL).append(name)
    print(
        ("  PASS  " if cond else "  FAIL  ")
        + name
        + (("  <- " + extra) if extra and not cond else "")
    )


# ---- 三档命中：距离按 _to_similarity(d)=1-d/2 反推，落在 low/medium/high ----
DS_LOW = 2.0  # sim 0.00 -> low
DS_MED = 1.3  # sim 0.35 -> medium
DS_HIGH = 0.0  # sim 1.00 -> high

LOW = [(_doc(), DS_LOW)]
MED = [(_doc(), DS_MED)]
HIGH = [(_doc(), DS_HIGH)]


print("\n[A] 弱检索（low）复用了 RAG 路径的 grounding_note")
saved = _install_stub(LOW)
try:
    r = kb_search.invoke({"query": "量子纠缠是什么"}, config=cfg())
    exp = grounding_note(assess_grounding(LOW))
    check("low 档 grounding_note 非空", exp != "", repr(exp))
    check("kb_search 开头 == grounding_note（单来源）", r.startswith(exp), r[:60])
    check("返回含【…必须遵守】约束", "必须遵守" in r, r[:60])
    check("已移除旧版硬编码提示（回归护栏）", "检索到的片段相似度全部偏低" not in r, "旧文案仍在")
finally:
    _restore(saved)

print("\n[B] 灰色地带（medium）同样吐约束，但用沾边话术")
saved = _install_stub(MED)
try:
    r = kb_search.invoke({"query": "玉米什么时候播种"}, config=cfg())
    exp = grounding_note(assess_grounding(MED))
    check("medium 档 grounding_note 非空", exp != "", repr(exp))
    check("kb_search 开头 == grounding_note（单来源）", r.startswith(exp), r[:60])
    check("medium 用『沾边内容』话术", "沾边" in r, r[:60])
finally:
    _restore(saved)

print("\n[C] 强检索（high）不吐任何约束，只回片段")
saved = _install_stub(HIGH)
try:
    r = kb_search.invoke({"query": "苞米是什么"}, config=cfg())
    check("high 档 grounding_note 为空", grounding_note(assess_grounding(HIGH)) == "", "")
    check("强检索不吐【必须遵守】", "必须遵守" not in r, r[:60])
    check("强检索直接返回片段（[1] 角标开头）", r.lstrip().startswith("**[1]**"), r[:60])
finally:
    _restore(saved)

print("\n[D] GROUNDING_NOTICE 关闭后，偏弱也不吐约束")
saved_notice = core.config.GROUNDING_NOTICE
core.config.GROUNDING_NOTICE = False
saved = _install_stub(LOW)
try:
    r = kb_search.invoke({"query": "量子纠缠是什么"}, config=cfg())
    check("关开关后 low 档不吐【必须遵守】", "必须遵守" not in r, r[:60])
    check("关开关后 low 档直接返回片段", r.lstrip().startswith("**[1]**"), r[:60])
finally:
    _restore(saved)
    core.config.GROUNDING_NOTICE = saved_notice

# ---------------------------------------------------------------- 汇总
print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    for f in FAIL:
        print("  FAILED:", f)
    sys.exit(1)
print("全部通过")
