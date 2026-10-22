# -*- coding: utf-8 -*-
"""用户画像标签体系（30 条定稿词表 + 七大类 + 同义词归一）。

数据来源：`用户标签_汇总定稿_2026-10-22.md`（4 位组员清单合并，30 条无重复）。

**为什么要有这个文件，而不是把词表继续写在 api/chat.py 里：**

1. 词表原来只是"防标签表爆炸的闸门"（模型只能从里面挑），作用是**过滤**。
   现在它还要当**归一字典**用：用户说"六级"、说"CET6"、说"英语四六级"，
   模型可能抽出三种写法 → `user_tags` 里变成三条记录、权重各 1，
   画像被摊薄成"这人好像对英语有点兴趣但不多"。
   归一之后统一写"英语四六级"，权重 3，推荐才有信号。
   ⇒ 这条不做不会报错，只会悄悄退化，属于上线三个月后才发现的坑。

2. 30 条里混着三种性质不同的东西（目标/身份 vs 任务 vs 学科），
   不区分的话画像会被学科词淹没——一个人可能勾十个学科但只有一个目标，
   最后推荐出来的全是"给你推数据库资料"，而不是"毕业设计该怎么推进"。
   `type` 字段就是为这件事留的：首屏只让选目标/身份/任务，学科作为细分。

字段说明：
  name      标签名（落库就是它，唯一）
  category  七大类之一，用于前端分组与"这类标签该推哪批模板"
  type      目标 / 身份 / 任务 / 学科 / 方向 / 考试 / 场景 / 能力 / 状态
  parent    父标签名（None 表示顶层）；子级只做细分，不重复计权重
  desc      一句话说明（前端悬浮提示、模型抽标签时也看得到）
"""

from __future__ import annotations

# 七大类。顺序即前端展示顺序（先"要往哪去"，再"正在干什么"，最后"日常"）。
CATEGORY_ORDER: list[str] = [
    "升学考研",
    "求职实习",
    "课程考试",
    "科研论文",
    "编程技术",
    "语言考证",
    "校园生活",
]

# type 的合法值。前四类构成首屏可选集合，后两类（能力/状态）刻意不进首屏——
# 让用户一进来就面对"时间管理""心态调节"，等于系统在暗示"你有问题"。
TYPE_ORDER: list[str] = ["目标", "身份", "任务", "学科", "方向", "考试", "场景", "能力", "状态"]

# 不进首屏选择的类型（见上）
_TYPE_NOT_FOR_PICK = {"能力", "状态"}

