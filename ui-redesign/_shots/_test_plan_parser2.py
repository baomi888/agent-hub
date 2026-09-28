# -*- coding: utf-8 -*-
"""parsePlan 修正版单测（同步 ChatArea.tsx）"""
import re, json

def clean_line(l):
    l = re.sub(r"^#{1,6}\s*", "", l)
    l = l.replace("**", "")
    l = re.sub(r"^[-*•·]\s*", "", l)
    l = re.sub(r"^>\s*", "", l)
    l = re.sub(r"^【[^】]*】\s*", "", l)
    return l.strip()

def parse_plan(content):
    raws, lines = [], []
    for raw in content.split("\n"):
        line = clean_line(raw)
        if line:
            raws.append(raw.strip())
            lines.append(line)
    if len(lines) < 2: return None
    title = lines[0][:60]
    timeline, phases, cur = [], [], None
    for i, line in enumerate(lines):
        raw = raws[i]
        if len(line) <= 30 and re.search(r"(第?[一二三四五六七八九十0-9]{1,3}周|W\d|[0-9]{1,2}月|[0-9]{1,2}日|[0-9]{4}[-年]|[0-9]{1,2}[-/~至—][0-9]{1,2}|筹备期|执行期|收尾|阶段)", line):
            timeline.append(line); continue
        is_phase = bool(re.match(r"^(0?[0-9]{1,2}[、.．:：]|[一二三四五六七八九十]{1,3}[、.．:：]|第[一二三四五六七八九十0-9]{1,3}[阶段部分]|步骤\s*[0-9一二三四五六七八九十]{1,3}|[-*•·]\s)", raw))
        if is_phase:
            if cur: phases.append(cur)
            t = re.sub(r"^0?[0-9]{1,2}[、.．:：]", "", line)
            t = re.sub(r"^[一二三四五六七八九十]{1,3}[、.．:：]", "", t)
            t = re.sub(r"^第[一二三四五六七八九十0-9]{1,3}[阶段部分]\s*", "", t)
            t = re.sub(r"^步骤\s*[0-9一二三四五六七八九十]{1,3}[:：]?\s*", "", t)
            t = re.sub(r"^[-*•·]\s*", "", t)
            cur = {"title": t.strip()}
        elif cur:
            cur["desc"] = cur.get("desc", "") + (" " if cur.get("desc") else "") + line
    if cur: phases.append(cur)
    if not phases and len(timeline) < 2: return None
    if not title: return None
    return {"title": title, "timeline": timeline, "phases": phases}

cases = [
    ("计划文本（编号+时间轴）", "新品发布会策划方案\n第1周 筹备期\n1. 组建团队\n确定核心成员分工\n2. 场地与预算\n确认场地档期\n第2周 制作期\n3. 物料制作\n主视觉与宣传物料\n第3周 执行期\n4. 现场执行\n发布会彩排与落地"),
    ("计划文本（时间轴+列表）", "暑期学习计划\n第一周：基础\n- 英语词汇\n- 数学复习\n第二周：进阶\n- 专项练习\n- 错题整理"),
    ("普通回复（无结构）", "好的，我明白了。这个问题需要结合你的具体情况来分析，建议你先梳理一下现有流程，再决定下一步怎么做。"),
    ("普通回复（有编号但非计划）", "回答你的问题：1. 是的 2. 不是 3. 看情况"),
    ("普通回复（单个时间词）", "好的，第一阶段先看看情况再说。"),
    ("短回复", "好的"),
    ("markdown 加粗标题", "**Q3 营销方案**\n第一周 预热期\n- 内容排期\n第二周 爆发期\n- 投放计划"),
]

for name, text in cases:
    r = parse_plan(text)
    print(f"=== {name} ===")
    print(json.dumps(r, ensure_ascii=False) if r else "-> None (回退普通渲染)")
