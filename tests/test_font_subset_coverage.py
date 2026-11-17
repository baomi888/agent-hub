"""离线字体子集的覆盖守卫：界面上要出现的字，子集里必须有字形。

2026-11-15 用户截图来问「首页『论坛』的『坛』为什么和『论』不一样」。
查下来不是样式问题，也不是浏览器渲染 bug，而是**离线字体子集缺字**：

  `--font-serif` 的第一档是 `LXGW WenKai Screen`（手写体），它只有 1455 个字形
  （2026-10-14 按当时的源码文案裁的），没有「坛」。浏览器于是对这一个字
  逐字回退到后面的 `Noto Serif SC` → `Songti SC` → `SimSun`，画出一个宋体，
  而「论」还是手写体。同一个词里两种字体、两种字重（手写体只有 400，
  700 是合成的；宋体 700 是真的粗体），于是「坛」看着又粗又方。

这类问题**永远表现为"某一个字长得不对"**，接口、构建、测试全绿 —— 只能靠
看得见的人发现。所以补这条守卫，把"文案引入了子集外的字"变成红。

失败时怎么办：
    venv/Scripts/python.exe tools/subset_fonts_project.py
然后重新部署 `frontend/public/fonts/*.woff2`。
"""

import re
from pathlib import Path

from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "frontend" / "public" / "fonts"
SRC = ROOT / "frontend" / "src"

# 只查 .ts/.tsx —— 和生成脚本扫的前端范围逐字对齐。
# 不扫 .css：那里的中文全在注释里（「唇」「笺」「毛玻璃」这类形容），
# 不会渲染；把它们塞进子集只是让字体白变大 50 多个字形。
# 也不扫后端 .py：那是 `--font-sans` 的场景，Noto 带 GB2312 全量兜底。
_SCAN_SUFFIXES = {".ts", ".tsx"}


def _faces():
    """从 fonts.css 里读出 (family, weight, 文件名)。文件被改名也能立刻发现。"""
    css = (FONTS / "fonts.css").read_text(encoding="utf-8")
    faces = re.findall(
        r"font-family:\s*'([^']+)';\s*font-style:\s*normal;\s*font-weight:\s*(\d+);"
        r".*?url\('([^']+)'\)",
        css,
        re.S,
    )
    return faces


def _cmap(font_file: str) -> set:
    path = FONTS / Path(font_file).name
    assert path.exists(), f"fonts.css 指向的 {font_file} 不存在"
    return set(TTFont(str(path)).getBestCmap())


def _source_cjk() -> set:
    chars = set()
    for path in SRC.rglob("*"):
        if path.suffix not in _SCAN_SUFFIXES or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        chars.update(ch for ch in text if "\u4e00" <= ch <= "\u9fff")
    return chars


def test_every_source_character_exists_in_every_offline_face():
    """源码里出现过的每一个汉字，9 个离线字重都必须有字形。

    缺一个字的后果不是"少个字"，而是这个词里混进另一种字体 ——
    字号、重心、笔画粗细全变，看着像排版事故。
    """
    faces = _faces()
    assert len(faces) >= 9, f"fonts.css 只解析出 {len(faces)} 个字重，生成脚本改过？"

    chars = _source_cjk()
    # 扫描失效（路径搬了、后缀改了）时这条会先红，而不是让下面的循环空转
    assert len(chars) >= 1000, f"只扫到 {len(chars)} 个汉字，扫描路径可能失效了"

    broken = []
    for family, weight, url in faces:
        cmap = _cmap(url)
        missing = sorted(ch for ch in chars if ord(ch) not in cmap)
        if missing:
            broken.append(f"{family} {weight}（{Path(url).name}）缺 {len(missing)} 个：{''.join(missing[:40])}")

    assert not broken, (
        "离线字体子集缺字，界面上这些字会回退到系统字体：\n  "
        + "\n  ".join(broken)
        + "\n修复：venv/Scripts/python.exe tools/subset_fonts_project.py，然后重新部署 fonts/*.woff2"
    )


def test_the_word_forum_is_drawn_by_one_single_face():
    """「论坛」两个字都必须在手写体子集里。

    这是上一条的具体病历：`坛` 缺失时浏览器只回退这一个字，
    `论` 还是手写体 —— 一行两个字，两种字体。留一条点名的断言，
    失败时的报错能直接说明症状，不用再翻上面的清单。
    """
    lxgw = None
    for family, _weight, url in _faces():
        if "LXGW" in family:
            lxgw = _cmap(url)
            break
    assert lxgw is not None, "fonts.css 里没有手写体（LXGW 那一档）"

    for ch in "论坛":
        assert ord(ch) in lxgw, (
            f"手写体子集里没有「{ch}」—— 它会在「论坛」这个词里被替换成宋体，"
            "看起来又粗又方（2026-11-15 用户截图反馈过的现象）"
        )


def test_gb2312_fallback_is_actually_complete():
    """GB2312 兜底必须真的覆盖到每个区的**最后一个字**。

    `tools/subset_fonts_project.py` 原来写的是 `range(0xA1, 0xFE)`，上界少了 1，
    于是 low 字节为 0xFE 的那一整列（威 摸 掐 晓 浸 牺 玄…）从来没进过字符集。
    这些字在 Noto Sans 子集里也没有字形，正文里遇到就落到系统宋体上。
    """
    noto = None
    for family, weight, url in _faces():
        if family == "Noto Sans SC" and weight == "400":
            noto = _cmap(url)
            break
    assert noto is not None, "fonts.css 里没有 Noto Sans SC 400"

    # 全部是 GB2312 一级字，且 low 字节都是 0xFE —— 老实现的固定漏网名单
    tail_column = "威摸掐晓浸牺玄"
    missing = [ch for ch in tail_column if ord(ch) not in noto]
    assert not missing, (
        f"GB2312 兜底漏了每区最后一个字：{''.join(missing)}（range 上界应为 0xFF）"
    )
