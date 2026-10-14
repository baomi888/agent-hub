# -*- coding: utf-8 -*-
"""
生成「知穗」2027 挑战杯路演 PPT 的 PPTD 工程。
风格：深蓝科技底 + 暖米黄点缀（对齐参考件设计语言，但全部为原生可编辑元素）
尺寸：960 x 540 pt (16:9)
页数：28（通知限制 <= 30）
"""
import io
import os

OUT = r'C:\Users\HUAWEI\Desktop\苞米agent\outputs\知穗_路演PPT'
PAGES = os.path.join(OUT, 'pages')
os.makedirs(PAGES, exist_ok=True)

# ---------------------------------------------------------------- 设计令牌
NAVY      = "#0B1F3A"   # 主背景 深海军蓝
NAVY_DEEP = "#071528"   # 更深底
NAVY_CARD = "#12294A"   # 卡片底
NAVY_LINE = "#1E3E68"   # 分隔线
CYAN      = "#4FC3E8"   # 科技亮青
CYAN_DIM  = "#2E7FA8"
GOLD      = "#E8B961"   # 暖米黄金（点缀/强调）
GOLD_DEEP = "#C8892A"   # 玉米金（品牌色）
PAPER     = "#F5F0E1"   # 暖米黄纸（主文字）
PAPER_DIM = "#B8BCBF"   # 次级文字
WHITE     = "#FFFFFF"
RED       = "#E06C5A"

FONT = "{latin: Arial, ea: Microsoft YaHei, win: Microsoft YaHei, mac: PingFang SC}"
FONT_TITLE = "{latin: Georgia, ea: Microsoft YaHei, win: Microsoft YaHei, mac: Songti SC}"


def y(s):
    """yaml 标量安全引号"""
    return '"%s"' % s.replace('\\', '\\\\').replace('"', '\\"')


def txt(eid, x, ty, w, h, text, size=16, color=PAPER, bold=False,
        align="left", font=FONT, lh=1.35):
    lines = text.split('\n')
    if len(lines) > 1:
        body = '      text: |-\n' + '\n'.join('        ' + l for l in lines) + '\n'
    else:
        body = '      text: %s\n' % y(text)
    return (
        "  - elementId: %s\n"
        "    elementType: text\n"
        "    bounds: [%s, %s, %s, %s]\n"
        "    content:\n"
        "%s"
        "      fontFamily: %s\n"
        "      fontSize: %s\n"
        "      color: \"%s\"\n"
        "      bold: %s\n"
        "      align: %s\n"
        "      lineHeight: %s\n"
    ) % (eid, x, ty, w, h, body, font, size, color,
         'true' if bold else 'false', align, lh)


def rect(eid, x, ty, w, h, fill, border=None, bw=0, radius=None):
    s = ("  - elementId: %s\n"
         "    elementType: shape\n"
         "    bounds: [%s, %s, %s, %s]\n"
         "    shapeName: %s\n"
         "    fill: {type: solid, color: \"%s\"}\n"
         ) % (eid, x, ty, w, h, 'roundRect' if radius else 'rect', fill)
    if border:
        s += "    border: {width: %s, color: \"%s\"}\n" % (bw, border)
    else:
        s += "    border: {width: 0, color: \"%s\"}\n" % fill
    return s


def hline(eid, x, ty, w, color=NAVY_LINE, bw=1):
    return rect(eid, x, ty, w, bw, color)


def page(body, bg=NAVY, notes=None):
    s = "pageType: content\n"
    s += "background: {type: solid, color: \"%s\"}\n" % bg
    if notes:
        s += "notes: %s\n" % y(notes)
    s += "elements:\n"
    s += body
    return s


def fnum(num):
    """右下角页码（独立小元素，放最上层避免遮挡告警）"""
    return txt("pg-num", 878, 508, 42, 16, "%02d" % num, 10, CYAN_DIM, False,
               "right")


def header(section, subtitle, num=None):
    """统一页眉：左上章节标 + 副标题 + 右上作品名 + 分隔线"""
    b = ""
    b += rect("hdr-accent", 48, 34, 4, 22, GOLD)
    b += txt("hdr-sec", 62, 32, 260, 26, section, 19, GOLD, True)
    if subtitle:
        b += txt("hdr-sub", 62, 60, 620, 20, subtitle, 12, PAPER_DIM)
    b += txt("hdr-brand", 700, 34, 212, 24, "知穗", 17, CYAN, True, "right",
             FONT_TITLE)
    b += hline("hdr-line", 48, 90, 864, NAVY_LINE, 1)
    return b


def bullet_card(eid, x, ty, w, h, title, lines, accent=GOLD, tsize=15,
                bsize=12):
    b = rect(eid + "-bg", x, ty, w, h, NAVY_CARD, NAVY_LINE, 1)
    b += rect(eid + "-bar", x, ty, 3, h, accent)
    b += txt(eid + "-t", x + 16, ty + 10, w - 28, 28, title, tsize, accent, True)
    if lines:
        b += txt(eid + "-b", x + 16, ty + 40, w - 28, h - 52,
                 "\n".join(lines), bsize, PAPER, False, "left", FONT, 1.5)
    return b


P = []  # (filename, content)

# =============================================================== 01 封面
P.append(("01-cover.page", page(
    rect("bg-band", 0, 0, 960, 540, NAVY_DEEP)
    + rect("bg-glow", 0, 300, 960, 240, "#0E2744")
    + rect("accent-l", 60, 150, 5, 64, GOLD)
    + txt("name", 84, 138, 500, 100, "知穗", 68, PAPER, True, "left", FONT_TITLE)
    + txt("sub", 84, 232, 700, 40,
          "面向大学生个性化AI学习助手", 26, GOLD, True)
    + hline("rule", 84, 292, 560, CYAN_DIM, 2)
    + txt("desc", 84, 312, 660, 52,
          "面向大学生的 Agent 个性化学习助手\n"
          "备考 + 求职双场景 · 以“辅助学习而非替代思考”为 AI 素养导向", 13, PAPER_DIM,
          False, "left", FONT, 1.6)
    + txt("meta", 84, 400, 420, 74,
          "负责人：冯靖豪\n"
          "汇报人：冯靖豪\n"
          "项目类别：科技发明制作 B 类（信息技术）", 13, PAPER, False,
          "left", FONT, 1.7)
    + txt("school", 84, 492, 620, 22,
          "广东石油化工学院 · 2027 年“挑战杯”大学生课外学术科技作品竞赛",
          11, CYAN_DIM)
    + txt("rt", 660, 400, 240, 80,
          "计算机学院\n数据科学与大数据技术\n2025 级", 12, PAPER_DIM,
          False, "right", FONT, 1.7)
    , NAVY_DEEP, "开场：一句话定位 + 作品类别。汇报人默认填冯靖豪，如需更换请改。")))

# =============================================================== 02 目录
toc = [
    ("01", "痛点与背景", "大学生学习场景的真实困境"),
    ("02", "调研依据", "56 份有效问卷的需求验证"),
    ("03", "系统方案", "八层架构与核心技术栈"),
    ("04", "技术创新", "五项可验证的差异化能力"),
    ("05", "已有基础", "已落地功能与性能实测"),
    ("06", "验证与推广", "应用价值与转化路径"),
    ("07", "规划与团队", "里程碑与风险对策"),
]
b = header("目录", "CONTENTS")
yy = 132
for i, (n, t, d) in enumerate(toc):
    col = 0 if i < 4 else 1
    row = i if i < 4 else i - 4
    x = 60 + col * 440
    ty = yy + row * 62
    b += txt("toc-n%d" % i, x, ty, 40, 30, n, 22, GOLD, True, "left", FONT_TITLE)
    b += txt("toc-t%d" % i, x + 46, ty - 2, 340, 24, t, 16, PAPER, True)
    b += txt("toc-d%d" % i, x + 46, ty + 22, 340, 20, d, 11, PAPER_DIM)
    b += hline("toc-l%d" % i, x, ty + 48, 384, NAVY_LINE, 1)
