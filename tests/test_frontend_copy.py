"""页头文案的「不许又写成技术串」断言。

内测反馈（2026-10-27 林宝宝，8 张截图）里最扎的一条不是功能缺失，而是
**页面上写着用户看不懂的东西**：/flow 把 UUID、任务 id、可编辑 JSON 摊在界面上，
其余几屏的页头第一行顶着 `P4 · /delegate` 这种开发期编号 + 路由路径。

后者尤其容易回潮：新加一屏时照着旧页面抄，「P9 · /foo」就又回来了 ——
而此时页面一切正常、接口全部 200、视觉上也不难看，没有任何一条断言会红。
等到下一个内测用户问「这个 P4 是什么」才发现。

所以这里把「眉题必须是中文短语」钉成不变量：
  · 不允许出现 `P<数字>` 开发期编号
  · 不允许出现 `/xxx` 形式的内部路由
  · 必须含中文字符（不能是纯英文/纯符号）

刻意用源码级断言而不是端到端截图比对：这条规则是**文案规则**，
不需要渲染就能判定，跑一次几十毫秒，成本低到没理由不钉。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "frontend" / "src" / "app"

# 只抓字符串字面量写法：eyebrow="xxx" ；表达式写法（eyebrow={...}）跳过，
# 那种由页面自己拼，静态读不到，硬猜反而会误报。
_EYEBROW_STR = re.compile(r'eyebrow="([^"]*)"')
_EYEBROW_EXPR = re.compile(r"eyebrow=\{")

# 开发期编号：P1 / P4 / P12 这种（后面跟分隔符，避免误伤 "P3M" 这类正常词）
_DEV_TICKET = re.compile(r"(^|[^A-Za-z0-9])P\d{1,2}(?![A-Za-z0-9])")
# 内部路由：/delegate、/api/cet/plan 等
_INTERNAL_ROUTE = re.compile(r"(^|\s)/[a-z]")
_CJK = re.compile(r"[\u4e00-\u9fff]")


def _page_files() -> list[Path]:
    files = sorted(APP_DIR.glob("*/page.tsx"))
    # 目录都没了说明路径写错了，别让这条断言"因为找不到文件"而静默通过
    assert files, f"没找到任何页面文件，检查路径：{APP_DIR}"
    return files


def _eyebrows() -> list[tuple[str, str]]:
    """返回 [(相对路径, 眉题文案)]"""
    out: list[tuple[str, str]] = []
    for f in _page_files():
        src = f.read_text(encoding="utf-8")
        for m in _EYEBROW_STR.finditer(src):
            out.append((f.relative_to(ROOT).as_posix(), m.group(1)))
        # 表达式写法存在时，无法静态校验 —— 至少留下痕迹让人知道这里有漏网的可能
        if _EYEBROW_EXPR.search(src) and not _EYEBROW_STR.search(src):
            pytest.skip(f"{f.name} 的眉题是表达式写法，无法静态校验")
    return out


def test_eyebrow_has_no_dev_ticket_or_route():
    """眉题不许写开发期编号，也不许写内部路由。"""
    bad: list[str] = []
    for path, text in _eyebrows():
        if _DEV_TICKET.search(text):
            bad.append(f"{path}: eyebrow=\"{text}\" —— 含开发期编号 P#")
        if _INTERNAL_ROUTE.search(text):
            bad.append(f"{path}: eyebrow=\"{text}\" —— 含内部路由 /xxx")
    assert not bad, (
        "页头眉题是用户第一眼看到的字，不能写开发期编号或路由路径。"
        "改成一句用户看得懂的中文短语（例：把「P4 · /delegate」改成「自动替你干活」）：\n  "
        + "\n  ".join(bad)
    )


def test_eyebrow_is_chinese_phrase():
    """眉题必须是中文短语 —— 纯英文/纯符号对用户等于没写。"""
    bad = [
        f"{path}: eyebrow=\"{text}\""
        for path, text in _eyebrows()
        if not _CJK.search(text)
    ]
    assert not bad, "页头眉题里没有中文字符：\n  " + "\n  ".join(bad)


def test_every_page_has_eyebrow():
    """每一屏都要有眉题：没有眉题的页头只剩一个标题，用户不知道自己在哪一层。"""
    missing = [
        f.relative_to(ROOT).as_posix()
        for f in _page_files()
        if "ConsoleShell" in f.read_text(encoding="utf-8")
        and not _EYEBROW_STR.search(f.read_text(encoding="utf-8"))
        and not _EYEBROW_EXPR.search(f.read_text(encoding="utf-8"))
    ]
    assert not missing, "以下页面用了 ConsoleShell 却没传 eyebrow：\n  " + "\n  ".join(missing)