_TAG_LIST: list[dict] = [
    # ---------- 1. 升学考研 ----------
    {
        "name": "考研备考",
        "category": "升学考研",
        "type": "目标",
        "parent": None,
        "desc": "备考期间的规划学习、复习进度、考点整理、刷题复盘",
    },
    {
        "name": "考研数学",
        "category": "升学考研",
        "type": "学科",
        "parent": "考研备考",
        "desc": "高数、线代、概率的公式与错题整理",
    },
    {
        "name": "考研英语",
        "category": "升学考研",
        "type": "学科",
        "parent": "考研备考",
        "desc": "词汇、长难句、历年真题、作文模板",
    },
    {
        "name": "考研政治",
        "category": "升学考研",
        "type": "学科",
        "parent": "考研备考",
        "desc": "时政要点、选择题、背诵材料",
    },
    {
        "name": "保研",
        "category": "升学考研",
        "type": "目标",
        "parent": None,
        "desc": "整理保研材料、绩点与竞赛信息、申请规划",
    },
    {
        "name": "留学申请",
        "category": "升学考研",
        "type": "目标",
        "parent": None,
        "desc": "选校、文书、语言成绩、网申流程与时间节点",
    },
    # ---------- 2. 求职实习 ----------
    {
        "name": "求职面试",
        "category": "求职实习",
        "type": "任务",
        "parent": None,
        "desc": "整理简历与常见面试问题，准备笔试与面试",
    },
    {
        "name": "实习",
        "category": "求职实习",
        "type": "身份",
        "parent": None,
        "desc": "记录岗位信息、任务安排、阶段学习心得",
    },
    {
        "name": "公务员",
        "category": "求职实习",
        "type": "目标",
        "parent": None,
        "desc": "国考、省考、事业单位；岗位选择、报名节点、行测申论",
    },
    # ---------- 3. 课程考试 ----------
    {
        "name": "期末考试",
        "category": "课程考试",
        "type": "任务",
        "parent": None,
        "desc": "复习重点、刷题、找复习资料",
    },
    {
        "name": "课程作业",
        "category": "课程考试",
        "type": "任务",
        "parent": None,
        "desc": "日常课后作业、期末整理课件笔记",
    },
    # ---------- 4. 科研论文 ----------
    {
        "name": "论文写作",
        "category": "科研论文",
        "type": "任务",
        "parent": None,
        "desc": "选题、查资料、整理结构、润色文字",
    },
    {
        "name": "文献阅读",
        "category": "科研论文",
        "type": "任务",
        "parent": None,
        "desc": "读懂文献、提取有用信息、做笔记",
    },
    {
        "name": "实验操作",
        "category": "科研论文",
        "type": "任务",
        "parent": None,
        "desc": "实验步骤、操作规范、数据记录与结果复盘",
    },
    {
        "name": "毕业设计",
        "category": "科研论文",
        "type": "任务",
        "parent": None,
        "desc": "选题、开题、做实验、开发、写报告、答辩全流程",
    },
    # ---------- 5. 编程技术 ----------
    {
        "name": "编程开发",
        "category": "编程技术",
        "type": "任务",
        "parent": None,
        "desc": "动手写代码、做软件或网站，用代码解决实际问题",
    },
    {
        "name": "Python",
        "category": "编程技术",
        "type": "学科",
        "parent": "编程开发",
        "desc": "写程序、做数据分析、自动化脚本",
    },
    {
        "name": "前端开发",
        "category": "编程技术",
        "type": "方向",
        "parent": "编程开发",
        "desc": "页面布局、按钮样式、页面交互",
    },
    {
        "name": "后端开发",
        "category": "编程技术",
        "type": "方向",
        "parent": "编程开发",
        "desc": "数据存储、业务逻辑、接口对接",
    },
    {
        "name": "算法竞赛",
        "category": "编程技术",
        "type": "任务",
        "parent": None,
        "desc": "程序算法比赛、算法题解能力训练",
    },
    {
        "name": "操作系统",
        "category": "编程技术",
        "type": "学科",
        "parent": None,
        "desc": "系统底层原理，进程、内存管理",
    },
    {
        "name": "计算机网络",
        "category": "编程技术",
        "type": "学科",
        "parent": None,
        "desc": "网络通信原理、网络协议、数据传输",
    },
    {
        "name": "数据库",
        "category": "编程技术",
        "type": "学科",
        "parent": None,
        "desc": "建库建表、SQL 语句、表结构设计",
    },
    {
        "name": "机器学习",
        "category": "编程技术",
        "type": "学科",
        "parent": None,
        "desc": "从数据中学习规律、做模型训练",
    },
    {
        "name": "深度学习",
        "category": "编程技术",
        "type": "学科",
        "parent": "机器学习",
        "desc": "神经网络，多用于图像、文本 AI 研究",
    },
    # ---------- 6. 语言考证 ----------
    {
        "name": "英语四六级",
        "category": "语言考证",
        "type": "考试",
        "parent": None,
        "desc": "准备参加大学英语四级、六级考试",
    },
    {
        "name": "考证",
        "category": "语言考证",
        "type": "任务",
        "parent": None,
        "desc": "教师资格证、计算机等级、职业资格等证书",
    },
    # ---------- 7. 校园生活 ----------
    {
        "name": "社团活动",
        "category": "校园生活",
        "type": "场景",
        "parent": None,
        "desc": "社团、学生会、志愿活动的策划、招新、协作",
    },
    {
        "name": "时间管理",
        "category": "校园生活",
        "type": "能力",
        "parent": None,
        "desc": "把课程、备考、实习、社团的日程排明白",
    },
    {
        "name": "心态调节",
        "category": "校园生活",
        "type": "状态",
        "parent": None,
        "desc": "考试、求职、人际压力下的情绪疏导与自我调整",
    },
]