P.append(("02-toc.page", page(b + txt("foot", 60, 500, 400, 18,
    "汇报时长约 8 分钟 · 28 页", 10, CYAN_DIM), NAVY)))

# =============================================================== 03 痛点
b = header("痛点与背景", "大学生学习场景中的三个真实困境", 3)
pains = [
    ("无长期记忆",
     ["通用大模型仅依赖对话上下文",
      "上下文一经压缩或丢失，学习资料、\n薄弱点与个人规划便无从沉淀，\n对话越往后越“失忆”"],
     RED),
    ("个性化不足",
     ["每次对话都要重复自我介绍",
      "系统无法基于历史形成真正贴合\n个人的学习建议"],
     GOLD),
    ("未融入学习流",
     ["工具与真实学习场景脱节",
      "难以促成自发、高频的使用习惯"],
     CYAN),
]
for i, (t, ls, c) in enumerate(pains):
    b += bullet_card("p%d" % i, 60 + i * 288, 150, 264, 230, t, ls, c, 17, 12)
b += rect("concl-bg", 60, 408, 840, 76, "#12294A", NAVY_LINE, 1)
b += rect("concl-bar", 60, 408, 4, 76, GOLD)
b += txt("concl-t", 80, 420, 200, 22, "核心痛点", 14, GOLD, True)
b += txt("concl-b", 80, 446, 800, 30,
         "学生需要的不是又一个通用对话框，而是一个能长期记住自己、"
         "越用越懂自己的学习助手。", 12, PAPER, False, "left", FONT, 1.5)
P.append(("03-pain.page", page(b, NAVY)))

# =============================================================== 04 政策与趋势
b = header("痛点与背景", "需求真实：政策、行业与高校三重印证", 4)
b += bullet_card("g1", 60, 150, 404, 168, "国家与政策导向",
    ["教育部公开数据：全国普通高校在校生规模超 4700 万人",
     "“十四五”教育数字化推动 AI 赋能教学",
     "高校与运营商明确“辅助学习、拒绝直接代做”"], GOLD, 16, 12)
b += bullet_card("g2", 488, 150, 412, 168, "行业热度数据",
    ["抖音 AI 内容兴趣用户同比增长 105%",
     "AI 学习类内容观看量同比增长 200%",
     "AI 应用分发从应用商店转向内容平台与社交分享"], CYAN, 16, 12)
b += bullet_card("g3", 60, 338, 404, 154, "高校落地先例",
    ["大连理工联合腾讯“连小理”全场景 AI 学伴（2026-10）",
     "配套 AI 智能体实训营与新生设计大赛",
     "中国移动灵犀 AI 助手设校园专区"], CYAN, 16, 12)
b += bullet_card("g4", 488, 338, 412, 154, "竞争格局判断",
    ["通用 Agent 平台已高度内卷",
     "Dify 12.7 万+ ★、n8n 17.1 万+ ★ 占据通用市场",
     "垂直教育场景仍是空白切口"], GOLD, 16, 12)
P.append(("04-trend.page", page(b, NAVY)))

# =============================================================== 05 问卷方法
b = header("调研依据", "需求不是拍脑袋：56 份问卷的实证", 5)
b += txt("m-t", 60, 140, 500, 30, "调研设计", 20, GOLD, True)
b += txt("m-b", 60, 178, 480, 130,
         "· 目标人群：全日制本科在校生，覆盖文 / 理 / 工 / 医 / 商\n"
         "· 发放渠道：校园社群、小红书、抖音、B 站、朋友圈、线下扫码\n"
         "· 题目设计：7 题精简版，1–2 分钟完成，提高完卷率\n"
         "· 交叉分析：学科 × 场景，验证模板的跨学科普适性",
         12, PAPER, False, "left", FONT, 1.7)
b += rect("m-note", 60, 322, 480, 96, "#12294A", GOLD_DEEP, 1)
b += txt("m-note-t", 78, 334, 440, 22, "样本量说明", 13, GOLD, True)
b += txt("m-note-b", 78, 360, 444, 48,
         "本页数据来自有效样本 56 份。问卷设计目标为 200 份有效样本，"
         "当前处于首轮验证阶段，结论已标注样本量，未做外推。",
         11, PAPER, False, "left", FONT, 1.5)
# 右：学科分布
b += txt("d-t", 580, 140, 320, 30, "样本学科分布", 20, GOLD, True)
dist = [("理科 / 工科", 31, 55.36, CYAN),
        ("人文社科", 13, 23.21, GOLD),
        ("经管商科", 5, 8.93, PAPER_DIM),
        ("艺术体育", 3, 5.36, PAPER_DIM),
        ("医科", 1, 1.79, PAPER_DIM),
        ("其他", 3, 5.36, PAPER_DIM)]
for i, (n, v, pct, c) in enumerate(dist):
    ty = 184 + i * 44
    b += txt("d-n%d" % i, 580, ty, 110, 20, n, 12, PAPER)
    b += rect("d-bg%d" % i, 694, ty + 4, 150, 12, "#16325A")
    b += rect("d-fg%d" % i, 694, ty + 4, max(6, int(150 * pct / 55.36)), 12, c)
    b += txt("d-v%d" % i, 852, ty, 60, 20, "%d 人" % v, 11, PAPER_DIM, False, "right")
b += txt("d-note", 580, 452, 332, 34,
         "身份构成：大一 / 大二本科生 85.71%（48 人）", 10, CYAN_DIM)
P.append(("05-survey.page", page(b, NAVY)))

# =============================================================== 06 场景需求
b = header("调研依据", "场景需求排行：考研与笔记整理居首", 6)
scenes = [("考研复习规划与题库", 41, 73.21),
          ("课程笔记整理与复习", 40, 71.43),
          ("论文写作与文献综述", 38, 67.86),
          ("期末冲刺自测", 36, 64.29),
          ("求职简历与模拟面试", 35, 62.50),
          ("四六级 / 雅思托福备考", 35, 62.50),
          ("实习与求职信息聚合", 35, 62.50),
          ("文献阅读与核心提炼", 28, 50.00)]
for i, (n, v, pct) in enumerate(scenes):
    ty = 136 + i * 40
    c = GOLD if i < 3 else CYAN
    b += txt("s-n%d" % i, 60, ty, 180, 20, n, 12, PAPER)
    b += rect("s-bg%d" % i, 250, ty + 4, 480, 13, "#16325A")
    b += rect("s-fg%d" % i, 250, ty + 4, int(480 * pct / 73.21), 13, c)
    b += txt("s-v%d" % i, 742, ty - 1, 160, 20,
             "%d 人 · %.2f%%" % (v, pct), 11, PAPER_DIM, False, "right")
b += rect("s-concl", 60, 462, 840, 54, "#12294A", NAVY_LINE, 1)
b += rect("s-concl-bar", 60, 462, 4, 54, GOLD)
b += txt("s-concl-t", 78, 476, 800, 28,
         "多选题，样本 56 份。前三位场景将作为首发模板优先打磨交互。",
         12, PAPER, False, "left", FONT, 1.4)
P.append(("06-scene.page", page(b, NAVY)))

# =============================================================== 07 顾虑与意愿
b = header("调研依据", "用户最大的顾虑，正是本项目的技术切口", 7)
b += txt("c1-t", 60, 138, 400, 28, "最担心的问题（多选）", 18, GOLD, True)
worries = [("答案不准还装自信", 47, 83.93, RED),
           ("隐私 / 资料被滥用", 31, 55.36, GOLD),
           ("让我变懒、不动脑", 27, 48.21, CYAN),
           ("收费不透明", 25, 44.64, PAPER_DIM),
           ("操作太复杂", 18, 32.14, PAPER_DIM)]
