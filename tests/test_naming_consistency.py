"""界面用词一致性：一个东西只能有一个名字（2026-11-14 第四批，11-15 第 14 批改写）。

两件事，都来自「同一件事在两个地方各叫一个名」：

1. **资料库 / 公开知识库**。侧栏曾叫「知识库」、页面标题叫「我的资料库」、
   右栏面板叫「资料库」、空状态叫「还没有资料库」。用户点侧栏进来会以为这是另一处
   —— 事实上还有一个 /library（公开广场），三个入口两个词，谁在哪儿说不清。

   ★ 2026-11-15 第 14 批用户定名，改成分工而不是统一：
     · **资料库** = 我自己的库（/knowledge，「我的资料库」）
     · **公开知识库** = /library 那个广场（别人也能检索的库）
     · **官方知识库** = 广场里站点自建的那一区（2026-11-15 第 24 批定名）
   三个词指三个东西，不再是同义反复。除这三个整词外，界面上一律不许出现
   「知识库」—— 后端命名 kb_* 也不用改。
   改动原因写在 `_ALLOWED_LIBRARY` 上面。

2. **番茄钟同步条**。进页面、网络恢复、页面重新可见都会自动同步，所以
   「已同步 · 刚刚同步 · 云端共 N 条记录」+「立即同步」按钮是在复述一件本来就
   该成立的事。删掉；但「仅本机」「未同步」留着 —— 那两句说的是后果
   （换设备看不看得到、记录会不会丢），不是状态。

3. **那个 AI 的正式称呼只有一个：「AI 助手」**（2026-11-15 第 25 批用户定名）。
   但**只管对外**：站内原有的「让助手帮我写」「学习助手」等说法照旧，
   用户明确要求"不要全站替换"。见 ④ 那一节。

★ 每条都按"故意改坏一次必须变红"验证过（output/_counterproof_batch4.py）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"

NAV = SRC / "lib" / "nav.ts"
KNOWLEDGE = SRC / "app" / "(console)" / "knowledge" / "page.tsx"
# ★ 2026-11-23：广场主体搬进 components/library/PlazaPane（资料库页的
#   「公开资料库」tab 与 /library 共用一份），页面文件只剩一个薄壳。
#   断言要读真正含文案的那份。
LIBRARY = SRC / "components" / "library" / "PlazaPane.tsx"
SYNC_BAR = SRC / "campus" / "tools" / "pomodoro" / "SyncBar.tsx"
POMODORO = SRC / "campus" / "tools" / "pomodoro" / "PomodoroTool.tsx"
FOCUS_SYNC = SRC / "campus" / "logic" / "focusSync.ts"

# 注释：块注释（含 JSX 的 {/* ... */}）与整行 //。
# ★ 必须先剥：注释里会引用旧文案做解释（"原来写的是「知识库」"），
#   不剥会把说明性注释当成界面上真实渲染的字。
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"^[ \t]*//.*$", re.M)


def _code(path: Path) -> str:
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", path.read_text(encoding="utf-8")))


# ==================== ① 界面不再出现「知识库」 ====================

# 允许保留的例外，逐条写明理由 —— 不加"仅此一处"这种模糊豁免。
#  · lib/chatStatus.ts 的 `re:` 行：那些正则匹配的是**后端 status 帧的原文**，
#    后端还在发「正在检索知识库(bk_xxx)」。改了正则就一条都匹配不上，
#    界面上那行状态会永远停在上一条。要改先改后端。
#  · components/chat/legacyBlocks.ts 的 SRC_NAME：同样匹配**后端历史消息里的
#    标签原文**（core/data_boundary.py 打的是【资料开始·知识库】）。改了字面量
#    旧消息里那些块就再也剥不掉，正是 c93e283 要修的那个 bug 复活。
#    注意 SOURCE_LABEL 的显示文案已按纪律改成「资料库」，这里只豁免正则。
_ALLOWED_LINES = {
    "lib/chatStatus.ts": re.compile(r"\bre:\s*/"),
    "components/chat/legacyBlocks.ts": re.compile(r"^const SRC_NAME\s*="),
}

# 允许的整体词：**「公开知识库」与「官方知识库」两个，只有这两个**。
#
# 2026-11-15 第 14 批用户定名：广场（/library）叫「公开知识库」，我自己的库叫
# 「资料库」。上一批我按旧纪律把它落成「公开资料库」，用户当场要求改回，
# 所以纪律本身要跟着改 —— 不是"网开一面"，而是"两个词指两个东西"。
#
# 2026-11-15 第 24 批又加了「官方知识库」：广场里官方那一区原叫「官方课程库」，
# 用户看第 3 期文案后说「那个不叫课程库，应该叫官方知识库」。三个词的分工是：
#   · 资料库       = 我自己的库（/knowledge）
#   · 公开知识库   = /library 那个广场
#   · 官方知识库   = 广场里由站点自建的那一区（眉题 / 后端 source / 详情页副标题同词）
# 「个人知识库」仍然没有豁免 —— 那个仍然是把「资料库」叫错。
#
# 实现上按**词**豁免而不是按行：检查前先把这几个整词从行里摘掉，
# 剩下的文本里再出现「知识库」照样红。这样「知识库管理」「我的知识库」
# 这类漏网写法一个都跑不掉（按行豁免会把整行一起放过，那些就跑掉了）。
_ALLOWED_TERMS = ("公开知识库", "官方知识库")

# 不扫的只有一处：
# · _backup/  旧版快照，不参与构建
#
# 这里原本还有 `app/forum/` —— 论坛页当时由另一个会话在改，怕两边打架先跳过。
# **2026-11-14 第 19 轮已合并，豁免删除**：留着的话论坛页里的「知识库」永远
# 扫不出来，而"跳过某个目录"这种事一旦写进去就没人再回头看。
_SKIP_PREFIX = ("_backup/",)


def test_ui_never_says_zhishiku():
    offenders: list[str] = []
    scanned = 0

    for path in SRC.rglob("*.ts*"):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith(_SKIP_PREFIX):
            continue
        scanned += 1

        allow = _ALLOWED_LINES.get(rel)
        for lineno, line in enumerate(_code(path).splitlines(), 1):
            # 按词豁免：把允许的整词逐个摘掉后再看还剩不剩「知识库」
            rest = line
            for term in _ALLOWED_TERMS:
                rest = rest.replace(term, "")
            if "知识库" not in rest:
                continue
            if allow is not None and allow.search(line):
                continue
            offenders.append(f"{rel}:{lineno}  {line.strip()[:70]}")

    # ★ 护栏的护栏：路径一变、glob 失效，上面那个循环会一个文件都扫不到，
    #   测试反而变绿（第二批就踩过：13 个页搬进 (console)/ 后 rglob 一层都没扫到，
    #   却因为有 6 个顶层页一直是绿的）。所以必须同时断言"扫到了多少个"。
    assert scanned >= 150, f"只扫到 {scanned} 个文件，扫描范围可能坏了"

    assert not offenders, (
        "界面上还在叫「知识库」（统一用「资料库」；后端命名 kb_* 不用改）：\n  "
        + "\n  ".join(offenders)
    )


# ==================== ② 侧栏与页面标题同源 ====================

def test_nav_label_and_page_title_share_one_name():
    nav = _code(NAV)
    m = re.search(r'\{\s*href:\s*"/knowledge"[^}]*label:\s*"([^"]+)"', nav)
    assert m is not None, "nav.ts 里找不到 /knowledge 那条，正则失效了"
    assert m.group(1) == "资料库", f"侧栏写的是「{m.group(1)}」"

    page = _code(KNOWLEDGE)
    m2 = re.search(r'<PageShell\b[^>]*?title="([^"]+)"', page, re.S)
    assert m2 is not None, "knowledge 页的 PageShell 没有 title"
    assert "资料库" in m2.group(1), f"页面标题写的是「{m2.group(1)}」"


def test_public_library_uses_the_name_the_user_chose():
    """广场的 h1 必须与左栏按钮、首屏卡片同名（2026-11-15 第 14 批定名）。

    历史：11-14 统一成「公开资料库」（当时的纪律是"全站只许叫资料库"）；
    11-15 用户看截图后要求改成「公开知识库」。现在三处（左栏按钮 / 首屏卡片 /
    广场 h1）必须是同一个词，否则又是"同一个入口两个名字"。
    """
    lib = _code(LIBRARY)
    assert "<h1>公开知识库</h1>" in lib, "公开广场的 h1 没跟着改名（应为「公开知识库」）"

    # ★ 2026-11-23：左栏那颗独立的「公开知识库」按钮已经撤掉 ——
    #   入口统一成「资料库」，公开广场变成资料库页里的一个 tab。
    #   所以同源判据改成：左栏不再单独出现这个词，资料库页有「公开资料库」tab。
    sidebar = _code(SRC / "components" / "Sidebar.tsx")
    assert "公开知识库" not in sidebar, (
        "左栏又长出了一颗独立的「公开知识库」按钮 —— 入口应当统一到「资料库」"
    )
    kpage = _code(SRC / "app" / "(console)" / "knowledge" / "page.tsx")
    assert "公开资料库" in kpage, "资料库页没有「公开资料库」tab，广场就没入口了"
    cards = _code(SRC / "components" / "chat" / "constants.tsx")
    assert re.search(r'href:\s*"/library",\s*label:\s*"公开知识库"', cards), (
        "首屏那格「公开知识库」的标签没跟着改 —— 同一屏两个名字"
    )


# ==================== ②b 后端返回的展示串也算界面文案 ====================

# 广场页「官方」那一区：拿它的眉题当**唯一真名**，别处都往它上对。
# ★ 必须锚在 section 上，不能全页找第一个 <span className="eyebrow">：
#   这一页有四个区（官方 / 我分享的 / 我的收藏库 / 同学分享的），
#   按"第一个"找会随排版顺序变化而静默指错区。
_OFFICIAL_SECTION = re.compile(
    r'<section className="lib-sec lib-official">.*?<span className="eyebrow">([^<]+)</span>',
    re.S,
)


def _official_section_name() -> str:
    m = _OFFICIAL_SECTION.search(_code(LIBRARY))
    assert m, "广场页找不到「官方」那一区的眉题（section 的 class 改名了？）"
    return m.group(1).strip()


def test_backend_display_string_matches_ui():
    """`api/kb.py` 收藏接口里的 `source` 是**给用户看的字符串**，不是判定值。

    ★ 这条是补漏：第一轮只扫了 frontend/src，源码里干净了，线上「我的资料库」
      页面上却还挂着「来源官方知识库」—— 那句话是后端拼好返回的。
      判定字段是 `official`（布尔），`source` 只负责显示，所以它可以跟着改。

    ★ 2026-11-15 第 19 批改法：**不再写死"应该是哪个词"**，而是要求后端这个
      字面量与广场眉题**逐字相等**。上一版写死「官方资料库」，于是眉题改成
      「官方课程库」之后测试照样绿 —— 那就是"两个地方各存一份真名"的老毛病，
      只是这次真名藏在测试里。现在真名只有一处（眉题），其余三处（后端 source、
      详情页副标题、收藏行「来源…」）都往上对。
    """
    kb = (ROOT / "api" / "kb.py").read_text(encoding="utf-8")
    m = re.search(r'"source":\s*"([^"]+)"\s*if official', kb)
    assert m, "kb.py 里找不到 source 的官方取值（写法变了？别让这条静默失效）"
    source = m.group(1)
    name = _official_section_name()

    assert source == name, (
        f"后端来源串是「{source}」，广场眉题是「{name}」—— 同一件事两个名字。"
        "收藏卡片会写成「来源{后端那个}」，而它上面那一区写着另一个词。"
    )
    # 真名必须是白名单里的整词。第 19 批这条写的是 `"知识库" not in source`，
    # 因为当时只许「公开知识库」一个整词；第 24 批用户定名「官方知识库」之后，
    # 改判"是不是一个整词"而不是"有没有出现知识库"—— 否则这条与产品决定互相打脸。
    assert name in _ALLOWED_TERMS, (
        f"广场眉题「{name}」不在允许的整词里 {_ALLOWED_TERMS}"
    )

    # 历史上用过的几个词不许以"展示串"的身份回来（当前这个名字除外）
    for old in ("官方资料库", "官方知识库", "官方课程库"):
        if old == name:
            continue
        assert f'"source": "{old}"' not in kb, f"api/kb.py 的展示串退回了「{old}」"

    # 详情页副标题也要同词（它是在另一个文件里手写的，最容易漏）
    detail = _code(SRC / "app" / "library" / "[kbId]" / "page.tsx")
    assert f'"{name} · 在线预览或下载"' in detail, (
        f"库详情页的副标题没跟着叫「{name}」（每人各自手写一遍，就会分叉）"
    )



# ==================== ③ 同步条：成功时不说话 ====================

def test_sync_bar_is_silent_when_synced():
    src = _code(SYNC_BAR)

    # 三种"不需要你做什么"的状态都必须提前返回
    for state in ("synced", "checking", "syncing"):
        assert f'"{state}"' in src, f"SyncBar 没有处理 {state} 状态"
    assert src.count("return null") == 1, "正常态的提前返回不见了"

    # 常显时代的两样东西必须彻底消失
    assert "立即同步" not in src, "「立即同步」按钮还在 —— 同步本来就是自动的"
    assert "lastSyncedAt" not in src, "「N 分钟前同步」的时间戳还在"

    # 但两种后果必须还说：换设备看不看得到 / 记录会不会丢
    assert "仅本机" in src and "未同步" in src, "把「仅本机 / 未同步」也删掉了，用户会以为数据在云端"


def test_pomodoro_no_longer_keeps_sync_timestamp():
    src = _code(POMODORO)
    assert "lastSyncedAt" not in src, "PomodoroTool 还在传 lastSyncedAt"
    assert re.search(r"useState<\{ status: SyncStatus; message: string \}>", src), (
        "同步状态里还留着 at 字段"
    )


def test_sync_error_is_not_raw_exception_text():
    """失败原因不能把 `Failed to fetch` 这种原样透给用户。

    原来 `message: error instanceof Error ? error.message : …` 直接把异常串
    拼进「未同步」那一行 —— 断网时用户看到的是八级英语。
    """
    src = _code(FOCUS_SYNC)
    assert "message: error instanceof Error ? error.message" not in src, (
        "又直接透传原始异常串了"
    )
    assert "syncErrorText" in src, "没有把失败原因翻译成人话"
    assert "instanceof TypeError" in src, "网络层失败（TypeError）没被单独识别"


# ==================== ④ 正式称呼只有一个：AI 助手 ====================

# 2026-11-15 第 25 批用户定名：「AI 助手」是**对外正式称呼**。
#
# ★ 同一批里用户当场纠了一句：「那个说辞不要改变，就是不要全站替换」。
#   所以这一节**不管站内原有的说法** ——
#   「让助手帮我写」「助手的分析」「知穗是个会自己动手的学习助手」全部照旧。
#   那些是产品里已经用惯的词，为了术语整齐去改它们，等于让用过的人
#   重新认一遍按钮，换来的只是"文档看着整齐"。
#
#   这条只管一件窄事：**不许多出一个新名字**。正式称呼只有那一个；
#   以后写对外文案（公告 / 海报 / 朋友圈）用它，别临时发明「AI 助理」
#   「智能助手」，也别写不带空格的「AI助手」。
_ASSISTANT = "AI 助手"

# 别的叫法一个都不许出现。带空格与不带空格是两种写法，所以两种都列 ——
# 只列带空格的，等于放过「AI助手」这个最容易顺手打出来的版本。
#
# ★ 这里**故意不列**「学习助手」「写作助手」这类：
#   前者是新手引导里原本就在用的说法（不是"新发明的名字"），
#   后者只活在 prototypes 的原型稿里。把它们列进来就变成"逼着改现有说辞"，
#   而那正是用户不要的。
_ASSISTANT_ALIASES = (
    "AI助手",
    "AI助理",
    "AI 助理",
    "ai助手",
    "ai 助手",
    "AI小助手",
    "AI 小助手",
    "智能助手",
)


def test_no_new_name_for_the_assistant():
    """不许给那个 AI 再起一个新名字（**原有说法不动**，见上面的注释）。

    ★ 这条刻意不查"裸「助手」"：界面上「让助手帮我写」这类说法一共 5 处，
      正是用户要求保留的。查它们就等于把上一版的做法换个地方重来一遍。
    """
    hits: list[str] = []
    scanned = 0

    for path in SRC.rglob("*.ts*"):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith(_SKIP_PREFIX):
            continue
        scanned += 1
        for lineno, line in enumerate(_code(path).splitlines(), 1):
            for alias in _ASSISTANT_ALIASES:
                if alias in line:
                    hits.append(f"{rel}:{lineno}  {line.strip()[:70]}")
                    break

    # 护栏的护栏：glob 失效时一个文件都扫不到，hits 空着反而变绿
    assert scanned >= 150, f"只扫到 {scanned} 个文件，扫描范围可能坏了"
    assert not hits, (
        f"出现了别的叫法（正式称呼只有「{_ASSISTANT}」）：\n  " + "\n  ".join(hits)
    )


def test_release_notes_call_it_the_formal_name():
    """对外文案里真的用上了这个名字 —— 否则"定了名"就只活在测试里。

    只钉公告：它是每一期写给所有人看的那份，也是对外口径的锚。
    海报与朋友圈是 `docs/宣传图/` 里的静态产物，不在这条的范围（人工比对）。
    """
    src = _code(SRC / "lib" / "announcements.ts")
    assert _ASSISTANT in src, (
        f"最新一期公告里没有出现「{_ASSISTANT}」—— 对外正式称呼定了却没人用"
    )


