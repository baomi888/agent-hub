"""页头文案的两条不变量：眉题是例外不是默认，说明句是句子不是目录。

内测反馈（2026-10-27 林宝宝，8 张截图）里最扎的一条不是功能缺失，而是
**页面上写着用户看不懂的东西**：/flow 把 UUID、任务 id、可编辑 JSON 摊在界面上，
其余几屏的页头第一行顶着 `P4 · /delegate` 这种开发期编号 + 路由路径。

当时把「眉题必须是中文短语」钉成了不变量。2026-11-14 的全站审查往后走了一步：
**眉题这件事本身价值有限** —— 13 个页面全是「眉题 + 标题 + 一句说明」三层，
而侧栏高亮已经在说"你在哪"，标题自己又说一遍，说明句再解释一遍，同一件事说三次。
实测结论也早就在仓库里：待办页给手机端做的 `compact` 就是把眉题藏掉，
注释写的是「占 18px 却不提供任何行动线索」。

所以现在的不变量是：
  · 眉题只允许出现在 admin 那两个调试面（dashboard / flow），值必须标"管理 · 内部"；
    其余页面出现任何 eyebrow= 都算回潮 —— 加页面时照着旧页抄是最容易的复发路径
  · 眉题仍不许写开发期编号 P# 和内部路由 /xxx
  · 说明句（sub）是**句子**，字面量写法不超过 60 字；写成小目录（"账号、外观、
    检索、隐私、用量…"）的那种一定会超

刻意用源码级断言而不是端到端截图比对：这些都是**文案规则**，不需要渲染就能判定，
跑一次几十毫秒，成本低到没理由不钉。

★ 2026-11-14 修了一个静默死角：原来 `_page_files()` 用的是 `APP_DIR.glob("*/page.tsx")`，
  只覆盖 app 根目录那 6 个页面；13 个控制台页搬进 `(console)/` 之后就一个都扫不到了，
  于是「眉题不许写 P#」这条一直在空转。现在改 `rglob`，并加了扫描数量的下限断言 ——
  路径再变一次也会红，而不是安静地什么都不查。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "frontend" / "src" / "app"
SHELL = ROOT / "frontend" / "src" / "components" / "console" / "PageShell.tsx"

# 只抓字符串字面量写法：eyebrow="xxx" ；表达式写法（eyebrow={...}）静态读不到，跳过。
_EYEBROW_STR = re.compile(r'eyebrow="([^"]*)"')
_EYEBROW_EXPR = re.compile(r"eyebrow=\{")
_SUB_STR = re.compile(r'sub="([^"]*)"')

# 开发期编号：P1 / P4 / P12 这种（后面跟分隔符，避免误伤 "P3M" 这类正常词）
_DEV_TICKET = re.compile(r"(^|[^A-Za-z0-9])P\d{1,2}(?![A-Za-z0-9])")
# 内部路由：/delegate、/api/cet/plan 等
_INTERNAL_ROUTE = re.compile(r"(^|\s)/[a-z]")
_CJK = re.compile(r"[\u4e00-\u9fff]")

# 唯一允许有眉题的页面（都是管理员调试面，布局表达不出"这是内部工具"）。
ADMIN_EYEBROW_PAGES = {"dashboard", "flow"}
# 说明句的长度**兜底**上限：现在最长的一句 43 字（装到桌面那页）。
# ★ 长度不是判据 —— 2026-11-14 删掉的四句都在 40 字以内，靠长度一个都拦不住。
# 它只用来挡住"写成一整段话"，真正的判据在句形（见下方那条测试）。
SUB_MAX_CHARS = 80
# 字面量 sub 的页面数下限。防止有人把 sub 全改成表达式写法绕开长度检查 ——
# 两个标签页（dashboard / checkin / delegate / settings）本来就是表达式，余量留 2。
SUB_LITERAL_FLOOR = 6


def _page_files() -> list[Path]:
    files = sorted(APP_DIR.rglob("page.tsx"))
    # 目录都没了说明路径写错了，别让这条断言"因为找不到文件"而静默通过。
    # 2026-11-14：glob 改 rglob 之后必须有这条 floor —— 否则下次路由再挪一层，
    # 三条断言会一起变成空转，而它们是绿的。
    assert len(files) >= 15, f"只扫到 {len(files)} 个页面，路径可能变了：{APP_DIR}"
    return files


def _rel(f: Path) -> str:
    return f.relative_to(ROOT).as_posix()


def _eyebrows() -> list[tuple[str, str]]:
    """返回 [(相对路径, 眉题文案)]"""
    return [
        (_rel(f), m.group(1))
        for f in _page_files()
        for m in _EYEBROW_STR.finditer(f.read_text(encoding="utf-8"))
    ]


def test_eyebrow_has_no_dev_ticket_or_route():
    """眉题不许写开发期编号，也不许写内部路由。"""
    bad: list[str] = []
    for path, text in _eyebrows():
        if _DEV_TICKET.search(text):
            bad.append(f'{path}: eyebrow="{text}" —— 含开发期编号 P#')
        if _INTERNAL_ROUTE.search(text):
            bad.append(f'{path}: eyebrow="{text}" —— 含内部路由 /xxx')
    assert not bad, (
        "页头眉题是用户第一眼看到的字，不能写开发期编号或路由路径。\n  " + "\n  ".join(bad)
    )


def test_eyebrow_is_chinese_phrase():
    """眉题必须是中文短语 —— 纯英文/纯符号对用户等于没写。"""
    bad = [f'{path}: eyebrow="{text}"' for path, text in _eyebrows() if not _CJK.search(text)]
    assert not bad, "页头眉题里没有中文字符：\n  " + "\n  ".join(bad)


def test_eyebrow_is_the_exception_not_the_default():
    """眉题只留给 admin 调试面，且必须标「管理 · 内部」。

    2026-11-14 全站删掉 11 条眉题（「校园生活」「你的偏好」「每日总结」「把今天记下来」
    …），它们都在替侧栏高亮和标题重说一遍。这条断言的作用不是"删干净了就完事"，
    而是**拦住下一个人照着旧页面抄**：新加一屏时写 eyebrow="学习数据"，这里会红。
    """
    offenders: list[str] = []
    for f in _page_files():
        src = f.read_text(encoding="utf-8")
        if not (_EYEBROW_STR.search(src) or _EYEBROW_EXPR.search(src)):
            continue
        page = f.parent.name
        if page not in ADMIN_EYEBROW_PAGES:
            offenders.append(f"{_rel(f)} —— 页面「{page}」不该有眉题")

    assert not offenders, (
        "眉题只在 admin 调试面（dashboard / flow）保留，用来标「管理 · 内部」。"
        "其余页面的定位由侧栏高亮和标题负责，再加一行小字等于把「你在哪」说第三遍。"
        "真觉得缺信息，先问：这句话布局说得出来吗？\n  " + "\n  ".join(offenders)
    )

    # 白名单里的也不能写空话 —— 它的全部价值就是"这是内部工具"。
    for path, text in _eyebrows():
        page = Path(path).parent.name
        if page in ADMIN_EYEBROW_PAGES:
            assert "管理" in text or "内部" in text, (
                f'{path}: 眉题 "{text}" 已经不再说明"这是内部工具"，那它就只是装饰，去掉'
            )


def test_page_shell_treats_eyebrow_and_sub_as_optional():
    """PageShell 的两个属性必须是可选的，且渲染时判断缺失。

    只把属性改成 `?` 而渲染处仍无条件输出 <p className="eyebrow">，
    会渲染出一个空的 18px 小字块 —— 每个页面首屏白占一行，还没有任何报错。
    """
    shell = SHELL.read_text(encoding="utf-8")
    assert "eyebrow?: string" in shell, "PageShell 的 eyebrow 又变回必填了"
    assert "sub?: ReactNode" in shell, "PageShell 的 sub 又变回必填了"
    assert "{eyebrow ? <p className=\"eyebrow\">" in shell, "eyebrow 缺失时仍会渲染空的小字块"
    assert "{sub ? <p className=\"pg-sub\">" in shell, "sub 缺失时仍会渲染空段落（自带 margin）"


def test_sub_does_not_navigate_or_introduce_the_form():
    """说明句不许描述控件路径，也不许说"这里能做什么"。

    这两类句子有稳定的字面标志，静态就能判，而且正好覆盖 2026-11-14 删掉的两条：
      · 「权限只有一处：待办 → 设置 → 提醒权限。」
        —— 页头 actions 里就有一个跳到该处的按钮，句子在替按钮说话；
      · 「密码、昵称、头像都能在这里改。改登录名要验证一次密码。」
        —— 前半句是把上面那张卡的字段念一遍，只有后半句是规则。

    ★ 为什么不用"长度上限"当判据：这条最早写的是「sub ≤ 60 字」，反证时发现
      它**拦不住任何一条真正被删的句子** —— 那四条分别是 33 / 24 / 30 / 40 字，
      全都够不到 60。长度不是判据，"复述"才是。所以换成句形判据，
      长度只留 80 字当兜底（现在最长的一句 43 字）。
    """
    arrow = ("→", "->", "⇒")
    intro = ("在这里", "这里可以", "在这里能", "都能在这里")
    bad: list[str] = []
    for f in _page_files():
        for m in _SUB_STR.finditer(f.read_text(encoding="utf-8")):
            text = m.group(1)
            if any(a in text for a in arrow):
                bad.append(f'{_rel(f)}: 用箭头描述控件路径 —— "{text}"')
            if any(text.startswith(i) or i in text for i in intro):
                bad.append(f'{_rel(f)}: 在说"这里能做什么" —— "{text}"')
            if len(text) > SUB_MAX_CHARS:
                bad.append(f"{_rel(f)}: {len(text)} 字，已经是一段话而不是一句说明")

    assert not bad, (
        "说明句只留布局看不见的事实（口径 / 限制 / 后果 / 隐私边界）。"
        "描述控件路径、清点卡片字段的句子属于 R1，删掉让布局自己说：\n  " + "\n  ".join(bad)
    )


def test_sub_literals_are_still_checked():
    """字面量 sub 的数量下限 —— 防止有人把 sub 全改成表达式绕开上面那条检查。

    dashboard / checkin / delegate / settings 本来就是表达式写法（值里含变量或
    加粗片段），所以余量留 2。
    """
    literals = sum(len(_SUB_STR.findall(f.read_text(encoding="utf-8"))) for f in _page_files())
    assert literals >= SUB_LITERAL_FLOOR, (
        f"只找到 {literals} 处字面量 sub（< {SUB_LITERAL_FLOOR}）。"
        "多半是有人把 sub 改成了表达式写法来绕开检查 —— 那样上面那条断言就空转了。"
    )