# 主词表：落库与模型受控词表都用它（顺序稳定，便于断言与展示）
USER_PROFILE_TAGS: list[str] = [t["name"] for t in _TAG_LIST]

# 名字 → 定义（校验、写库、前端查详情）
TAGS_BY_NAME: dict[str, dict] = {t["name"]: t for t in _TAG_LIST}

# 小写名 → 规范名。词表里有 "Python"，模型常输出 "python"，不能当成未知标签。
_BY_LOWER: dict[str, str] = {t["name"].lower(): t["name"] for t in _TAG_LIST}


# ==================== 同义词归一 ====================
# 键是**会被写成标签的原始说法**（小写用于 ASCII 比较），值是词表里的规范名。
# 加新别名前先问一句：它真的只会指向这一个标签吗？
#   反例："英语" —— 可能指四六级、可能指考研英语、也可能指专业英语课，语义不唯一，
#   所以**故意不收**；收了就会把"看英文文献"错算成"在准备四六级"。
_ALIASES_RAW: dict[str, str] = {
    # —— 英语四六级 ——
    "四级": "英语四六级",
    "六级": "英语四六级",
    "四六级": "英语四六级",
    "英语四级": "英语四六级",
    "英语六级": "英语四六级",
    "cet": "英语四六级",
    "cet4": "英语四六级",
    "cet6": "英语四六级",
    "cet-4": "英语四六级",
    "cet-6": "英语四六级",
    "大学英语四级": "英语四六级",
    "大学英语六级": "英语四六级",
    # —— 升学 ——
    "考研": "考研备考",
    "考研复习": "考研备考",
    "推免": "保研",
    "保研夏令营": "保研",
    "夏令营": "保研",
    "出国留学": "留学申请",
    "留学": "留学申请",
    # 雅思/托福目前没有独立标签（已知缺口），归到最接近的"留学申请"
    "雅思": "留学申请",
    "托福": "留学申请",
    "ielts": "留学申请",
    "toefl": "留学申请",
    # —— 求职 ——
    "考公": "公务员",
    "国考": "公务员",
    "省考": "公务员",
    "公务员考试": "公务员",
    "事业单位": "公务员",
    "面试": "求职面试",
    "简历": "求职面试",
    "秋招": "求职面试",
    "春招": "求职面试",
    "找工作": "求职面试",
    # —— 课程 ——
    "期末": "期末考试",
    "期末复习": "期末考试",
    "作业": "课程作业",
    "课后作业": "课程作业",
    # —— 科研 ——
    "论文": "论文写作",
    "写论文": "论文写作",
    "文献": "文献阅读",
    "读文献": "文献阅读",
    "毕设": "毕业设计",
    "毕业论文": "毕业设计",
    "实验": "实验操作",
    # —— 编程 ——
    "编程": "编程开发",
    "写代码": "编程开发",
    "代码开发": "编程开发",
    "python3": "Python",
    "前端": "前端开发",
    "web前端": "前端开发",
    "后端": "后端开发",
    "服务端": "后端开发",
    "算法": "算法竞赛",
    "算法题": "算法竞赛",
    "leetcode": "算法竞赛",
    "acm": "算法竞赛",
    "蓝桥杯": "算法竞赛",
    "sql": "数据库",
    "mysql": "数据库",
    "数据库设计": "数据库",
    "操作系统原理": "操作系统",
    "计网": "计算机网络",
    "网络协议": "计算机网络",
    "神经网络": "深度学习",
    # —— 考证 ——
    "证书": "考证",
    "考证备考": "考证",
    "教资": "考证",
    "教师资格证": "考证",
    "计算机等级考试": "考证",
    # —— 校园生活 ——
    "社团": "社团活动",
    "学生会": "社团活动",
    "志愿活动": "社团活动",
    "拖延": "时间管理",
    "焦虑": "心态调节",
    "压力": "心态调节",
}

