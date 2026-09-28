# -*- coding: utf-8 -*-
"""回归：标题含"阶段"不再误判为时间轴；编号阶段仍识别"""
import re

def clean_line(l):
    l = re.sub(r"^#{1,6}\s*", "", l); l = l.replace("**", "")
    l = re.sub(r"^[-*•·]\s*", "", l); l = re.sub(r"^>\s*", "", l)
    l = re.sub(r"^【[^】]*】\s*", "", l); return l.strip()

def parse_plan(content):
    raws, lines = [], []
    for raw in content.split("\n"):
        line = clean_line(raw)
        if line: raws.append(raw.strip()); lines.append(line)
    if len(lines) < 2: return None
    title = lines[0][:60]
    timeline, phases, cur = [], [], None
    for i, line in enumerate(lines):
        raw = raws[i]
        if len(line) <= 30 and re.search(r"(第?[一二三四五六七八九十0-9]{1,3}周|DAY\s*\d{1,2}|第[一二三四五六七八九十0-9]{1,3}天|第[一二三四五六七八九十0-9]{1,3}晚|[0-9]{1,2}月|[0-9]{1,2}日|第[一二三四五六七八九十0-9]{1,3}课时|[0-9]{4}[-年]|[0-9]{1,2}[-/~至—][0-9]{1,2}|筹备期|预热期|执行期|收尾|第[一二三四五六七八九十0-9]{1,3}阶段)", line, re.I):
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

# 真实后端返回的场景：标题含"阶段"
text = (
    "考研全年学习计划（基础—强化—冲刺三阶段）\n"
    "第1周 确定目标院校与专业，收集参考书目和历年真题\n"
    "第2周 制定每日作息表，英语单词启动，数学教材第一章\n"
    "1. 英语\n全程贯穿：单词每日不断，真题为核心\n"
    "2. 数学\n基础期吃透教材，强化期刷题\n"
)
r = parse_plan(text)
assert r, "should parse"
assert r["title"] == "考研全年学习计划（基础—强化—冲刺三阶段）", r["title"]
assert len(r["timeline"]) == 2, r["timeline"]
assert r["timeline"][0].startswith("第1周") and r["timeline"][1].startswith("第2周"), r["timeline"]
assert len(r["phases"]) == 2, r["phases"]
print("OK: 标题含阶段不再入时间轴, timeline:", r["timeline"])
print("    phases:", r["phases"])

# 编号阶段仍识别
text2 = "产品上线计划\n第1阶段 需求\n1. 需求收集\n访谈与问卷\n第2阶段 开发\n2. 功能开发\n前后端联调"
r2 = parse_plan(text2)
assert r2 and len(r2["timeline"]) == 2 and len(r2["phases"]) == 2, r2
print("OK: 第X阶段仍识别 timeline:", r2["timeline"])
