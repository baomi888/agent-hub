"""「让布局自己解释」的四条不变量（2026-11-14 第二批）。

第一批是纯删（眉题、复述型说明）。这一批换一种做法：**把文字变成结构**。
文字删干净之后，剩下的信息缺口要靠布局补，否则就是"删完变空"。

四条规则，每条对应一个真实发生过的问题：

1. **指标卡不挂括号**（dashboard）。八张卡里原来有四处括号在解释"这个数跟随筛选吗、
   口径怎么算"，另有第五处直接写在标签里。同一类事实在五个地方各说一遍，卡面被塞满。
   现在：不跟随筛选的卡挂一个「全量」角标（`.stat-scope`），规则在页头说一次。

2. **一页不许有两个同名 h2**（reminders）。整页标题叫「待办」，分区标题也叫「待办」，
   读屏按标题跳转时分不清层级。分区改成「任务」——「任务 / 提醒」是同层的对象名。

3. **小标题不许复述上面的 tab**（reminders）。tab 写着「今天」，主区小标题又写「今天」。
   根因是 `showTaskSub` 把「今天完成」也算进了分段数：只要有一条已完成，单段主区
   就冒出标题。判据改成只看主区自己分了几段。

4. **同一事实只写一处**（tools）。页头与本页某张卡讲同一件事（本机存储 + 登录同步），
   两种措辞。留信息更全的那处（多了"清浏览器会丢"的后果），页头只说价值。

★ 每条都按"故意改坏一次必须变红"验证过（output/_counterproof_batch2.py）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "frontend" / "src" / "app"
CONSOLE = APP / "(console)"

DASH = CONSOLE / "dashboard" / "page.tsx"
REM = CONSOLE / "reminders" / "page.tsx"
TOOLS = CONSOLE / "tools" / "page.tsx"
SETTINGS = CONSOLE / "settings" / "page.tsx"

# 注释：块注释 `/* ... */`（含 JSX 里的 `{/* ... */}`）与整行 `//`。
# ★ 必须先剥注释再查文案：注释里会引用被删掉的句子做解释（"原来这里写的是…"），
#   不剥就会把说明性注释当成页面上真实渲染的字，于是"已经删干净了"的断言永远为假。
#   JSX 注释原来单独写过一条正则，后来发现 JSDoc 同样会把原句抄一遍，就统一按块注释剥了。
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"^[ \t]*//.*$", re.M)
_TAG = re.compile(r"<[^>]+>")


def _code(path: Path) -> str:
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", path.read_text(encoding="utf-8")))


# ==================== ① 指标卡不挂括号 ====================

_STAT_LABEL = re.compile(r'<div className="l">(.*?)</div>', re.S)
# 这五句是第二批从卡片标签里搬走的东西。留在这里当黑名单：
# 它们只要重新出现在标签里，说明"括号解释"又长回来了。
CARD_RULE_BLACKLIST = (
    "不随筛选",
    "不受筛选",
    "当前快照",
    "均值，非 p95",
    "不含未登录",
)


def test_metric_cards_hold_a_label_not_a_rule():
    """指标卡的标签只能是名词，不能带括号解释。

    括号里那类话（口径怎么算、跟不跟随筛选）属于一屏一次的规则，
    应该是页头一句 + 一个角标，不该在八张卡里各挂一句。
    """
    labels = [t for t in (_TAG.sub("", m.group(1)).strip() for m in _STAT_LABEL.finditer(_code(DASH)))]
    assert len(labels) >= 8, f"只读到 {len(labels)} 张指标卡的标签，结构可能变了：{DASH}"

    bad = [
        f"「{t}」"
        for t in labels
        if "（" in t or "(" in t or any(w in t for w in CARD_RULE_BLACKLIST)
    ]
    assert not bad, (
        "指标卡标签里又出现了解释性括号：" + "、".join(bad) + "\n"
        "口径这类一屏说一次的规则放页头；不跟随筛选的卡挂「全量」角标。"
    )


def test_scope_badge_marks_exactly_the_unfiltered_cards():
    """「全量」角标必须恰好标在那两张不跟随筛选的卡上，多一个少一个都是错。"""
    src = _code(DASH)
    badges = re.findall(r'<span className="stat-scope">([^<]*)</span>', src)
    assert len(badges) == 2, (
        f"「全量」角标有 {len(badges)} 个，应为 2 个（注册用户、待处理反馈）。\n"
        "标多了会让跟随筛选的数字看起来也不变，标少了那张卡就没有口径提示。"
    )
    assert set(badges) == {"全量"}, f"角标文案不统一：{badges}"

    # 角标的含义必须由页头说清楚，否则它只是个看不懂的符号。
    assert "标「全量」的是当前快照" in src, (
        "页头没有解释「全量」是什么意思 —— 只挂角标而不给规则，等于换了个看不懂的符号"
    )


def test_no_reference_to_a_file_the_reader_cannot_open():
    """页面文案里不许出现仓库文件名。

    dashboard 页头原来写着「口径见 口径与数据字典.md」——那是仓库里的一个 md，
    线上用户点不开、站内也搜不到，只对开发者有意义。真正的口径应该写成一句话。
    """
    bad = []
    for f in sorted(APP.rglob("page.tsx")):
        for line in _code(f).splitlines():
            if re.search(r"[\w\u4e00-\u9fff]+\.md\b", line) and "口径" in line:
                bad.append(f"{f.relative_to(ROOT).as_posix()}: {line.strip()}")
    assert not bad, (
        "页面文案里引用了仓库文件名（线上点不开）：\n  " + "\n  ".join(bad)
    )


# ==================== ②③ 标题层级 ====================

_H2 = re.compile(r"<h2[^>]*>([^<{]+)</h2>")


def test_no_page_has_two_headings_with_the_same_text():
    """一页之内不许出现两个同名 h2。

    读屏用户靠标题跳转，两个「待办」会让他不知道该跳到哪一个；
    而且第二个看起来像第一个的子节，实际是同级的两个区。
    """
    bad = []
    for f in sorted(APP.rglob("page.tsx")):
        texts = [t.strip() for t in _H2.findall(_code(f))]
        dups = {t for t in texts if texts.count(t) > 1}
        if dups:
            bad.append(f"{f.relative_to(ROOT).as_posix()}: {sorted(dups)}")
    assert not bad, "同一页出现同名 h2：\n  " + "\n  ".join(bad)


def test_reminders_section_is_named_task_not_todo():
    """待办页的分区标题是「任务」，整页标题仍是「待办」。

    「任务 / 提醒」是一对同层的对象名；「待办 / 提醒」不是 ——
    一个是待办事项，一个是通知方式，不在同一根轴上。
    """
    src = _code(REM)
    assert re.search(r'rem-sect-title">任务<', src), "任务区的标题不见了"
    assert not re.search(r'rem-sect-title">待办<', src), "分区标题又写回「待办」，与页标题同名"
    assert 'aria-label="任务"' in src, "任务区的 aria-label 没跟着改，读屏仍会念「待办」"
    assert re.search(r'title="待办"', src), "整页标题被改了 —— 它要和侧栏的「待办」对齐"


def test_sub_section_heading_does_not_repeat_the_tab():
    """主区小标题只在主区自己分段时才出现，不许把「今天完成」算进分段数。

    ★ 这是第二批的根因修复：`showTaskSub` 原来写作
      `taskBlocks.length + (doneTail.length > 0 ? 1 : 0) > 1`，
      于是今天 tab 只要有一条已完成，单段主区就冒出小标题「今天」——
      而上面的 tab 正写着「今天」。删掉 doneTail 这一项即可，不是删标题本身。
    """
    src = _code(REM)
    assert re.search(r"const showTaskSub = taskBlocks\.length > 1;", src), (
        "showTaskSub 又把 doneTail 算进去了 —— 今天 tab 会在 tab 下面重复一个「今天」"
    )
    assert not re.search(r"showTaskSub\s*=[^;]*doneTail", src), "showTaskSub 里仍有 doneTail"


# ==================== ④ 同一事实只写一处 ====================

def test_tools_header_does_not_repeat_the_gpa_note():
    """工具集页头只说价值，「本机存储 / 登录同步」留给 GPA 卡那一处。

    两处措辞几乎相同，而 GPA 卡那句多了「清理浏览器数据会一并清掉」这个后果，
    是更完整的版本；页头重复它只会让同一件事在一屏里出现两次。
    """
    src = _code(TOOLS)
    sub = re.search(r'\bsub="([^"]*)"', src)
    assert sub, "工具集页头的 sub 找不到了"
    text = sub.group(1)
    assert "数据先存在" not in text, f"页头又在讲本机存储/同步了：「{text}」"
    assert "不用连教务" in text, f"页头没有留下价值主张：「{text}」"

    # 反过来：GPA 卡里那句必须还在 —— 否则不是"去重"，是把唯一的说明删了。
    gpa = (ROOT / "frontend" / "src" / "campus" / "tools" / "gpa" / "GpaTool.tsx").read_text(
        encoding="utf-8"
    )
    assert "清理浏览器数据会一并清掉本地那份" in gpa, (
        "GPA 卡底部那句被删了 —— 去重的前提是保留信息更全的那一份"
    )


# ==================== ⑤ 提示句要挨着它解释的东西 ====================

def test_settings_hint_sits_beside_the_account_card():
    """「改登录名要先验证一次密码」必须夹在账号卡与上手说明按钮之间。

    它解释的是账号卡里的「登录名」。排在「再看一次上手说明」按钮之后时，
    位置本身在说"这句话是在解释那个按钮"——而按钮讲的是别的事。
    """
    src = _code(SETTINGS)
    i_card = src.find("<AccountCard")
    i_hint = src.find("改登录名要先验证一次密码")
    i_button = src.find("再看一次上手说明")
    assert min(i_card, i_hint, i_button) >= 0, "账号卡 / 提示句 / 按钮有一个不见了"
    assert i_card < i_hint < i_button, (
        "提示句不在账号卡与上手说明按钮之间 —— 它读起来会像在解释那个按钮"
    )


# ==================== 第三批：把说明变成控件 ====================
#
# 前两批做的是「删」和「挪」。这批换个思路：**说明之所以要写，是因为控件不说话**。
# 让控件自己说话，那段说明就不必存在了。

GPA_TOOL = ROOT / "frontend" / "src" / "campus" / "tools" / "gpa" / "GpaTool.tsx"
GPA_LOGIC = ROOT / "frontend" / "src" / "campus" / "logic" / "gpa.ts"
PWD_DIALOG = ROOT / "frontend" / "src" / "components" / "settings" / "PasswordDialog.tsx"


def test_gpa_scale_is_collapsed_and_derived_from_the_real_table():
    """换算表默认收起，且档位必须从 logic 的 TABLES 派生，不许在组件里再手写一份。

    原来那张表是一段手写字符串（「90=4.0、85=3.7……」），和 `logic/gpa.ts` 的
    阈值表各活各的：改了一个忘了另一个，就会出现"说明写 85、代码按 84 算"。
    更直接的问题是它在窄屏把卡片撑高 —— 390 宽下切换算法，整块高度差 48px。

    这条断言守两件事：① 手写串不能回来；② 表只能从 `gpaScale()` 来。
    """
    tool = _code(GPA_TOOL)
    logic = _code(GPA_LOGIC)

    assert "export function gpaScale" in logic, "logic 层没有导出的换算表投影函数"
    derived = tool.count("scale: gpaScale(")
    assert derived == 2, (
        f"换算表派生调用有 {derived} 处，应为 2 处（标准 / 北大）；"
        "加权均分没有档位表，只有一句口径"
    )

    # 手写串不许回来。这三段是原来那段 desc 的特征片段。
    for frag in ("90=4.0", "85=3.7", "63 与 60 均记 1.0"):
        assert frag not in tool, f"手写的换算说明又回来了：「{frag}」"

    # 必须是可展开的 details，且摘要说明是哪套算法的表（否则用户不知道点开看的是什么）
    assert re.search(r"<details[^>]*campus-details", tool), "换算表没有收进 <details>"
    assert "<summary>" in tool and "activeAlgo.label" in tool.split("<summary>")[1][:80], (
        "details 的摘要里没有带算法名"
    )


def test_password_rule_speaks_only_when_it_has_something_to_say():
    """密码规则不许常显，改成随输入给反馈。

    原来是输入框下面一行常驻灰字「至少 8 位。别用生日和学号，那是最先被试的几个。」
    —— 没输之前先把规则念一遍，输了之后也不告诉他还差多少。

    现在：没有输入 → 什么都不显示；不足 8 位 → 报差额；够了但形状弱 → 说为什么弱；
    够了且不弱 → 闭嘴（「还差 N 位」消失本身就是可以提交的信号）。
    """
    src = _code(PWD_DIALOG)

    assert "至少 8 位。别用生日和学号" not in src, "常显的静态规则又回来了"
    assert "pwdHint" in src, "实时反馈函数不见了"
    assert re.search(r"\{hint &&", src), "提示不是按条件渲染的 —— 空输入时也会占一行"
    assert 'role="status"' in src, "变化中的提示需要 role=status，否则读屏不会播报"

    # 三条判据各守一件事：硬条件（位数）、形状（纯数字 / 同字符 / 连号）
    for marker in ("还差 ", "纯数字", "连号"):
        assert marker in src, f"弱口令判据少了一条：「{marker}」"

    # 硬条件必须仍然挡住提交 —— 前端的 8 位校验不能改成只提示不拦
    assert re.search(r"newPwd\.length < 8", src), "提交前的 8 位校验被删了"


def test_gpa_empty_state_is_a_row_not_a_lecture():
    """成绩单空态是一行状态，不是虚线框里的一段教程。

    原来：「还没有课程。点「添加课程」手动录入，或「载入示例」先看看效果。」
    两个按钮就在上方 20px 处，这句话在念它们的名字。表头在场，
    本身已经说明「这里会列课程」，缺的是数据不是说明。
    """
    src = _code(GPA_TOOL)
    assert "campus-row-empty" in src, "空态没有收进表格行"
    assert "还没有课程" in src, "空态文字丢了 —— 表头在场也要有一句状态"

    for frag in ("点「添加课程」", "「载入示例」先看看效果"):
        assert frag not in src, f"空态又在念按钮名：「{frag}」"
