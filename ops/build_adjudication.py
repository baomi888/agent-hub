"""生成 X4 歧义词的**裁决表**（70 词 / 210 义项），供词表评审与澄清功能使用。

为什么不按「210 个义项逐个判换/不换」
------------------------------------
先说清楚一个容易走偏的地方：X4 的 210 个义项**不是 210 个待决事项**，
它们是 70 个词的**依据**。真正的决策单位是词，而且只有一个问题：

    **这个词能不能进「替换词表」当别名？**

因为「换/不换」这个二值对歧义词本身就没有意义——`栈` 换成什么？
数据结构栈、函数调用栈、还是进程栈区？**没有上下文就无解**。
所以裁决只有三种，且都是「词级」的：

    禁止入表            本身就是标准术语，替换只会把正确的说法改坏
    禁止入表 · 需澄清    含日常义，替换必错，但用户可能真要问那个日常义 → 该追问
    可入表 · 有条件      有压倒性的高频义项，且能用触发词圈定 → 有条件下替换

判定规则（可复核、可复现，不是拍脑袋）
------------------------------------
1. 该词的义项里只要出现**日常/生活类场景**，就归「需澄清」——
   理由：一句「这个概念怎么理解」里的「概念」可能就是日常义，
   机械替换成学科术语会直接把问题改掉。
2. 全部义项都是学科术语 → 「禁止入表」。
3. 少数词有压倒性高频义项（目前只有「综述」），进 `_OVERRIDES` 手工裁决并写明触发词。

⚠️ 关于触发词：**这一版只做到「学科标签」粒度，没有编关键词表。**
85 个不同的学科标签，硬编一份关键词字典等于闭门造车；正确做法是等 X2（真实问法）
到货后用真实语料反推——学生会自己写出「栈的入栈出栈」「这棵二叉树」这种上下文词。
现在编出来只会是好看但没用。

用法：
    python ops/build_adjudication.py
    python ops/build_adjudication.py --x4 docs/组员交付_2026-10-23/ambiguous_罗绍祺.xlsx
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 这些场景标签代表「日常/生活义」。命中说明该词会被当成大白话用，替换必错。
DAILY_TAGS = {"日常", "日常表达", "日常交流", "校园生活", "校园活动", "法律与生活", "生活"}

# 少数词有压倒性高频义项，人工裁决并给触发词。
# 加进来的每个词都必须写清楚「为什么」和「靠什么触发」，否则就是拍脑袋。
#
# ★ 触发词一律写**搭配短语**（含该词本身），不写学科关键词。
# 原因是踩过的：一开始想用「计算机/AI」这类学科标签当触发条件，
# 但 85 个学科标签里有一半是「编程」「科研写作」这种用户不会说出口的词，
# 编出来永远命中不了。而搭配是**用户会真的打出来的**：「跑模型」「训练模型」。
# 实测证据：金标第「跑模型要租显卡吗」要求换成「模型训练」，
# 若把「模型」无条件禁掉，这处就白丢——召回率从 99.0% 掉到 98.0%。
_OVERRIDES: dict[str, dict] = {
    "综述": {
        "decision": "可入表·有条件",
        "triggers": ["怎么写", "综述部分", "章节", "老师", "论文", "文献综述",
                     "答辩", "开题", "写综述"],
        "reason": "「综述」在科研/课程语境下压倒性指「文献综述」（一个章节），"
                  "X4 自己也把它标在「科研写作、课程作业」下；"
                  "但单说「综述一下」是动词义，不能换。故靠搭配短语圈定。",
        "needs_clarify": "否",
    },
    "模型": {
        "decision": "可入表·有条件",
        "triggers": ["跑模型", "训练模型", "模型训练", "模型微调", "微调模型",
                     "模型推理", "调模型", "模型调参", "部署模型", "模型压缩"],
        "reason": "X4 判它跨三个学科都是「模型」这个名词本身（机器学习/数学建模/商业），"
                  "替换无处可去；但「跑模型 / 训练模型」是学生真实会打的搭配，"
                  "金标第 5 条就要求换成「模型训练」。故只在这些搭配短语命中时放行。",
        "needs_clarify": "否",
    },
}

_HEADERS = [
    "序号", "词", "义项数", "含日常义", "歧义类型", "风险等级",
    "裁决", "需澄清", "触发词", "澄清问法", "依据", "备注",
]

_POINT_HEADERS = ["序号", "词", "义项序号", "义项", "学科标签",
                  "是否日常义", "该词裁决"]


def load_x4(path: str) -> list[dict]:
    """读 X4 初筛表。表头行靠「词」+「风险」两列定位，跳过标题块与统计块。

    注意：必须按**行号**切片，不能顺手 `min_row=2` 从头扫——
    表头上方有一行「词条数 70 / 歧义义项数 210 / …」的统计行，
    它的第 2 个格子是数字 70，会被当成一个叫「70」的词条混进来。
    """
    import openpyxl
    ws = openpyxl.load_workbook(path, data_only=True).active
    all_rows = [list(r) for r in ws.iter_rows(values_only=True)]

    header_idx = None
    header: list[str] = []
    for i, row in enumerate(all_rows):
        cells = ["" if c is None else str(c).strip() for c in row]
        if "词" in cells and any("风险" in c for c in cells):
            header_idx, header = i, cells
            break
    if header_idx is None:
        raise ValueError("找不到表头（要求同时有「词」和「风险」列）")

    def idx(name_part: str, default: int) -> int:
        for i, h in enumerate(header):
            if name_part in h:
                return i
        return default

    i_word = idx("词", 1)
    i_a = idx("含义A", 2)
    i_b = idx("含义B", 3)
    i_c = idx("含义C", 4)
    i_scene = idx("使用场景", 6)
    i_type = idx("歧义类型", 7)
    i_risk = idx("风险", 9)
    i_note = idx("说明", 10)

    out = []
    for row in all_rows[header_idx + 1:]:
        cells = ["" if c is None else str(c).strip() for c in row]
        if len(cells) <= i_word or not cells[i_word]:
            continue
        senses = [cells[i] for i in (i_a, i_b, i_c) if i < len(cells) and cells[i]]
        if not senses:
            continue
        scenes = [p.strip() for p in (cells[i_scene] if i_scene < len(cells) else "")
                  .replace("，", "、").split("、") if p.strip()]
        out.append({
            "word": cells[i_word],
            "senses": senses,
            "scenes": scenes,
            "type": cells[i_type] if i_type < len(cells) else "",
            "risk": cells[i_risk] if i_risk < len(cells) else "",
            "note": cells[i_note] if i_note < len(cells) else "",
        })
    return out


def adjudicate(word: dict) -> dict:
    """给一个词下裁决。返回裁决行 + 义项明细。"""
    senses = word["senses"]
    scenes = word["scenes"]
    aligned = len(scenes) == len(senses)
    sense_rows = []
    daily = []
    for i, s in enumerate(senses, start=1):
        tag = scenes[i - 1] if aligned else ""
        is_daily = bool(tag and tag in DAILY_TAGS) or ("日常" in word["type"])
        if tag and tag in DAILY_TAGS:
            daily.append(tag)
        sense_rows.append({
            "义项序号": i, "义项": s, "学科标签": tag,
            "是否日常义": (
                "是"
                if is_daily and tag
                else ("是（整词含日常义）" if ("日常" in word["type"] and not tag) else "否")
            ),
        })

    ov = _OVERRIDES.get(word["word"])
    if ov:
        decision, needs, triggers, reason = (
            ov["decision"], ov["needs_clarify"], "|".join(ov["triggers"]), ov["reason"])
    elif daily or "日常" in word["type"]:
        decision, needs, triggers = "禁止入表·需澄清", "是", ""
        reason = ("含日常/生活义" + (f"（{'、'.join(daily)}）" if daily else "")
                  + "：同一句里可能指的是大白话，替换成术语会改掉原意")
    else:
        decision, needs, triggers = "禁止入表", "否", ""
        reason = "全部义项均为学科术语，词本身已规范，替换只会把正确的说法改坏"

    clarify = ""
    if needs == "是":
        clarify = f"你说的「{word['word']}」是指：{' / '.join(senses)}？"

    remark = ""
    if not aligned:
        remark = f"场景标签 {len(scenes)} 个与义项 {len(senses)} 个对不上，标注不可靠"
    if ov:
        remark = (remark + "；" if remark else "") + "人工裁决（见脚本 _OVERRIDES）"

    row = {
        "词": word["word"], "义项数": len(senses),
        "含日常义": "是" if (daily or "日常" in word["type"]) else "否",
        "歧义类型": word["type"], "风险等级": word["risk"],
        "裁决": decision, "需澄清": needs, "触发词": triggers,
        "澄清问法": clarify, "依据": reason, "备注": remark,
    }
    return {"row": row, "points": sense_rows}


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 X4 歧义词裁决表")
    ap.add_argument("--x4", default=os.path.join(ROOT, "docs/组员交付_2026-10-23/ambiguous_罗绍祺.xlsx"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data/vocab/adjudication.csv"))
    ap.add_argument("--points", default=os.path.join(ROOT, "data/vocab/adjudication_points.csv"))
    args = ap.parse_args()

    words = load_x4(args.x4)
    rows, points = [], []
    for n, w in enumerate(words, start=1):
        res = adjudicate(w)
        res["row"]["序号"] = f"A{n:03d}"
        rows.append(res["row"])
        for p in res["points"]:
            points.append({"序号": f"A{n:03d}", "词": w["word"], **p,
                           "该词裁决": res["row"]["裁决"]})

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    for path, headers, data in ((args.out, _HEADERS, rows), (args.points, _POINT_HEADERS, points)):
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            wri = csv.DictWriter(f, fieldnames=headers)
            wri.writeheader()
            wri.writerows(data)
        print(f"已写出 {path}（{len(data)} 行）")

    from collections import Counter
    c = Counter(r["裁决"] for r in rows)
    print("\n裁决分布：")
    for k, v in c.most_common():
        print(f"  {k}：{v} 词")
    print(f"  合计：{len(rows)} 词 / {len(points)} 个义项")

    need = [r["词"] for r in rows if r["需澄清"] == "是"]
    print(f"\n需澄清（{len(need)} 词）：{'、'.join(need)}")
    cond = [r for r in rows if r["裁决"] == "可入表·有条件"]
    if cond:
        print(f"\n可入表·有条件（{len(cond)} 词）：")
        for r in cond:
            print(f"  {r['词']}：触发词 {r['触发词']}")
    bad = [r for r in rows if r["备注"] and "对不上" in r["备注"]]
    if bad:
        print(f"\n⚠️ {len(bad)} 词的场景标签与义项数不一致，标注需人工复核："
              f"{'、'.join(r['词'] for r in bad)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
