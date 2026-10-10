# -*- coding: utf-8 -*-
"""面向大学生的智能体场景模板目录（模板广场数据源）。

设计原则（与产品定位一致）：
  1. 「教搭建而非代做」——每个模板都强调"答案要你自己来"，呼应政策对
     AI 学伴"拒绝代做、重在培养能力"的要求，也是和普通通用 Agent 平台的差异点。
  2. 每个模板自带一个建议知识库 + 一个开箱即用的问题，让用户 30 秒内看到价值。
  3. 数据纯静态、零外部依赖，方便后续接数据库或后台编辑。

字段说明：
  id               模板唯一 slug
  name             展示名
  category         分类（前端按分类着色）
  icon             卡片图标（emoji，避免引入图片资源）
  tagline          一句话卖点
  description      2-3 句场景描述
  tags             标签（卡片上以 chip 展示）
  suggested_kb_name 应用模板时自动创建的知识库名（可为空）
  session_title    应用后会话的标题
  starter_prompt   应用后预填的首个问题（引导用户立刻上手）
  recommended_mode 推荐聊天模式：rag / agent / auto
  philosophy       一句"理念"提示，强化"辅助而非替代"
"""

from __future__ import annotations

_TEMPLATE_LIST: list[dict] = [
    {
        "id": "kaoyan",
        "name": "考研复习助手",
        "category": "升学规划",
        "icon": "📚",
        "tagline": "把复习资料变成专属题库与时间表",
        "description": "上传你的专业课笔记、真题与大纲，它会帮你拆解考点、规划每月节奏、生成自测题。",
        "tags": ["时间规划", "真题解析", "考点梳理"],
        "suggested_kb_name": "我的考研资料库",
        "session_title": "📚 考研复习助手",
        "starter_prompt": (
            "我准备考【专业名】，现在是【X 月】。请先帮我制定一份从现在到考前的月度复习计划，"
            "并标注每个阶段该往「我的考研资料库」里补充哪类资料。"
        ),
        "recommended_mode": "rag",
        "philosophy": "它帮你拆解考点、规划节奏、出模拟题，但答案要你自己写、自己背——这是你的考试。",
    },
    {
        "id": "thesis",
        "name": "论文写作助手",
        "category": "学术科研",
        "icon": "📝",
        "tagline": "从选题到润色，理清你的论证链",
        "description": "把参考文献、导师意见、自己的草稿放进知识库，它帮你梳理论文结构、找逻辑漏洞、润色表达。",
        "tags": ["结构梳理", "文献综述", "学术润色"],
        "suggested_kb_name": "我的论文参考文献库",
        "session_title": "📝 论文写作助手",
        "starter_prompt": (
            "我想写一篇关于【主题】的论文。请先帮我把论文的整体结构（摘要/引言/方法/结论）梳理清楚，"
            "并指出每个部分我应该往「我的论文参考文献库」里放什么材料。"
        ),
        "recommended_mode": "rag",
        "philosophy": "它帮你把论证讲清楚，但观点和研究是你自己的——学术诚信比一篇润色稿重要。",
    },
    {
        "id": "lecture-notes",
        "name": "课程笔记整理",
        "category": "学习备考",
        "icon": "🗒️",
        "tagline": "散乱笔记一键变结构化大纲",
        "description": "上传课堂录音转写、截图或零散笔记，它帮你提炼要点、生成思维导图式大纲与复习卡片。",
        "tags": ["要点提炼", "大纲生成", "复习卡片"],
        "suggested_kb_name": "我的课程资料库",
        "session_title": "🗒️ 课程笔记整理",
        "starter_prompt": (
            "我有一份【课程名】的课堂笔记（稍后上传）。请先告诉我：上传后你希望我按什么格式整理，"
            "以及怎样的知识点最适合做成复习卡片？"
        ),
        "recommended_mode": "rag",
        "philosophy": "它帮你把知识理顺，但消化和理解还得靠你自己做题、自己讲一遍。",
    },
    {
        "id": "final-sprint",
        "name": "期末冲刺教练",
        "category": "学习备考",
        "icon": "⚡",
        "tagline": "按你上传的重点出模拟自测",
        "description": "把老师的划重点、往年卷、课件放进去，它按章节出选择/简答/论述题，并给出评分要点。",
        "tags": ["自测题", "查漏补缺", "评分要点"],
        "suggested_kb_name": "我的期末复习库",
        "session_title": "⚡ 期末冲刺教练",
        "starter_prompt": (
            "我要复习【课程名】。请先根据我上传的重点，出 5 道涵盖核心章节的自测题（含答案要点），"
            "帮我定位最薄弱的部分。"
        ),
        "recommended_mode": "rag",
        "philosophy": "它出题、你答题、它判分——真正记住知识的，是动笔的那个人。",
    },
    {
        "id": "resume",
        "name": "求职简历与面试",
        "category": "求职发展",
        "icon": "💼",
        "tagline": "把经历讲成 HR 想听的故事",
        "description": "上传你的项目经历与 JD，它帮你 STAR 化改写简历、模拟面试提问并给出应答思路。",
        "tags": ["STAR 改写", "模拟面试", "JD 匹配"],
        "suggested_kb_name": "我的简历素材库",
        "session_title": "💼 求职简历与面试",
        "starter_prompt": (
            "我要投【岗位名】。请先教我：怎么把一段校园项目经历用 STAR 法则改写成简历要点，"
            "并预演 3 个面试官最可能追问的问题。"
        ),
        "recommended_mode": "agent",
        "philosophy": "它帮你把经历讲得更清楚，但故事的主角是你——别让它替你编造没做过的事。",
    },
    {
        "id": "english",
        "name": "四六级 / 雅思备考",
        "category": "语言提升",
        "icon": "🌐",
        "tagline": "作文批改 + 口语陪练 + 词汇本",
        "description": "上传你的英语作文让它逐句批改，或开口语陪练模式练 IELTS 话题；它还能按错题生成词汇本。",
        "tags": ["作文批改", "口语陪练", "词汇本"],
        "suggested_kb_name": "",
        "session_title": "🌐 四六级 / 雅思备考",
        "starter_prompt": (
            "请帮我批改下面这段英语作文，指出语法、用词和逻辑问题，并给出范文级别的改写建议。"
        ),
        "recommended_mode": "llm",
        "philosophy": "它指出错误、陪你练习，但语感是每天读出来的——坚持比一次批改更重要。",
    },
    {
        "id": "club",
        "name": "社团与活动策划",
        "category": "校园生活",
        "icon": "🎯",
        "tagline": "从脑暴到执行清单一站搞定",
        "description": "迎新、比赛、志愿活动都能用：它帮你写策划书、预算表、分工与时间线，还能出宣传文案。",
        "tags": ["策划书", "预算分工", "宣传文案"],
        "suggested_kb_name": "我的活动策划库",
        "session_title": "🎯 社团与活动策划",
        "starter_prompt": (
            "我要办一场【活动名】。请先给我一份策划书骨架（目标/预算/分工/时间线/风险），"
            "并列出最容易踩的 3 个坑。"
        ),
        "recommended_mode": "agent",
        "philosophy": "它把繁琐的框架搭好，但活动的灵魂——创意和人情味——是你的。",
    },
    {
        "id": "literature",
        "name": "文献阅读助手",
        "category": "学术科研",
        "icon": "🔍",
        "tagline": "半小时读懂一篇硬核论文",
        "description": "把 PDF 论文放进知识库，它帮你三句话概括核心、拆解方法、对比相关文献、提炼可复用的思路。",
        "tags": ["核心概括", "方法拆解", "文献对比"],
        "suggested_kb_name": "我的文献库",
        "session_title": "🔍 文献阅读助手",
        "starter_prompt": (
            "我上传了一篇关于【方向】的论文。请先用三句话概括它的核心贡献，"
            "再拆解它的研究方法，并指出可能的局限。"
        ),
        "recommended_mode": "rag",
        "philosophy": "它帮你在信息洪流里定位重点，但批判性思考——判断谁对谁错——只能由你来做。",
    },
]


def get_catalog() -> list[dict]:
    """返回模板目录（深拷贝，避免调用方误改原数据）。"""
    import copy

    return copy.deepcopy(_TEMPLATE_LIST)


def get_template(tid: str) -> dict | None:
    """按 id 取单个模板；不存在返回 None。"""
    for t in _TEMPLATE_LIST:
        if t["id"] == tid:
            return t
    return None
