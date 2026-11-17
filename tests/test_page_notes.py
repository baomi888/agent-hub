"""页头说明：一句解释都不留，除非它能让人避免做错决定（2026-11-17 全站清账）。

沿革：
  · 11-16（P1）：sub 缩成 ≤20 字的主张，原文挪进 ⓘ；
  · 11-17（本批）：评审划掉了十四张截图里的提示，结论是「成熟的 App 不解释自己」。
    于是十页的 sub 整体退场，只剩两页：

      dashboard          —— 管理员口径，读错数字会误判
      settings/reminder  —— 安卓在国内收不到推送，不说明就只会干等

    这两页里，dashboard 的原文收进 ⓘ（InfoNote，按需挂载），reminder 只留一句。

钉五件事（都是"编译不报错、样式也不坏"的那类）：
  1. 该删的都删了 —— 黑名单逐个断言不在源码里（断的是剥过注释的代码）；
  2. 只有允许的两页还有 sub；
  3. 没有 sub 的页头走 pg-head--solo，且标题真的被放大（CSS 里查 --fs-page）；
  4. detail 仍是按需挂载，不是 display:none；
  5. ⓘ 的触控热区还在。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"
SHELL = SRC / "components" / "console" / "PageShell.tsx"
INFO = SRC / "components" / "console" / "InfoNote.tsx"
CONSOLE_CSS = SRC / "app" / "styles" / "console.css"
TOKENS = SRC / "app" / "styles" / "tokens.css"
TOUCH = SRC / "app" / "styles" / "touch.css"

# 页头还允许有说明文的两页（理由见文件头）。key = 页面目录名，value = 允许的最大字数。
ALLOWED_SUB = {
    "dashboard": 12,
    # 这句讲的是"哪些机型根本收不到推送"，删了用户会一直等一条永远不会来的通知。
    # 28 字是它的实际长度，留一点余量。
    "settings/reminder": 30,
}

# 2026-11-17 删掉的提示原文（用片段即可，够长才不会被同名误命中）。
BANNED = [
    "整站文字（正文、标题、按钮）一起变",
    "你现在就是从桌面图标打开的",
    "切片档位（精细 / 均衡 / 完整）",
    "长对话被压缩时，把里面的知识句",
    "还没做好，先置灰",
    "连着问得太快会被暂时挡住",
    "不用连教务，打开就能用",
    "这里的每一项就是提醒中心里的一条待办",
    "分享前自动脱敏",
    "连续天数按每天的总结自动算",
    "聊过的东西容易忘",
    "到点提醒你，课表与打卡自动排好",
    "让知穗在固定时间替你复盘",
    "课件、笔记、论文都能放进来",
    "只要到时间提醒，不用勾完成",
    "按「定时触发」和「你点的」分开记",
    # 2026-11-18：成绩单底部「数据先存在这台设备的浏览器里…清理浏览器数据会一并
    # 清掉本地那份」。用户正在录分数，问的不是"数据在哪"。
    "数据先存在这台设备的浏览器里",
]


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _read_code(p: Path) -> str:
    """剥掉注释再断言 —— 注释里写着"删掉了哪一句"，用原文匹配会自己红自己。"""
    src = _read(p)
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"(?m)^\s*//.*$", "", src)
    return src


def _page_sources() -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    for p in sorted((SRC / "app" / "(console)").rglob("page.tsx")):
        rel = p.relative_to(SRC / "app" / "(console)")
        out.append((str(rel.parent).replace("\\", "/"), p))
    return out


def _sub_of(path: Path) -> str | None:
    m = re.search(r'\bsub="([^"]*)"', _read_code(path))
    return m.group(1) if m else None


def test_only_two_pages_keep_a_sub():
    """说明文只允许留在两页：dashboard（口径）与 settings/reminder（平台限制）。"""
    kept: dict[str, str] = {}
    for name, path in _page_sources():
        val = _sub_of(path)
        if val is not None:
            kept[name] = val
    assert set(kept) == set(ALLOWED_SUB), "页头说明只允许留在 %s，实际留下的是 %s" % (
        sorted(ALLOWED_SUB),
        sorted(kept),
    )
    for name, claim in kept.items():
        assert len(claim) <= ALLOWED_SUB[name], "%s 的主张 %d 字，太长：%s" % (
            name,
            len(claim),
            claim,
        )


def test_banned_hints_are_gone():
    """十四张截图里圈掉的提示，一句都不许回来（整片 src 扫描，含组件与 campus 工具）。"""
    files = [p for p in SRC.rglob("*.tsx") if "node_modules" not in str(p)]
    files += [p for p in SRC.rglob("*.ts") if "node_modules" not in str(p)]
    hits: list[str] = []
    for f in files:
        code = _read_code(f)
        for s in BANNED:
            if s in code:
                hits.append("%s → %s" % (f.relative_to(ROOT), s))
    assert not hits, "这些解释性文案又回来了：\n" + "\n".join(hits)


def test_solo_header_is_wired():
    """没有 sub 时 PageShell 必须打 pg-head--solo（否则标题仍是 20px 的小字）。"""
    src = _read(SHELL)
    assert re.search(r"const\s+solo\s*=\s*!sub\s*&&\s*!detail", src), (
        "PageShell 没有「无说明文」的判定"
    )
    assert "pg-head--solo" in src, "PageShell 没把 solo 形态挂到 class 上"


def test_solo_title_is_bigger_and_overrides_compact():
    """放大规则必须在 compact 的 18px 之外还能生效，否则手机上仍是 18px。"""
    css = _read(CONSOLE_CSS)
    assert "--fs-page" in _read(TOKENS), "tokens.css 缺 --fs-page（页头大标题档）"
    assert re.search(r"\.pg-head\.pg-head--solo h2\s*\{[^}]*var\(--fs-page\)", css), (
        "缺 .pg-head.pg-head--solo h2 的放大规则"
    )
    # 手机档也要放大，且用同一条选择器（特异性压过 .pg-head--compact h2）
    assert css.count(".pg-head.pg-head--solo h2") >= 2, "手机媒体查询里少了 solo 标题那一档"


def test_detail_is_mounted_conditionally():
    """detail 必须"打开才渲染"，不能靠 display:none 藏（藏起来的孩子还在无障碍树里）。"""
    src = _read_code(INFO)
    assert re.search(r"detail\s*:", src), "InfoNote 没有 detail 参数"
    assert re.search(r"\bopen\s*\?\s*\(", src), "InfoNote 里 detail 不是条件渲染"
    assert "display:none" not in src.replace(" ", ""), "InfoNote 用 display:none 藏内容"


def test_shell_wires_detail_to_info_note():
    """PageShell 传了 sub + detail 时走 InfoNote，只传 sub 时仍是单行 pg-sub。"""
    src = _read(SHELL)
    assert "InfoNote" in src, "PageShell 没有引用 InfoNote"
    assert re.search(r"sub\s*&&\s*detail", src), "PageShell 没有按 detail 分流"
    assert 'className="pg-sub"' in src, "PageShell 丢了纯文本那一支"


def test_info_button_has_touch_room():
    """ⓘ 视觉 18px，热区必须扩到 44（伪元素扩，不改宽高）。"""
    src = _read(TOUCH)
    assert ".info-note-btn::before" in src, "touch.css 里没有给 ⓘ 补热区"


def test_settings_no_longer_explains_weights():
    """设置页不再把内部打分模型念给用户听。"""
    src = _read_code(SRC / "app" / "(console)" / "settings" / "page.tsx")
    assert "权重来自手选" not in src, "权重算法说明还在设置页"
    assert "权重 ${c.weight}" not in src, "还在界面上显示权重数字"
