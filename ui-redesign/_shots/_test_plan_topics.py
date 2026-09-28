# -*- coding: utf-8 -*-
"""题材模板识别 · 单测（镜像 ChatArea.tsx：detectPlanTopic / buildPlanPrompt / 扩展 parsePlan）"""
import re, json

PLAN_KEYWORDS = ["计划", "策划", "方案", "安排", "规划", "路线图", "日程", "排期", "攻略", "行程", "roadmap", "plan"]

PLAN_TOPICS = [
    (["营销", "发布会", "推广", "上市", "促销", "品牌", "市场", "宣发", "campaign", "活动策划"], ("营销活动", "时间轴建议按“第1周 筹备期 / 第2周 预热期 / 第3周 爆发期”划分。")),
    (["学习", "备考", "复习", "考研", "考公", "雅思", "托福", "读书", "课程", "培训", "考试", "study"], ("学习计划", "时间轴建议按“第1周 ~ 第N周”划分，阶段按科目或模块编号。")),
    (["旅行", "旅游", "出游", "自驾", "攻略", "度假", "出差", "trip", "travel"], ("旅行行程", "时间轴建议按“DAY 1 / DAY 2”或“第1天 / 第2天”划分，每阶段写当天的行程安排。")),
    (["健身", "训练", "减脂", "增肌", "跑步", "马拉松", "瑜伽", "运动", "fitness"], ("健身训练", "时间轴建议按“第1周 ~ 第N周”划分，阶段按训练部位或阶段编号。")),
    (["婚礼", "订婚", "求婚", "派对", "聚会", "生日", "wedding"], ("婚礼派对", "时间轴建议按“筹备期 / 当天 / 收尾”或“第N个月”划分。")),
    (["搬家", "装修", "改造", "布置", "收纳", "move"], ("搬家装修", "时间轴建议按“第N周”或“打包 / 运输 / 布置”阶段划分。")),
    (["内容", "写作", "文案", "脚本", "公众号", "小红书", "直播", "自媒体", "专栏", "书籍", "论文", "content", "video"], ("内容创作", "时间轴建议按“第N周”或“选题 / 撰写 / 发布 / 复盘”阶段划分。")),
    (["产品", "开发", "研发", "上线", "迭代", "立项", "需求", "版本", "发布"], ("产品研发", "时间轴建议按“第N周”或“需求 / 设计 / 开发 / 测试 / 上线”阶段划分。")),
    (["创业", "开店", "经营", "融资", "商业", "公司", "团队", "startup"], ("创业经营", "时间轴建议按“第N月”或“筹备 / 启动 / 运营”阶段划分。")),
    (["预算", "财务", "理财", "省钱", "采购", "成本", "资金", "budget"], ("财务预算", "时间轴建议按“第N周”或“盘点 / 规划 / 执行 / 复盘”阶段划分。")),
]

def detect_topic(q):
    if not q: return None
    lq = q.lower()
    for keys, topic in PLAN_TOPICS:
        if any(k in lq for k in keys):
            return topic
    return None

def build_prompt(raw, topic):
    hint = (topic[1] if topic else "时间轴建议按“第1周 / 第N天 / DAY N”划分，阶段用编号列出。")
    return raw + "\n\n【格式要求】请直接输出一份结构化计划，不要解释。格式如下：\n" \
        "第一行：计划标题\n时间轴：每行以“第1周 xxx”“第N天 xxx”或“DAY 1 xxx”开头，简短\n" \
        "阶段清单：每行用“1. xxx”编号，标题一行、说明一行\n" + hint

def strip_hint(text):
    i = text.find("\n\n【格式要求】")
    return text[:i] if i >= 0 else text

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
            raws.append(raw.strip()); lines.append(line)
    if len(lines) < 2: return None
    title = lines[0][:60]
    timeline, phases, cur = [], [], None
    for i, line in enumerate(lines):
        raw = raws[i]
        if len(line) <= 30 and re.search(r"(第?[一二三四五六七八九十0-9]{1,3}周|DAY\s*\d{1,2}|第[一二三四五六七八九十0-9]{1,3}天|第[一二三四五六七八九十0-9]{1,3}晚|[0-9]{1,2}月|[0-9]{1,2}日|第[一二三四五六七八九十0-9]{1,3}课时|[0-9]{4}[-年]|[0-9]{1,2}[-/~至—][0-9]{1,2}|筹备期|预热期|执行期|收尾|阶段)", line, re.I):
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

print("== 题材检测 ==")
for q in ["帮我做一份新品发布会策划方案", "给我个考研学习计划", "做一份云南7天旅行攻略",
          "帮我做个减脂训练计划", "安排一下搬家流程", "写个公众号内容计划",
          "帮我做一份产品上线计划", "做个月度财务预算方案", "今天天气怎么样"]:
    t = detect_topic(q)
    print(f"  {q!r} -> {t[0] if t else None}")

print()
print("== 提示注入（发送给 AI 的文本，气泡不显示）==")
raw = "给我个考研学习计划"
prompt = build_prompt(raw, detect_topic(raw))
print("  " + prompt.replace("\n", " ⏎ "))
print("  strip 后:", repr(strip_hint(prompt)))
print("  strip 普通消息:", repr(strip_hint("普通消息")))

print()
print("== 扩展解析：旅行 DAY 格式 ==")
travel = "云南7天旅行行程\nDAY 1 抵达丽江\n1. 入住古城客栈\n适应海拔，逛逛古城\nDAY 2 玉龙雪山\n2. 雪山一日游\n蓝月谷与冰川公园\nDAY 3 大理洱海\n3. 环洱海骑行\n双廊与喜洲古镇"
r = parse_plan(travel)
print("  ", json.dumps(r, ensure_ascii=False))

print()
print("== 扩展解析：学习 第N天 格式 ==")
study = "考研冲刺计划\n第1天 摸底\n1. 摸底测试\n做一套真题\n第2天 规划\n2. 制定计划\n分配各科时间\n第3天 启动\n3. 开始复习\n英语单词与数学基础"
r = parse_plan(study)
print("  ", json.dumps(r, ensure_ascii=False))