for i, (n, v, pct, c) in enumerate(worries):
    ty = 180 + i * 42
    b += txt("w-n%d" % i, 60, ty, 150, 20, n, 12, PAPER)
    b += rect("w-bg%d" % i, 218, ty + 4, 250, 13, "#16325A")
    b += rect("w-fg%d" % i, 218, ty + 4, int(250 * pct / 83.93), 13, c)
    b += txt("w-v%d" % i, 476, ty, 74, 20, "%.2f%%" % pct, 11, PAPER_DIM,
             False, "right")
b += rect("hit", 60, 396, 490, 120, "#12294A", GOLD_DEEP, 1)
b += txt("hit-t", 78, 408, 440, 24,
         "83.93% 担心“答案不准还装自信”", 14, GOLD, True)
b += txt("hit-b", 78, 436, 452, 68,
         "这正是本项目“可溯源问答”的直接依据：所有回答附带可点击的原文角标，"
         "可回溯至知识库具体片段，把“相信 AI”变成“核对 AI”。",
         11, PAPER, False, "left", FONT, 1.5)
# 右：使用意愿
b += txt("i-t", 590, 138, 310, 28, "使用意愿", 18, GOLD, True)
intent = [("马上用", 22, 39.29, GOLD), ("观望一下", 33, 58.93, CYAN),
          ("不太感兴趣", 1, 1.79, PAPER_DIM), ("完全不想用", 0, 0, PAPER_DIM)]
for i, (n, v, pct, c) in enumerate(intent):
    ty = 184 + i * 52
    b += txt("i-n%d" % i, 590, ty, 140, 20, n, 13, PAPER)
    b += rect("i-bg%d" % i, 590, ty + 24, 280, 12, "#16325A")
    b += rect("i-fg%d" % i, 590, ty + 24, max(3, int(280 * pct / 58.93)), 12, c)
    b += txt("i-v%d" % i, 762, ty - 2, 108, 20,
             "%d 人 · %.2f%%" % (v, pct), 11, PAPER_DIM, False, "right")
b += txt("i-note", 590, 404, 310, 40,
         "58.93% 选择“观望”——说明定位成立，\n但需要真实可用的产品来建立信任。",
         11, PAPER_DIM, False, "left", FONT, 1.5)
P.append(("07-concern.page", page(b, NAVY)))

# =============================================================== 08 架构总览
b = header("系统方案", "八层架构：从界面到个性化输出", 8)
layers = [
    ("前端体验层", "Next.js 16 · React 19 · Tailwind v4", GOLD),
    ("接口契约层", "/api/v1 统一响应 · 错误码 · 鉴权 · Mock 规则", CYAN),
    ("Agent 编排层", "LangChain v1 + LangGraph · 意图识别 · 工具调用", GOLD),
    ("知识库层", "现状 Chroma 向量库 / 规划文本文件 + 标签 + 全文检索", CYAN),
    ("联网搜索层", "Serper · 与知识库分级协同，避免知识闭环", GOLD),
    ("数据层", "SQLite（WAL）· 用户 / 会话 / 标签三类模型", CYAN),
    ("定时任务层", "每日总结 · 每日委托 · 幂等与重试保障", GOLD),
    ("输出层", "对话应答 · 思维导图打卡图 · 学习路径进度", CYAN),
]
for i, (t, tech, c) in enumerate(layers):
    ty = 118 + i * 48
    b += rect("l-bg%d" % i, 60, ty, 620, 44, NAVY_CARD, NAVY_LINE, 1)
    b += rect("l-bar%d" % i, 60, ty, 4, 44, c)
    b += txt("l-n%d" % i, 74, ty + 13, 28, 20, "%d" % (i + 1), 12, c, True,
             "center", FONT_TITLE)
    b += txt("l-t%d" % i, 110, ty + 12, 180, 22, t, 13, c, True)
    b += txt("l-tech%d" % i, 296, ty + 13, 378, 20, tech, 9, PAPER_DIM)
b += rect("pr", 700, 118, 200, 206, "#12294A", GOLD_DEEP, 1)
b += txt("pr-t", 716, 130, 172, 22, "三大架构原则", 13, GOLD, True)
b += txt("pr-b", 716, 156, 172, 160,
         "① 不做纯向量库\n   可读可改文本文件 +\n   结构化标签 + 全文检索\n\n"
         "② 必须保留联网搜索\n   规避知识闭环与错误放大\n\n"
         "③ 舍弃传统奖惩体系\n   改用正向反馈",
         9, PAPER, False, "left", FONT, 1.5)
b += rect("sec", 700, 336, 200, 188, NAVY_CARD, NAVY_LINE, 1)
b += txt("sec-t", 716, 348, 172, 22, "安全网关", 13, GOLD, True)
b += txt("sec-b", 716, 374, 172, 142,
         "· 默认拒绝，端点全挂鉴权\n\n"
         "· 按 owner 强制数据隔离\n\n"
         "· 归属校验失败返回 404\n  而非 403（防枚举）\n\n"
         "· 限流与配额 → 全局 429\n\n"
         "· SSRF 防护 · 1MB 分块上传",
         9, PAPER, False, "left", FONT, 1.5)
P.append(("08-arch.page", page(b, NAVY)))

# =============================================================== 09 技术栈
b = header("系统方案", "核心技术栈与选型依据", 9)
stack = [("对话模型", "DeepSeek deepseek-chat", "OpenAI 兼容接口"),
         ("视觉模型", "阿里百炼 qwen-vl-plus", "图片理解"),
         ("向量化", "阿里百炼 text-embedding-v3", "中文语义检索"),
         ("Agent 框架", "LangChain create_agent + LangGraph", "不挂 checkpoint"),
         ("向量库", "Chroma 0.5.23", "多 collection 隔离"),
         ("会话存储", "SQLite（WAL 模式）", "参数化 · 10 轮窗口截断"),
         ("联网搜索", "Serper API", "gl=cn / hl=zh-cn 锚定"),
         ("前端", "Next.js 16 / React 19 / Tailwind v4", "LXGW WenKai 字体")]
for i, (k, v, note) in enumerate(stack):
    col = i % 2
    row = i // 2
    x = 60 + col * 434
    ty = 134 + row * 80
    b += rect("st-bg%d" % i, x, ty, 410, 68, NAVY_CARD, NAVY_LINE, 1)
    b += rect("st-bar%d" % i, x, ty, 3, 68, GOLD if i % 2 == 0 else CYAN)
    b += txt("st-k%d" % i, x + 16, ty + 10, 150, 20, k, 12, GOLD, True)
    b += txt("st-v%d" % i, x + 16, ty + 32, 380, 20, v, 11, PAPER)
    b += txt("st-n%d" % i, x + 16, ty + 50, 380, 16, note, 9, PAPER_DIM)
b += txt("st-foot", 60, 468, 840, 40,
         "选型原则：核心检索与对话链路可完全本地化，零外部强依赖；"
         "所有外部服务均可替换为自建方案。", 11, CYAN_DIM, False, "left",
         FONT, 1.5)
P.append(("09-stack.page", page(b, NAVY)))

# =============================================================== 10 核心链路
b = header("系统方案", "核心链路：登录 → 对话 → 沉淀 → 检索 → 委托", 10)
steps = [("登录", "多用户\n鉴权隔离", GOLD),
         ("对话", "SSE 流式\n增量返回", CYAN),
         ("采集", "对话中沉淀\n学习画像", CYAN),
         ("标签", "关键词钩子\n自动调库", GOLD),
         ("检索", "专属知识库\n检索召回", CYAN),
         ("回复", "带原文角标\n的可溯源回答", GOLD),
         ("委托", "每日任务\n与反馈", CYAN),
         ("总结", "思维导图式\n打卡复盘", GOLD)]
