"""资料库双 tab（2026-11-23）。

改版把「我的资料库」与「公开知识库」收进同一页：
  · 左栏只有一个「资料库」入口（不再有独立的「公开知识库」按钮）；
  · 资料库页顶部两个 tab，数据不合并 —— 一边可上传/删除，一边只读；
  · 公开那一栏复用 components/library/PlazaPane，`/library` 也用它。

这里钉的是"只有一份实现"这条 —— 复制两套业务代码后，改一边忘一边
（下载权限、卡片字段、留言板）就会漂移，而且两头都长得像对的。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"
KNOWLEDGE = SRC / "app" / "(console)" / "knowledge" / "page.tsx"
LIB_ROUTE = SRC / "app" / "library" / "page.tsx"
PLAZA = SRC / "components" / "library" / "PlazaPane.tsx"
LIB_CSS = SRC / "app" / "styles" / "library.css"


def _read(p: Path) -> str:
    assert p.exists(), f"文件不存在：{p}"
    return p.read_text(encoding="utf-8")


def _code(p: Path) -> str:
    s = _read(p)
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"(?<![:\w])//[^\n]*", "", s)
    return s


def test_library_page_has_two_tabs_sharing_one_plaza():
    """两个 tab 是同一页面的平级分类，公开那栏必须复用 PlazaPane。"""
    k = _code(KNOWLEDGE)
    assert 'role="tablist"' in k, "资料库页没有 tablist 语义"
    assert "我的资料库" in k and "公开资料库" in k, "两个 tab 没有都出现"
    assert re.search(r"aria-selected=\{tab === \"(mine|public)\"\}", k), (
        "tab 没有 aria-selected：读屏器与键盘用户看不出当前在哪一栏"
    )
    assert "<PlazaPane embedded" in k, (
        "公开 tab 没有复用 PlazaPane —— 广场会变成第二份实现"
    )
    # 上传只属于"我的"：公开库是别人的，这里没有"上传给它"这回事
    assert re.search(r"actions=\{\s*tab === \"mine\"", k), (
        "上传按钮没有按 tab 收窄：在公开资料库里会露出一个点不动的上传"
    )


def test_library_route_is_a_thin_shell_over_the_same_pane():
    """/library 不能因为抽组件而失效，也不能自己再写一套。"""
    route = _code(LIB_ROUTE)
    assert "<PlazaPane />" in route, "/library 没有渲染 PlazaPane"
    assert "api." not in route, "/library 里又出现了接口调用 —— 业务逻辑应当只在 PlazaPane"

    plaza = _code(PLAZA)
    assert "embedded = false" in plaza, "PlazaPane 没有 embedded 入参"
    assert "!embedded &&" in plaza, "独立路由的页头没有按 embedded 收起来"
    assert '<div className="lib-main">' in plaza, (
        "嵌入时仍然输出 <main>：套在资料库页的 <main> 里是非法结构"
    )

    css = _read(LIB_CSS)
    assert ".lib-root--embedded" in css, "嵌入模式没有去掉 100dvh 与自己的滚动（会双滚动条）"
    assert ".lib-tabs" in css, "tab 样式缺失"
