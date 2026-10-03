# -*- coding: utf-8 -*-
"""检索结果挑选逻辑自测（纯函数，不需要网络和知识库）。

运行：python tests/test_retrieve_select.py
调 RETRIEVE_MIN_SIM / RETRIEVE_MMR_LAMBDA 之后跑一遍，确认阈值没有误杀或漏筛。
"""
import os
import sys

# 从项目根导入（直接跑 tests/ 下的脚本时 sys.path 里没有根）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.documents import Document

from kb.rag_core import select_diverse, _to_similarity

BASE = "苞米是一种在东北广泛种植的作物，秋天收割后可以直接煮着吃，也可以磨成玉米面。"


def d(text, score):
    return (Document(page_content=text), score)


def show(tag, picked):
    print(f"--- {tag} -> {len(picked)} 条")
    for doc, s in picked:
        print(f"    score={s:.3f} sim={_to_similarity(s)} | {doc.page_content[:34]}")


# 1) 重复去重：候选里 3 条几乎同一段（模拟相邻切片重叠），外加 2 条不同内容
cands = [
    d(BASE, 0.20),
    d(BASE + "农家一般在霜降前后完成收割。", 0.24),          # 与上一条高度重合
    d(BASE + "收割后要晾晒三天再入仓储存。", 0.27),          # 还是同一段
    d("高德的天气接口需要申请 Web 服务 Key 才能调用。", 0.45),
    d("知识库的切片参数包括分段长度与重叠字数两项。", 0.50),
]
picked = select_diverse(cands, 3)
show("重复去重 top3", picked)
assert len(picked) == 3
assert sum(1 for doc, _ in picked if "苞米" in doc.page_content) == 1, "同一段应只留一条"

# 2) 离群过滤：距离 1.9（余弦 0.05）属于明显不相关，应被丢掉
cands2 = [
    d("相关内容 A：苞米的种植密度与产量关系密切。", 0.30),
    d("相关内容 B：玉米螟是苞米的主要虫害之一。", 0.36),
    d("完全不相关的一段话，讲的是别的主题。", 1.90),
]
picked2 = select_diverse(cands2, 3)
show("离群过滤", picked2)
assert all("不相关" not in doc.page_content for doc, _ in picked2), "离群片段应被过滤"

# 3) 距离 > 2（向量未归一化，余弦换算不成立）→ 跳过过滤，只做去重
cands3 = [
    d("片段一：关于苞米的储存方式。", 3.1),
    d("片段二：关于苞米的储存方式补充。", 3.3),
    d("片段三：讲天气接口怎么申请。", 5.0),
]
picked3 = select_diverse(cands3, 2)
show("不可换算时跳过过滤", picked3)
assert len(picked3) == 2, "换不了余弦就不该乱砍"

# 4) 候选不足 → 原样返回，不多不少
short = [d("只有一条", 0.1), d("只有两条", 0.2)]
assert len(select_diverse(short, 5)) == 2
assert select_diverse([], 3) == []

# 5) lambda=1 时退化为纯相关度排序（挑出来的应当是距离最小的三条）
cands5 = [d(f"第{i}条内容，彼此都不相同，主题也各异。", 0.1 * (i + 1)) for i in range(6)]
picked5 = select_diverse(cands5, 3, lambda_mult=1.0)
show("lambda=1 纯相关度", picked5)
assert [round(s, 2) for _, s in picked5] == [0.1, 0.2, 0.3]

print("\nALL PASS")