for i, (t, d, c) in enumerate(steps):
    x = 52 + i * 108
    b += rect("k-bg%d" % i, x, 176, 96, 132, NAVY_CARD, c, 1)
    b += txt("k-t%d" % i, x, 188, 96, 26, t, 15, c, True, "center")
    b += hline("k-l%d" % i, x + 20, 220, 56, c, 1)
    b += txt("k-d%d" % i, x + 6, 232, 84, 60, d, 9, PAPER, False, "center",
             FONT, 1.5)
    if i < 7:
        b += txt("k-a%d" % i, x + 96, 232, 12, 20, "▶", 9, CYAN_DIM, False,
                 "center")
b += rect("k-note", 52, 336, 856, 90, "#12294A", NAVY_LINE, 1)
b += rect("k-note-bar", 52, 336, 4, 90, GOLD)
b += txt("k-note-t", 72, 348, 400, 22,
         "Mock 演示链路（院赛申报阶段）", 13, GOLD, True)
b += txt("k-note-b", 72, 374, 816, 44,
         "申报阶段以 Mock 数据驱动上述核心链路，业务逻辑用固定 JSON 返回，"
         "确保可现场演示；真实编码在院赛阶段启动，由刘佳林（Agent 编排、每日委托与总结）"
         "与林宏亮（知识库、检索、联网搜索融合）分工实现。",
         10, PAPER, False, "left", FONT, 1.5)
P.append(("10-flow.page", page(b, NAVY)))