# 统一小写建索引：alias 里本来就全小写，ASCII 大小写不该影响匹配
ALIASES: dict[str, str] = {k.lower(): v for k, v in _ALIASES_RAW.items()}


def _clean(raw: object) -> str:
    """去掉首尾空白与零宽字符、把连续空白压成一个空格。

    模型偶尔输出 "考研数学 "（尾随空格）或 "考研  数学"，不洗一遍会变成两条标签。
    """
    if raw is None:
        return ""
    s = str(raw).replace("\u200b", "").replace("\ufeff", "").strip()
    return " ".join(s.split())


def normalize_tag(raw: object) -> str:
    """把一个自由写法归一到词表里的规范名；归不上则**原样返回**（不清空）。

    为什么归不上要原样保留：`add_user_tags` 是通用写入口，检索偏置测试、
    以后别的模块都可能写词表外的主题词（"数学""英语"）。在这里一律丢弃会把
    那些正当的写入悄悄吃掉。**硬闸门放在 filter_known_tags()**，
    只有"模型自动抽标签"那条路才要求必须命中词表。
    """
    t = _clean(raw)
    if not t:
        return ""
    if t in TAGS_BY_NAME:
        return t
    low = t.lower()
    if low in _BY_LOWER:  # Python / python
        return _BY_LOWER[low]
    if low in ALIASES:  # CET6 / 六级 / 六级考试
        return ALIASES[low]
    return t


def normalize_tags(raw_tags: object) -> list[str]:
    """批量归一 + 去重（保留首次出现顺序）。空输入/非列表都安全返回 []。"""
    if not raw_tags:
        return []
    if isinstance(raw_tags, (str, bytes)):  # 传成单个字符串时别按字符遍历
        raw_tags = [raw_tags]
    out: list[str] = []
    seen: set[str] = set()
    for item in raw_tags:  # type: ignore[union-attr]
        t = normalize_tag(item)
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def match_tag(raw: object) -> str | None:
    """严格匹配：只有能落到词表里的才返回规范名，否则 None。"""
    t = normalize_tag(raw)
    return t if t in TAGS_BY_NAME else None


def filter_known_tags(raw_tags: object, limit: int | None = None) -> list[str]:
    """受控词表闸门：丢弃一切词表外的标签（模型抽标签必须过这一关）。"""
    out = [t for t in normalize_tags(raw_tags) if t in TAGS_BY_NAME]
    return out[:limit] if limit else out


def is_known(name: str) -> bool:
    return name in TAGS_BY_NAME


def category_of(name: str) -> str | None:
    t = TAGS_BY_NAME.get(name)
    return t["category"] if t else None


def tags_of_category(category: str) -> list[dict]:
    return [t for t in _TAG_LIST if t["category"] == category]


def pickable() -> list[str]:
    """首屏可选的标签名（排除"能力/状态"两类，见模块头注释）。"""
    return [t["name"] for t in _TAG_LIST if t["type"] not in _TYPE_NOT_FOR_PICK]


def children_of(name: str) -> list[str]:
    return [t["name"] for t in _TAG_LIST if t["parent"] == name]


def taxonomy() -> dict:
    """给前端的完整标签体系（一次性下发，前端不必硬编码任何标签名）。"""
    import copy

    groups = []
    for cat in CATEGORY_ORDER:
        items = tags_of_category(cat)
        if items:
            groups.append({"category": cat, "tags": copy.deepcopy(items)})
    return {
        "categories": copy.deepcopy(CATEGORY_ORDER),
        "types": copy.deepcopy(TYPE_ORDER),
        "groups": groups,
        "pickable": pickable(),
        "total": len(_TAG_LIST),
    }
