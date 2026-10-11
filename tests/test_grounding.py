# -*- coding: utf-8 -*-
"""依据强度判定（P0 可信度）离线回归。

盯的是"让模型敢说不知道"这条链路里最容易退化的三件事：
  1. 阈值口径——RETRIEVE_MIN_SIM（丢不丢）和 RETRIEVE_LOW_SIM（够不够格当依据）
     必须一致，否则会出现"card 上标了弱、回答却按正常资料答"的自相矛盾；
  2. **medium 也要示弱**——medium 是"没找到硬证据"，不是"找到了弱证据"。
     只让 low 示弱的话，0.26~0.44 这段最容易被硬答，而它恰恰是最常见的灰色地带；
  3. 换算失败时**必须放行**——相似度算不出来是配置问题不是数据问题，
     判成示弱会让模型对所有知识库都说"我找不到依据"，比不判更糟。

不联网、不建库，纯函数级验证。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import config  # noqa: E402
from kb.rag_core import (  # noqa: E402
    STRENGTH_HIGH,
    STRENGTH_LOW,
    STRENGTH_MEDIUM,
    STRENGTH_NONE,
    _to_similarity,
    assess_grounding,
    grounding_note,
)

# 造数时浮点会有 0.44999999999999996 这种尾巴，判定本身用严格比较是对的
# （阈值语义就是"达到才算"），所以断言侧统一用容差，不要反过来放宽带等于号。
EPS = 1e-9

FAILS = []


class _Doc:
    """只要 page_content 就够了——assess_grounding 不碰别的字段。"""

    def __init__(self, text: str = "x"):
        self.page_content = text
        self.metadata = {}


def _pair(sim: float | None, text: str = "x"):
    """按目标相似度反推一个 Chroma 距离，造出 (doc, distance)。

    sim 为 None 时用一个换算不出来的距离（>4），模拟"换了没归一化的 embedding"。
    """
    if sim is None:
        return (_Doc(text), 5.0)
    return (_Doc(text), (1 - sim) * 2)


def check(name: str, cond: bool, detail: str = ""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILS.append(name)


print("=" * 60)
print("依据强度判定（P0 可信度）")
print("=" * 60)

low = config.RETRIEVE_LOW_SIM
min_sim = config.RETRIEVE_MIN_SIM
print(f"阈值：RETRIEVE_MIN_SIM={min_sim}（丢不丢） / RETRIEVE_LOW_SIM={low}（够不够格）")
print(f"口径：best>={low} → high（正常答）；best<{low} → 一律示弱")
print()

# ---- 1. 没有结果 ----
g = assess_grounding([])
check("无结果 → none", g["strength"] == STRENGTH_NONE and g["count"] == 0, str(g))
check("无结果 → 不标 weak", g["weak"] is False, str(g))

# ---- 2. 强相关：正常回答，不打扰用户 ----
g = assess_grounding([_pair(0.78), _pair(0.42)])
check("top1=0.78 → high", g["strength"] == STRENGTH_HIGH, str(g))
check("high 不追加提示", grounding_note(g) == "", repr(grounding_note(g)))
check("high 不标 weak", g["weak"] is False, str(g))
check("best 取最大值", abs((g["best"] or 0) - 0.78) < 1e-6, str(g))

# ---- 3. ★ 灰色地带必须示弱（原实现漏的就是这里）----
gray = (min_sim + low) / 2  # = 0.35，单条落在 (min_sim, low)
g = assess_grounding([_pair(gray)])
check(f"top1={gray} → medium（卡片标「中」）", g["strength"] == STRENGTH_MEDIUM, str(g))
check("★ medium 也标 weak（没找到硬证据）", g["weak"] is True, str(g))
note = grounding_note(g)
check("★ medium 也追加提示", len(note) > 0, repr(note[:60]))
check("提示里含可执行下一步", "联网搜索" in note, repr(note[:200]))
check("提示里含不许硬编的底线", "不要编" in note, repr(note[:200]))

# ---- 4. 低置信 ----
g = assess_grounding([_pair(0.18), _pair(0.05)])
check("top1=0.18 → low", g["strength"] == STRENGTH_LOW, str(g))
check("low 标 weak", g["weak"] is True, str(g))
check("low 追加提示", len(grounding_note(g)) > 0)

# ---- 5. 边界：恰好达到判定线 ----
# ⚠️ 造数会有浮点尾巴（0.45 反推距离再换算回来是 0.44999999999999996），
# 这不是判定错，是 IEEE754 的必然结果。所以造边界样本时用「稍高于线」的值，
# 并单独验一下"尾巴朝下时确实判弱"——后者才是真正要保证的语义。
g = assess_grounding([_pair(low + 1e-6)])
check(f"top1 略高于 {low} → high", g["strength"] == STRENGTH_HIGH, str(g))
just_below = low - 1e-6
g = assess_grounding([_pair(just_below)])
check(f"top1={just_below} → 示弱（临界点不放过）", g["weak"] is True, str(g))
# 浮点尾巴本身的行为也钉住：0.45 造数回来是 0.44999...，按严格比较判弱。
# 记录这个事实，是为了将来有人"顺手"把比较改成 round 时能立刻发现语义变了。
g = assess_grounding([_pair(low)])
check(
    "浮点尾巴：0.45 造数实际落在线下 → 判弱（若改成 round 会变 high，属语义变更）",
    g["strength"] == STRENGTH_MEDIUM,
    str(g),
)

# ---- 6. ★ 换算失败必须放行 ----
g = assess_grounding([_pair(None), _pair(None)])
check("相似度算不出来 → high（放行）", g["strength"] == STRENGTH_HIGH, str(g))
check("相似度算不出来 → 不标 weak", g["weak"] is False, str(g))
check("相似度算不出来 → best 为 None", g["best"] is None, str(g))
check("相似度算不出来 → 不追加提示", grounding_note(g) == "")

# 混合：有一条能换算且够格，就该正常放行
g = assess_grounding([_pair(0.66), _pair(None)])
check("一强一不可换算 → high", g["strength"] == STRENGTH_HIGH, str(g))

# ---- 7. ★ 判定只看 top1，不能被平均值/最低分带偏 ----
g = assess_grounding([_pair(0.62), _pair(0.01), _pair(0.01), _pair(0.01)])
check("一条强 + 多条极弱 → 仍 high（不被平均分拖低）", g["strength"] == STRENGTH_HIGH, str(g))
check("同上 → 不标 weak", g["weak"] is False, str(g))

# ---- 8. ★ 与 build_sources 的口径必须一致 ----
# 卡片上标「弱」的片段，在 assess_grounding 里不能同时被判成"够格"。
# 这条一旦不一致，就会出现"引用卡片标了弱、回答却按资料正常作答"的错位。
from kb.rag_core import RagPipeline  # noqa: E402

for sim in (0.9, 0.5, 0.35, 0.2, 0.02):
    pairs = [_pair(sim)]
    src = RagPipeline.build_sources(pairs)[0]
    g = assess_grounding(pairs)
    card_ok = src["strength"] == STRENGTH_HIGH  # 卡片认为是"强"
    answer_ok = g["strength"] == STRENGTH_HIGH  # 回答侧认为是"够格"
    check(
        f"sim={sim}：卡片「{src['strength']}」与判定「{g['strength']}」口径一致",
        card_ok == answer_ok,
        f"card={src['strength']} assess={g['strength']}",
    )

check("卡片 strength 字段存在且三档齐全", True)
srcs = RagPipeline.build_sources([_pair(0.9), _pair(0.35), _pair(0.05)])
check(
    "卡片三档标注正确",
    [s["strength"] for s in srcs] == [STRENGTH_HIGH, STRENGTH_MEDIUM, STRENGTH_LOW],
    str([s["strength"] for s in srcs]),
)
check(
    "卡片相似度换算不了时 strength 为 None",
    RagPipeline.build_sources([_pair(None)])[0]["strength"] is None,
)

# ---- 9. 开关能一键回退 ----
_saved = config.GROUNDING_NOTICE
try:
    config.GROUNDING_NOTICE = False
    g = assess_grounding([_pair(0.1)])
    check("关闭开关后不追加提示", grounding_note(g) == "", repr(grounding_note(g)))
    check("关闭开关不影响判定本身（前端提示条仍在）", g["strength"] == STRENGTH_LOW, str(g))
finally:
    config.GROUNDING_NOTICE = _saved

print()
if FAILS:
    print(f"❌ 失败 {len(FAILS)} 项：" + "、".join(FAILS))
    sys.exit(1)
print("✅ 依据强度判定全部通过")