# =============================================================== 11 创新点总览
b = header("技术创新", "五项差异化创新", 11)
inno = [
    ("01", "长期用户记忆", "对话中采集 → 蒸馏 → 沉淀专属知识库，\n突破上下文窗口限制，越用越懂你", GOLD),
    ("02", "标签钩子", "识别对话关键信息自动调库，\n用户无需重复自我介绍", CYAN),
    ("03", "知识库 × 联网协同", "私有优先、公开补齐，\n规避知识闭环与错误循环放大", GOLD),
    ("04", "学习科学驱动设计", "每日总结 / 每日委托 / 关卡式路径，\n主动舍弃传统奖惩体系", CYAN),
    ("05", "强数据隔离", "归属校验返回 404 而非 403，\n避免资源存在性被枚举", GOLD),
]
for i, (n, t, d, c) in enumerate(inno):
    x = 60 + (i % 2) * 434
    ty = 132 + (i // 2) * 124
    if i == 4:
        x = 60
        ty = 380
    b += rect("in-bg%d" % i, x, ty, 404 if i == 4 else 410, 108,
              NAVY_CARD, NAVY_LINE, 1)
    b += rect("in-bar%d" % i, x, ty, 3, 108, c)
    b += txt("in-n%d" % i, x + 16, ty + 10, 40, 32, n, 20, c, True,
             "left", FONT_TITLE)
    b += txt("in-t%d" % i, x + 62, ty + 16, 320, 24, t, 15, PAPER, True)
    b += txt("in-d%d" % i, x + 62, ty + 44, 330, 54, d, 10, PAPER_DIM,
             False, "left", FONT, 1.5)
P.append(("11-innovation.page", page(b, NAVY)))

# =============================================================== 12 创新1 长期记忆
b = header("技术创新 · 01", "长期用户记忆：从「每次失忆」到「越用越懂你」", 12)
b += txt("k-t", 60, 136, 480, 28, "问题", 17, RED, True)
b += txt("k-b", 60, 170, 470, 60,
         "通用大模型仅依赖对话上下文，上下文一经压缩或丢失，"
         "用户的学习资料、知识薄弱点与个人规划便无从沉淀，"
         "对话越往后越“失忆”，个性化能力无法累积。",
         11, PAPER, False, "left", FONT, 1.6)
b += txt("s-t", 570, 136, 340, 28, "本作品做法", 17, GOLD, True)
b += txt("s-b", 570, 170, 334, 60,
         "在对话交互中持续采集用户信息，经蒸馏提炼为结构化条目后"
         "沉淀至用户专属知识库，随交互增多不断扩充，"
         "突破上下文窗口限制。",
         11, PAPER, False, "left", FONT, 1.6)
b += rect("flow", 60, 252, 844, 108, NAVY_CARD, NAVY_LINE, 1)
fsteps = ["对话交互", "信息采集", "蒸馏提炼", "标签归类", "专属知识库", "个性化应答"]
for i, s in enumerate(fsteps):
    x = 78 + i * 138
    b += txt("f-t%d" % i, x, 268, 128, 40, s, 11, GOLD if i in (2, 4) else CYAN,
             True, "center", FONT, 1.4)
    if i < 5:
        b += txt("f-a%d" % i, x + 126, 276, 14, 20, "▶", 10, PAPER_DIM,
                 False, "center")
    b += rect("f-c%d" % i, x, 314, 128, 32, "#16325A")
    note_i = {0: "自然对话", 1: "自动识别", 2: "核心难点", 3: "结构化",
              4: "持续累积", 5: "贴合个人"}.get(i, "")
    b += txt("f-n%d" % i, x, 320, 128, 20, note_i, 10, PAPER, True, "center")
b += rect("res", 60, 384, 844, 116, "#12294A", GOLD_DEEP, 1)
b += txt("res-t", 80, 396, 400, 24, "技术路线与实现阶段", 14, GOLD, True)
b += txt("res-b", 80, 424, 800, 66,
         "本创新对应 MemGPT、Mem0、MemoryBank 等长期记忆研究的技术路线，"
         "本项目将其落地到校园学习场景，是本作品区别于通用对话工具的核心。\n"
         "设计目标：蒸馏条目抽样准确率、标签钩子召回率。"
         "实现阶段：方案与接口设计已完成，编码在院赛阶段启动。",
         11, PAPER, False, "left", FONT, 1.6)
P.append(("12-innovation1.page", page(b, NAVY)))

# =============================================================== 13 创新2 标签钩子
b = header("技术创新 · 02", "标签钩子：免重复自我介绍的个性化应答", 13)
b += rect("cmp-l", 60, 132, 404, 240, NAVY_CARD, NAVY_LINE, 1)
b += txt("cmp-l-t", 80, 146, 360, 26, "通用做法：每次都从零开始", 15, RED, True)
b += txt("cmp-l-b", 80, 180, 364, 180,
         "· 用户每次对话都要重复自我介绍\n\n"
         "· 系统只能基于当次上下文应答\n\n"
         "· 历史形成的学习画像无法被主动调用\n\n"
         "· “个性化”停留在提示词层面，\n  无法真正贴合个人",
         11, PAPER, False, "left", FONT, 1.6)
b += rect("cmp-r", 496, 132, 404, 240, "#12294A", GOLD_DEEP, 1)
b += txt("cmp-r-t", 516, 146, 360, 26, "本作品：钩子自动识别并调库", 15, GOLD, True)
b += txt("cmp-r-b", 516, 180, 364, 180,
         "· 系统预设标签体系 + 关键词钩子\n\n"
         "· 识别对话中的关键信息（科目、目标、\n  薄弱点等）\n\n"
         "· 命中钩子即自动调取对应知识库内容\n\n"
         "· 用户画像随交互持续累积，\n  无需重复自我介绍即可获得贴合应答",
         11, PAPER, False, "left", FONT, 1.6)
b += rect("sec2", 60, 392, 844, 108, NAVY_CARD, NAVY_LINE, 1)
b += txt("sec2-t", 80, 404, 400, 24, "技术关键与实现阶段", 14, GOLD, True)
b += txt("sec2-b", 80, 432, 800, 58,
         "钩子召回准确率直接决定个性化体验的成败：召回不足则画像调不出来，误召回则引入无关内容。\n"
         "设计方案：系统预设标签体系 + 关键词钩子匹配 + 命中后的知识库定向检索。"
         "实现阶段：方案与接口设计已完成，编码在院赛阶段启动。",
         11, PAPER, False, "left", FONT, 1.6)
P.append(("13-innovation2.page", page(b, NAVY)))

# =============================================================== 14 创新3 联网协同
b = header("技术创新 · 03", "知识库 × 联网搜索协同：规避知识闭环", 14)
b += txt("p-t", 60, 134, 500, 26, "纯私有知识库的风险", 16, RED, True)
b += rect("p-bg", 60, 168, 400, 130, NAVY_CARD, NAVY_LINE, 1)
b += txt("p-b", 78, 182, 364, 106,
         "若完全关闭联网搜索，知识库将形成闭环：\n"
         "库内的错误信息被反复检索、反复生成，\n"
         "在闭环中不断循环放大；同时无法覆盖\n"
         "超出库范围的时效性信息。",
         11, PAPER, False, "left", FONT, 1.6)
b += txt("q-t", 500, 134, 404, 26, "本作品分级协同策略", 16, GOLD, True)
b += rect("q-bg", 500, 168, 400, 130, "#12294A", GOLD_DEEP, 1)
b += txt("q-b", 518, 182, 364, 106,
         "① 私有优先：先检索用户专属知识库\n"
         "② 公开补齐：库内无覆盖时调用联网搜索\n"
         "③ 来源标注：区分私有知识与公开信息\n"
         "④ 不因“纯净”而关闭联网——硬性原则",
         11, PAPER, False, "left", FONT, 1.6)
b += txt("e-t", 60, 318, 400, 24, "协同带来的改进", 15, GOLD, True)
eff = [("知识覆盖面", "显著扩大", "私有资料与公开信息\n互补"),
       ("时效性", "不再受限", "可获取库外\n最新信息"),
       ("错误传播", "有效抑制", "打破库内闭环\n循环")]
for i, (k, v, d) in enumerate(eff):
    x = 60 + i * 288
    b += rect("e-bg%d" % i, x, 352, 264, 148, NAVY_CARD, NAVY_LINE, 1)
    b += rect("e-bar%d" % i, x, 352, 3, 148, GOLD)
    b += txt("e-k%d" % i, x + 16, 366, 230, 22, k, 13, PAPER, True)
    b += txt("e-v%d" % i, x + 16, 396, 230, 30, v, 18, GOLD, True)
    b += txt("e-d%d" % i, x + 16, 436, 230, 50, d, 10, PAPER_DIM, False,
             "left", FONT, 1.5)
b += txt("note", 60, 510, 844, 20,
         "决策依据：团队会议明确「完全关闭联网搜索会造成知识库闭环，错误信息不断循环，必须保留联网搜索能力」。"
         "实现阶段：协同路由方案已定，编码在院赛阶段启动。",
         9, CYAN_DIM)
P.append(("14-innovation3.page", page(b, NAVY)))

# =============================================================== 15 创新4+5
b = header("技术创新 · 04 / 05", "学习科学驱动的产品设计 与 强数据隔离", 15)
b += rect("t4-bg", 60, 132, 404, 250, NAVY_CARD, NAVY_LINE, 1)
b += rect("t4-bar", 60, 132, 3, 250, GOLD)
b += txt("t4-t", 80, 146, 360, 26, "04 学习科学驱动的产品设计", 16, GOLD, True)
b += txt("t4-b", 80, 180, 366, 190,
         "每日总结　费曼复盘思想，定时汇总学习内容生成\n"
         "　　　　　思维导图式打卡图，带产品标签、可分享\n\n"
         "每日委托　填写当日产出、原理与问题 → Agent 反馈\n"
         "　　　　　→ 生成打卡成果图，提升成就感\n\n"
         "学习路径　知识点拆分为关卡，含总进度条\n"
         "　　　　　参考保卫萝卜交互，优先面向初学者\n\n"
         "反馈体系　主动舍弃传统奖惩，改用新手引导与正向反馈",
         10, PAPER, False, "left", FONT, 1.45)
b += rect("t5-bg", 496, 132, 404, 250, NAVY_CARD, NAVY_LINE, 1)
b += rect("t5-bar", 496, 132, 3, 250, CYAN)
b += txt("t5-t", 516, 146, 360, 26, "05 强数据隔离的多用户架构", 16, CYAN, True)
b += txt("t5-b", 516, 180, 366, 190,
         "传统做法　鉴权通过但无权访问时返回 403\n"
         "　　　　　明确告知「资源存在，只是你无权访问」\n"
         "　　　　　攻击者可遍历 id 逐步推断系统资源\n\n"
         "本作品　　归属校验失败一律返回 404 Not Found\n"
         "　　　　　不透露资源是否存在，从根源阻断枚举\n"
         "　　　　　kb_owners 归属表 + md5(owner|name) 生成 kb_id\n"
         "　　　　　每个业务端点强制挂载鉴权依赖，默认拒绝\n\n"
         "适配价值　满足校园多账号共用场景的数据主权要求",
         10, PAPER, False, "left", FONT, 1.45)
b += rect("bot", 60, 400, 844, 100, "#12294A", GOLD_DEEP, 1)
b += txt("bot-t", 80, 412, 400, 24, "设计依据与实现阶段", 14, GOLD, True)
b += txt("bot-b", 80, 440, 800, 52,
         "学习科学依据：费曼复盘（以教代学）与检索练习 / 测试效应（Roediger & Karpicke, 2006）表明"
         "「以测促学」优于重复阅读；传统奖惩易产生负面效果，故主动舍弃。\n"
         "实现阶段：交互与视觉方案已定，功能编码与定时任务在院赛阶段启动。",
         10, PAPER, False, "left", FONT, 1.5)
P.append(("15-innovation4.page", page(b, NAVY)))

# =============================================================== 15b 实现阶段说明
# 诚实标注：哪些已落地、哪些院赛实现。文件名 15b 表示排在 15 与 16 之间。
b = header("技术创新 · 实现阶段", "如实标注：本阶段已完成 与 院赛阶段实现", None)
b += rect("sg-l-bg", 60, 126, 404, 300, NAVY_CARD, GOLD, 1)
b += rect("sg-l-bar", 60, 126, 3, 300, GOLD)
b += txt("sg-l-t", 80, 140, 360, 26, "本阶段（申报期）已完成", 16, GOLD, True)
b += txt("sg-l-b", 80, 174, 366, 244,
         "✓ 分层架构工程脚手架\n"
         "　　FastAPI 分层 · 集中配置校验\n\n"
         "✓ 多用户隔离鉴权体系\n"
         "　　默认拒绝 · 归属校验返回 404 防枚举\n\n"
         "✓ RAG 知识库检索与三段式重排\n"
         "　　扩召回 → 离群过滤 → MMR 去重\n\n"
         "✓ 全链路 SSE 流式应答\n\n"
         "✓ Agent 五工具编排（含联网搜索）\n\n"
         "✓ 前端界面与校园工具集原型",
         10, PAPER, False, "left", FONT, 1.5)
b += rect("sg-r-bg", 496, 126, 404, 300, NAVY_CARD, CYAN, 1)
b += rect("sg-r-bar", 496, 126, 3, 300, CYAN)
b += txt("sg-r-t", 516, 140, 360, 26, "院赛阶段实现", 16, CYAN, True)
b += txt("sg-r-b", 516, 174, 366, 244,
         "○ 对话信息蒸馏与专属知识库\n"
         "　　文本化存储 + 结构化标签 + 全文检索\n\n"
         "○ 标签钩子\n"
         "　　关键信息识别与知识库定向调取\n\n"
         "○ 每日总结与每日委托\n"
         "　　思维导图打卡图 · 定时任务调度\n\n"
         "○ 学习路径可视化\n"
         "　　关卡式路径 + 总进度条\n\n"
         "○ 知识库与联网搜索协同路由",
         10, PAPER, False, "left", FONT, 1.5)
b += rect("sg-bot", 60, 440, 844, 78, "#12294A", GOLD_DEEP, 1)
b += txt("sg-bot-t", 80, 450, 400, 22, "交付边界（项目纪律）", 13, GOLD, True)
b += txt("sg-bot-b", 80, 476, 800, 36,
         "申报阶段仅交付「接口设计 + 功能需求 + 方案文档 + Mock 演示」，不做复杂业务编码；"
         "右侧能力统一在院赛阶段编码实现。此页为如实标注，便于评审对照。",
         10, PAPER, False, "left", FONT, 1.5)
P.append(("15b-stage.page", page(b, NAVY)))

# =============================================================== 16 已有基础
b = header("已有基础", "不是纸面方案：核心能力已落地并经线上验证", 16)
done = [("已建成", "FastAPI + LangChain v1 + Chroma + SQLite 全链路工程", GOLD),
        ("已实现", "RAG 知识库检索 + 三段式重排（扩召回 / 离群过滤 / MMR）", CYAN),
        ("已实现", "多工具 Agent 编排 · 全链路 SSE 流式应答", GOLD),
        ("已落地", "多用户隔离鉴权体系（404 防枚举）· 移动端响应式", CYAN),
        ("已具备", "设计系统「苞米地笔记本」成形，UI 一致性可控", GOLD),
        ("已通过", "W1/W2/W3 全链路测试 + 7 个离线回归测试", CYAN)]
for i, (k, v, c) in enumerate(done):
    col = i % 2
    row = i // 2
    x = 60 + col * 434
    ty = 134 + row * 78
    b += rect("db-bg%d" % i, x, ty, 410, 66, NAVY_CARD, NAVY_LINE, 1)
    b += rect("db-bar%d" % i, x, ty, 3, 66, c)
    b += txt("db-k%d" % i, x + 16, ty + 10, 120, 22, k, 13, c, True)
    b += txt("db-v%d" % i, x + 16, ty + 34, 380, 26, v, 10, PAPER, False,
             "left", FONT, 1.4)
b += rect("note2", 60, 390, 840, 110, "#12294A", GOLD_DEEP, 1)
b += txt("note2-t", 80, 402, 400, 24,
         "申报阶段的能力边界（如实说明）", 14, GOLD, True)
b += txt("note2-b", 80, 430, 800, 62,
         "按项目纪律，10/20 申报阶段交付“接口设计 + 功能需求 + 方案文档 + Mock 演示”，"
         "知识库蒸馏、定时任务、搜索融合等复杂业务编码在院赛阶段启动。\n"
         "上述“已落地”能力为现有工程资产，作为方案可落地性的背书。",
         10, PAPER, False, "left", FONT, 1.6)
P.append(("16-base.page", page(b, NAVY)))

# =============================================================== 17 性能实测
b = header("已有基础", "性能实测：阿里云 2 核 2G 环境，全程 0 失败", 17)
perf = [("只读接口", "8", "33.7 ms", GOLD),
        ("SSE 问答（端到端）", "3", "1.64 s", CYAN),
        ("文档上传", "3", "2.78 s", GOLD)]
b += txt("ph", 60, 136, 160, 22, "场景", 12, PAPER_DIM, True)
b += txt("ph2", 300, 136, 120, 22, "并发数", 12, PAPER_DIM, True, "center")
b += txt("ph3", 440, 136, 160, 22, "P50 延迟", 12, PAPER_DIM, True, "center")
b += txt("ph4", 620, 136, 280, 22, "对比基线", 12, PAPER_DIM, True)
b += hline("ph-l", 60, 164, 840, NAVY_LINE, 1)
maxv = 2.78
for i, (n, c, v, col) in enumerate(perf):
    ty = 180 + i * 78
    val = float(v.split()[0])
    b += rect("pf-bg%d" % i, 60, ty, 840, 62, NAVY_CARD, NAVY_LINE, 1)
    b += txt("pf-n%d" % i, 78, ty + 20, 200, 24, n, 13, PAPER, True)
    b += txt("pf-c%d" % i, 300, ty + 20, 120, 24, c, 14, col, True, "center")
    b += txt("pf-v%d" % i, 440, ty + 18, 160, 26, v, 17, col, True, "center")
    b += rect("pf-bar-bg%d" % i, 620, ty + 26, 262, 12, "#16325A")
    b += rect("pf-bar%d" % i, 620, ty + 26, min(260, max(6, int(262 * val / maxv))), 12, col)
b += rect("pconcl", 60, 424, 840, 88, "#12294A", GOLD_DEEP, 1)
b += txt("pc-t", 80, 436, 400, 24, "结论", 14, GOLD, True)
b += txt("pc-b", 80, 464, 800, 40,
         "并发从 1 提升到 3，问答延迟基本持平，说明异步化改造有效、服务无排队；"
         "上传延迟接近线性增长，瓶颈定位在外部向量化接口，后续可优化。",
         11, PAPER, False, "left", FONT, 1.6)
P.append(("17-perf.page", page(b, NAVY)))

# =============================================================== 18 竞品对比
b = header("已有基础", "与同类方案对比：差异化定位", 18)
b += txt("h1", 60, 136, 200, 40, "对比维度", 11, PAPER_DIM, True)
b += txt("h2", 268, 132, 150, 44, "通用对话工具\n(ChatGPT 等)", 10, PAPER_DIM,
         True, "center", FONT, 1.35)
b += txt("h3", 426, 132, 150, 44, "通用 RAG 平台\n(Dify / n8n)", 10, PAPER_DIM,
         True, "center", FONT, 1.35)
b += txt("h4", 584, 132, 150, 44, "校园 AI 助手\n(连小理等)", 10, PAPER_DIM,
         True, "center", FONT, 1.35)
b += txt("h5", 742, 132, 150, 44, "本作品\n知穗", 11, GOLD, True, "center",
         FONT, 1.35)
b += hline("h-l", 60, 184, 840, NAVY_LINE, 1)
rows = [("长期个性化记忆", "—", "—", "部分", "✓ 蒸馏沉淀"),
        ("免重复自我介绍", "—", "—", "—", "✓ 标签钩子"),
        ("知识库 × 联网协同", "—", "部分", "—", "✓ 分级路由"),
        ("学习科学驱动设计", "—", "—", "—", "✓ 每日复盘"),
        ("可溯源回答", "—", "部分", "部分", "✓ 角标溯源"),
        ("私有知识库", "—", "✓", "部分", "✓ 可私有部署"),
        ("数据强隔离", "—", "部分", "—", "✓ 404 防枚举"),
        ("开源可自托管", "—", "✓", "—", "✓ MIT")]
for i, (k, a, bb, c, d) in enumerate(rows):
    ty = 194 + i * 36
    bg = "#12294A" if i % 2 == 0 else NAVY_CARD
    b += rect("cp-bg%d" % i, 60, ty, 840, 32, bg)
    b += txt("cp-k%d" % i, 76, ty + 7, 190, 20, k, 11, PAPER, True)
    for j, val in enumerate([a, bb, c]):
        col = PAPER_DIM if val == "—" else PAPER
        b += txt("cp-%d-%d" % (i, j), 268 + j * 158, ty + 7, 150, 20, val, 10,
                 col, False, "center")
    b += txt("cp-d%d" % i, 742, ty + 7, 150, 20, d, 10, GOLD, True, "center")
b += txt("cp-note", 60, 488, 840, 34,
         "“—”表示该能力非产品核心定位或需额外配置；“部分”表示有限支持。"
         "对比基于各产品公开文档与功能说明（2026 年 10 月）。前四项为本次产品定位调整后的核心差异化维度。",
         9, CYAN_DIM, False, "left", FONT, 1.4)
P.append(("18-compare.page", page(b, NAVY)))

# =============================================================== 19 应用场景
b = header("验证与推广", "应用场景：覆盖备考与求职两大主线", 19)
scenes2 = [("课程学习", ["上传课件、教材与往年题",
                     "建立私有知识库，随时答疑",
                     "回答带原文角标，便于核对"], GOLD),
           ("考研备考", ["按阶段规划复习进度",
                      "错题集与真题精析入库",
                      "知识点梳理与自测生成"], CYAN),
           ("论文写作", ["参考文献规范引用",
                      "摘要与引言结构辅助",
                      "文献综述素材整理"], CYAN),
           ("求职准备", ["简历优化与 JD 分析",
                      "模拟面试与反馈",
                      "求职信息聚合"], GOLD)]
for i, (t, ls, c) in enumerate(scenes2):
    x = 60 + i * 216
    b += bullet_card("sc%d" % i, x, 132, 196, 216, t, ls, c, 14, 10)
b += rect("val", 60, 372, 840, 128, "#12294A", GOLD_DEEP, 1)
b += txt("val-t", 80, 384, 400, 24, "底层逻辑复用", 14, GOLD, True)
b += txt("val-b", 80, 412, 800, 80,
         "四大场景共享同一套底层能力：知识库检索 + 标签钩子 + Agent 编排 + 每日委托与总结。"
         "备考与求职的业务差异主要体现在模板与提示词层，无需重复建设技术底座。\n"
         "产品原则：保留联网搜索以规避知识闭环，主动舍弃传统奖惩体系，"
         "以新手引导与正向反馈替代。",
         11, PAPER, False, "left", FONT, 1.6)
P.append(("19-scenario.page", page(b, NAVY)))

# =============================================================== 20 推广路径
b = header("验证与推广", "推广路径：以校园实训与内容分发为主", 20)
b += txt("ch-t", 60, 132, 400, 26, "获客渠道（问卷实测）", 16, GOLD, True)
chans = [("同学 / 朋友推荐", 39, 69.64), ("小红书", 25, 44.64),
         ("老师 / 课程推荐", 25, 44.64), ("B 站", 21, 37.50),
         ("抖音 / 快手", 20, 35.71), ("微信朋友圈 / 公众号", 19, 33.93)]
for i, (n, v, pct) in enumerate(chans):
    ty = 172 + i * 38
    b += txt("ch-n%d" % i, 60, ty, 150, 20, n, 11, PAPER)
    b += rect("ch-bg%d" % i, 216, ty + 4, 240, 12, "#16325A")
    b += rect("ch-fg%d" % i, 216, ty + 4, int(240 * pct / 69.64), 12,
              GOLD if i == 0 else CYAN)
    b += txt("ch-v%d" % i, 464, ty, 80, 20, "%.2f%%" % pct, 10, PAPER_DIM,
             False, "right")
b += txt("ch-note", 60, 410, 484, 60,
         "多选题，样本 56 份。口碑推荐占比最高，说明校园内社交传播是主渠道，"
         "与“实训营 + 校园大赛”的推广设计一致。",
         10, PAPER_DIM, False, "left", FONT, 1.5)
b += txt("pl-t", 570, 132, 340, 26, "三阶段推广计划", 16, GOLD, True)
plans = [("第一阶段", "院赛 + 种子用户", "验证核心链路，收集真实反馈", GOLD),
         ("第二阶段", "校园实训与赛事", "办智能体实训营、校园大赛，形成作品生态", CYAN),
         ("第三阶段", "内容平台分发", "借短视频与社区分享扩大触达，沉淀公开指标", CYAN)]
for i, (k, t, d, c) in enumerate(plans):
    ty = 172 + i * 100
    b += rect("pl-bg%d" % i, 570, ty, 334, 88, NAVY_CARD, NAVY_LINE, 1)
    b += rect("pl-bar%d" % i, 570, ty, 3, 88, c)
    b += txt("pl-k%d" % i, 586, ty + 10, 120, 20, k, 11, c, True)
    b += txt("pl-t%d" % i, 586, ty + 32, 300, 22, t, 13, PAPER, True)
    b += txt("pl-d%d" % i, 586, ty + 56, 306, 26, d, 10, PAPER_DIM, False,
             "left", FONT, 1.4)
P.append(("20-promotion.page", page(b, NAVY)))

# =============================================================== 21 经济与社会效益
b = header("验证与推广", "应用价值与经济效益", 21)
b += bullet_card("v1", 60, 132, 404, 214, "社会价值",
    ["降低教师与助教的重复答疑成本，",
     "把优质资料沉淀为可复用的校本资产",
     "",
     "以“辅助学习而非替代思考”为导向，",
     "培养学生的提问能力与批判性核查习惯",
     "",
     "开源可私有部署，资料不出校、不出个人，",
     "契合高校数据主权与合规要求"], GOLD, 16, 11)
b += bullet_card("v2", 496, 132, 404, 214, "经济效益",
    ["服务规模：全国普通高校在校生超 4700 万人",
     "（教育部公开数据）",
     "",
     "运营模式：校园授权或轻量 SaaS",
     "计费口径：按问答次数与向量化配额计量",
     "",
     "成本结构：边际成本低、可规模化，",
     "支持本地模型以进一步降低外部依赖"], CYAN, 16, 11)
b += rect("eco", 60, 368, 840, 132, "#12294A", GOLD_DEEP, 1)
b += txt("eco-t", 80, 380, 400, 24, "转化路径", 14, GOLD, True)
b += txt("eco-b", 80, 408, 800, 84,
         "开源发布（GitHub）→ 校内试点（院系知识库对接）→ 校园授权 / 轻量 SaaS。\n"
         "可展示形式：磁盘（代码仓库）· 图片（界面截图与架构图）· 现场演示（答辩现场）。\n"
         "技术转让方式：暂不转让；专利申报：暂无。",
         11, PAPER, False, "left", FONT, 1.6)
P.append(("21-value.page", page(b, NAVY)))

# =============================================================== 22 里程碑
b = header("规划与团队", "里程碑规划：从院赛到省赛", 22)
miles = [("M0", "申报期", "2026-10-20", "接口设计、功能需求、方案文档、Mock 演示", GOLD),
         ("M1", "院赛", "2026-11", "真实编码、前后端联调、路演答辩", CYAN),
         ("M2", "大众推广", "院赛后", "收集公开反馈与使用数据指标", GOLD),
         ("M3", "校赛", "2026-12 底", "院赛通过 + 反馈达标方可晋级", CYAN),
         ("M4", "立项后", "校赛后", "完整功能开发、UI 迭代、团队扩员", GOLD),
         ("M5", "省赛", "2027-03 起", "校赛择优晋级", CYAN)]
b += hline("ml-line", 100, 168, 700, NAVY_LINE, 2)
for i, (k, t, d, desc, c) in enumerate(miles):
    x = 84 + i * 132
    b += rect("ml-dot%d" % i, x + 30, 160, 16, 16, c)
    b += txt("ml-k%d" % i, x, 190, 76, 22, k, 13, c, True, "center")
    b += txt("ml-t%d" % i, x - 10, 214, 96, 22, t, 12, PAPER, True, "center")
    b += txt("ml-d%d" % i, x - 10, 238, 96, 20, d, 9, PAPER_DIM, False,
             "center")
    b += txt("ml-x%d" % i, x - 22, 266, 120, 66, desc, 9, PAPER_DIM, False,
             "center", FONT, 1.45)
b += rect("now", 60, 356, 840, 144, "#12294A", GOLD_DEEP, 1)
b += txt("now-t", 80, 368, 400, 24, "当前所处位置", 14, GOLD, True)
b += txt("now-b", 80, 396, 800, 96,
         "已进入 M0 申报期收尾：申报书已定稿，接口契约文档 v0.1 已发团队评审，"
         "Mock 演示链路已组装完成。\n\n"
         "下一节点：院赛评审（各单位须于 11 月 6 日前完成评审并报送），"
         "院赛通过后进入真实编码与联调阶段。",
         11, PAPER, False, "left", FONT, 1.6)
P.append(("22-milestone.page", page(b, NAVY)))

# =============================================================== 23 团队分工
b = header("规划与团队", "团队分工与交付边界", 23)
team = [("冯靖豪", "申请人 / 总负责",
         ["全部 UI 设计 + 前端骨架", "接口对接统稿（T5）",
          "后端脚手架 + 数据模型", "申报书统稿 + 路演答辩"], GOLD),
        ("林宏亮", "AI 板块",
         ["知识库采集 / 存储 / 检索", "大模型调用封装",
          "联网搜索协同接口", "蒸馏与标签钩子"], CYAN),
        ("刘佳林", "业务与编排",
         ["Agent 编排与工具调用", "每日委托 / 每日总结",
          "定时任务接口设计", "部署与运维"], CYAN)]
for i, (n, role, ls, c) in enumerate(team):
    x = 60 + i * 288
    b += rect("tm-bg%d" % i, x, 132, 264, 226, NAVY_CARD, NAVY_LINE, 1)
    b += rect("tm-bar%d" % i, x, 132, 3, 226, c)
    b += txt("tm-n%d" % i, x + 18, 146, 230, 26, n, 17, c, True)
    b += txt("tm-r%d" % i, x + 18, 176, 230, 22, role, 11, PAPER_DIM)
    b += hline("tm-l%d" % i, x + 18, 202, 228, NAVY_LINE, 1)
    for j, l in enumerate(ls):
        b += txt("tm-%d-%d" % (i, j), x + 18, 214 + j * 32, 230, 28, "· " + l,
                 10, PAPER, False, "left", FONT, 1.4)
b += txt("adv", 60, 374, 840, 22,
         "指导老师：李欣 · 广东石油化工学院计算机学院", 12, GOLD, True)
b += rect("bd", 60, 406, 840, 94, "#12294A", GOLD_DEEP, 1)
b += txt("bd-t", 80, 418, 400, 24, "交付边界（项目纪律）", 14, GOLD, True)
b += txt("bd-b", 80, 446, 800, 48,
         "申报阶段只交付“方案文档 + Mock 演示”，复杂业务编码一律延后院赛后启动，不盲目赶工。"
         "唯一交接面为接口契约（规划统一至 /api/v1），改字段先改文档版本号，不口头约定。",
         10, PAPER, False, "left", FONT, 1.5)
P.append(("23-team.page", page(b, NAVY)))

# =============================================================== 24 风险与对策
b = header("规划与团队", "风险识别与应对策略", 24)
risks = [("信息蒸馏质量", "高", "用户零散对话转结构化知识噪声大", "钩子 + 样例标注；置信度阈值；输出可纠偏 UI", RED),
         ("知识库 × 联网协同", "高", "调用时机与优先级难定，可能冲突冗余", "分级路由（私有优先 / 公开补齐）；回答溯源标注", RED),
         ("知识库输出质量", "高", "蒸馏内容直接给用户可能出错", "输出前摘要校验 + 引用来源标注 + 用户可编辑", GOLD),
         ("定时任务稳定性", "中", "每日总结与委托可能漏跑或重复", "幂等设计 + 重试机制 + 失败监控告警", GOLD),
         ("新用户留存", "中", "无强激励时早期流失，是核心挑战", "新手引导 + 持续正向反馈 + 个性化初体验", GOLD),
         ("边界失控", "中", "赶工导致功能盲目扩张", "严守阶段性边界；先文档后编码；Mock 解耦业务", CYAN)]
b += txt("rh1", 60, 132, 190, 20, "风险项", 11, PAPER_DIM, True)
b += txt("rh2", 262, 132, 70, 20, "等级", 11, PAPER_DIM, True, "center")
b += txt("rh3", 348, 132, 250, 20, "说明", 11, PAPER_DIM, True)
b += txt("rh4", 614, 132, 286, 20, "应对措施", 11, PAPER_DIM, True)
b += hline("rh-l", 60, 158, 840, NAVY_LINE, 1)
for i, (k, lv, d, m, c) in enumerate(risks):
    ty = 168 + i * 56
    bg = "#12294A" if i % 2 == 0 else NAVY_CARD
    b += rect("rk-bg%d" % i, 60, ty, 840, 50, bg)
    b += txt("rk-k%d" % i, 76, ty + 15, 180, 20, k, 11, PAPER, True)
    b += rect("rk-lv-bg%d" % i, 268, ty + 15, 44, 20, c, c, 1)
    b += txt("rk-lv%d" % i, 268, ty + 17, 44, 18, lv, 10, NAVY_DEEP, True,
             "center")
    b += txt("rk-d%d" % i, 348, ty + 15, 256, 20, d, 9, PAPER_DIM, False,
             "left", FONT, 1.35)
    b += txt("rk-m%d" % i, 614, ty + 15, 280, 20, m, 9, PAPER, False, "left",
             FONT, 1.35)
P.append(("24-risk.page", page(b, NAVY)))

# =============================================================== 25 验证与指标
b = header("规划与团队", "验证方式与验收指标", 25)
b += bullet_card("vf1", 60, 132, 404, 200, "已完成的验证",
    ["· 需求验证：56 份有效问卷",
     "· 功能验证：W1/W2/W3 全链路测试",
     "· 回归验证：7 个离线回归用例",
     "· 性能验证：3 类接口并发压测",
     "· 工程验证：CI + 类型注解 + 文档"], GOLD, 16, 11)
b += bullet_card("vf2", 496, 132, 404, 200, "院赛后待验证",
    ["· 真实用户使用数据与留存曲线",
     "· 检索质量人工评估（引用相关性）",
     "· 知识库蒸馏准确率抽样评估",
     "· 定时任务稳定性监控指标",
     "· 公开反馈指标（仓库收藏数等）"], CYAN, 16, 11)
b += rect("kv", 60, 354, 840, 146, "#12294A", GOLD_DEEP, 1)
b += txt("kv-t", 80, 366, 400, 24, "关键量化指标一览", 14, GOLD, True)
metrics = [("检索 top_k", "3"), ("MMR λ", "0.65"),
           ("相似度下限", "0.25"), ("候选池倍数", "3×"),
           ("只读接口 P50", "33.7 ms"), ("SSE 问答 P50", "1.64 s"),
           ("文档上传 P50", "2.78 s"), ("问卷样本", "56 份")]
for i, (k, v) in enumerate(metrics):
    x = 80 + (i % 4) * 202
    ty = 398 + (i // 4) * 50
    b += txt("kv-k%d" % i, x, ty, 190, 18, k, 10, PAPER_DIM)
    b += txt("kv-v%d" % i, x, ty + 18, 190, 22, v, 15, GOLD, True)
P.append(("25-verify.page", page(b, NAVY)))

# =============================================================== 26 结尾
b = rect("end-bg", 0, 0, 960, 540, NAVY_DEEP)
b += rect("end-band", 0, 200, 960, 160, "#0E2744")
b += rect("end-l", 60, 200, 5, 64, GOLD)
b += txt("end-t", 84, 196, 700, 70, "以辅助学习而非替代思考", 40, PAPER, True,
         "left", FONT_TITLE)
b += txt("end-s", 84, 274, 700, 32,
         "让学生拥有一个真正懂自己资料、答案可核对的 AI 学习助手", 16, GOLD)
b += hline("end-line", 84, 330, 560, CYAN_DIM, 2)
b += txt("end-i", 84, 344, 700, 30,
         "知穗——面向大学生个性化AI学习助手", 18, PAPER, True)
b += txt("end-m", 84, 390, 700, 60,
         "冯靖豪 · 林宏亮 · 刘佳林    指导老师：李欣\n"
         "广东石油化工学院 计算机学院    科技发明制作 B 类 · 信息技术",
         11, PAPER_DIM, False, "left", FONT, 1.6)
b += txt("end-thx", 84, 470, 700, 30, "恳请各位专家批评指正", 14, CYAN_DIM)
P.append(("26-end.page", page(b, NAVY_DEEP)))

print('pages planned:', len(P))
for fn, content in P:
    with io.open(os.path.join(PAGES, fn), 'w', encoding='utf-8') as f:
        f.write(content)
print('written to', PAGES)
