"""更新公告：内容纪律 + 「滑到底才放行」这条机制本身（2026-11-15 第 20 批）。

这一批加了第 3 期公告。公告是**每期强制**的（见 AnnounceModal 顶部注释），
用户没有「稍后再说」，所以它有两个不能靠自觉的地方，必须由测试钉住：

  一、期号与已读键
     已读判定是 `baomi.announce.v1.<uid>.<期号 id>`。**id 复用不会报错**，
     只是读过那一期的人再也不会看到 —— 静默失效，而且发现不了。
     所以这里把用过的 id 写成一份冻结清单，新一期必须不在里面。

  二、强制机制的四条边界
     ① 没有关闭按钮、点空白不关；② 滑到底一定能关（内容不足一屏直接解锁）；
     ③ 解锁后不回锁；④ 新用户先读四步引导，公告顺延。
     这四条只要有任意一条被「优化」掉，用户就会被彻底困住，而页面上看不出异常。

★ 假绿防护（沿用 test_chat_layout 的三条）：
  1. 断言源码前先 `_code()` 剥注释 —— `AnnounceModal` 的注释里就写着
     「没有关闭按钮」「Esc 在读到底之前无效」，不剥的话这几条永远绿。
  2. 每条扫描型断言都配数量下限，正则一失效就红。
  3. 文案断言按**逐条**判，不做整文件子串 —— 一个词在注释里出现一次就够骗过整文件。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"

ANN_TS = SRC / "lib" / "announcements.ts"
MODAL = SRC / "components" / "announce" / "AnnounceModal.tsx"
HOME = SRC / "app" / "page.tsx"
CONSOLE_LAYOUT = SRC / "components" / "console" / "ConsoleLayout.tsx"

# ★ 发布过的期号。新一期**必须**不在这个集合里（id 复用 = 老用户永远看不到）。
USED_IDS = {"2026-11", "2026-11-b"}

# 产品周报腔。公告的语气是「同学在群里跟你讲」，不是「本次更新聚焦三大方向」。
FORBIDDEN = (
    "赋能",
    "闭环",
    "一站式",
    "助力",
    "体验升级",
    "全方位",
    "本次更新聚焦",
    "生态化",
    "焕新",
)


def _read(p: Path) -> str:
    assert p.is_file(), f"文件不在了：{p}"
    return p.read_text(encoding="utf-8")


def _code(src: str) -> str:
    """剥掉块 / 行注释（注释里的话不算数）。

    ★ 只剥 `/* */` 与整行 `//`，**不做** `{/* */}` 那种花括号成对剥离 ——
      `\\{\\s*/\\*.*?\\*/\\s*\\}` 在 JSX 里会从函数签名的 `{` 一路吃到
      第一个 `*/`，一份 9.4KB 的文件剥完只剩 4KB，而症状是
      「`const key` 找不到」这种莫名其妙的报错（本文件第一版就栽在这里）。
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"^\s*//.*$", "", src, flags=re.M)
    return src


def _entries() -> list[dict]:
    """把 ANNOUNCEMENTS 数组按 `id:` 切成一期一块，返回原始文本块。

    不引 JS 解析器：这里只需要「几期、每期几条、正文多长」，
    正则 + 切片足够；真正的结构由 tsc 保证。
    """
    raw = _code(_read(ANN_TS)).split("export const ANNOUNCEMENTS")[1]
    starts = [m.start() for m in re.finditer(r'^\s{4}id:\s*"', raw, flags=re.M)]
    assert len(starts) >= 3, f"只切到 {len(starts)} 期，数组结构变了"
    blocks = []
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(raw)
        blocks.append(raw[s:e])
    return blocks


def _str(block: str, key: str) -> str:
    """取 `key: "..."`，支持 `"a" + "b"` 这种跨行拼接。"""
    m = re.search(rf'{key}:\s*((?:"(?:[^"\\]|\\.)*"\s*\+?\s*)+)', block)
    assert m, f"这一期没有 {key}"
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))
    return "".join(parts)


def _plain(s: str) -> str:
    """去掉行内标记，得到用户实际读到的字数。"""
    return s.replace("**", "").replace("==", "")


def test_issue_ids_are_never_reused():
    ids = [re.search(r'id:\s*"([^"]+)"', b).group(1) for b in _entries()]
    assert len(ids) == len(set(ids)), f"期号重复：{ids}"
    newest = ids[0]
    assert newest not in USED_IDS, f"新一期用了旧 id（{newest}），读过的人不会再弹"
    # 二期以上：确认它真的是「新的一期」而不是把上一期改了内容
    assert newest.startswith("2026-11"), f"期号命名漂了：{newest}"


def test_latest_issue_is_the_one_shipping_now():
    b = _entries()[0]
    assert re.search(r'label:\s*"第 3 期"', b), "最新一期不是第 3 期"
    assert _str(b, "date") == "2026 年 11 月 15 日"
    assert _str(b, "title").startswith("十一月更新 · "), "标题没跟上一期的写法"
    assert len(_plain(_str(b, "lead"))) >= 30, "lead 太短，等于没摘要"


def test_change_numbers_are_sequential():
    for b in _entries():
        nos = re.findall(r'no:\s*"(\d{2})"', b)
        assert nos, "这一期一条正文都没有"
        assert nos == [f"{i:02d}" for i in range(1, len(nos) + 1)], f"编号不连续：{nos}"
    # 强制读的代价是篇幅：这一期最多 5 条，多了读不完也记不住。
    # 只钉最新一期 —— 第 1 期是 6 条（历史事实，不追改），规则从这一期起生效。
    nos = re.findall(r'no:\s*"(\d{2})"', _entries()[0])
    assert len(nos) <= 5, f"正文 {len(nos)} 条，超出一期能读完的量"


def test_each_paragraph_stays_within_three_lines():
    newest = _entries()[0]
    for b in _entries():
        for chunk in b.split('no: "')[1:]:
            body = _str(chunk, "body")
            n = len(_plain(body))
            assert 10 <= n <= 130, f"正文 {n} 字，手机上超过三行：{body[:20]}…"
            # 补充条目只在 `points: [...]` 里数。按缩进数会把正文第二行也算进来 ——
            # 正文续行与条目同一个缩进，第一版就是这么误判出「5 条」的。
            sec = re.search(r"points:\s*\[(.*?)\]", chunk, re.S)
            pts = re.findall(r'"((?:[^"\\]|\\.)*)"', sec.group(1)) if sec else []
            assert len(pts) <= 6, f"补充条目 {len(pts)} 条，太多了"
            # 条目数与单条长度只钉最新一期：第 1 期有一条 88 字、有一项 5 个条目
            # （都是历史事实，不追改）。规则从这一期起生效 —— 否则断言一上线就是红的，
            # 红久了就没人看。
            if b is newest:
                assert len(pts) <= 4, f"补充条目 {len(pts)} 条，太多了"
                for p in pts:
                    assert len(_plain(p)) <= 60, f"补充条目太长：{p[:16]}…"


def test_minors_are_a_list_not_a_paragraph():
    for b in _entries():
        sec = b.split("minors: [")[1] if "minors: [" in b else ""
        n = len(re.findall(r'^\s{6}"', sec, flags=re.M))
        assert 0 < n <= 8, f"细修 {n} 条（0 说明解析失效，>8 说明在堆流水账）"


def test_inline_markup_is_balanced():
    """只支持 **加粗** 与 ==着色==。少一个符号，页面上就会露出星号。"""
    for b in _entries():
        for s in re.findall(r'"((?:[^"\\]|\\.)*)"', b):
            assert s.count("**") % 2 == 0, f"加粗没闭合：{s[:30]}"
            assert s.count("==") % 2 == 0, f"着色没闭合：{s[:30]}"


def test_no_product_weekly_speak():
    allstr = " ".join(
        re.findall(r'"((?:[^"\\]|\\.)*)"', _code(_read(ANN_TS)).split("ANNOUNCEMENTS")[1])
    )
    assert len(allstr) > 1000, "取到的文案太少，正则可能失效"
    hit = [w for w in FORBIDDEN if w in allstr]
    assert not hit, f"公告里出现了产品周报腔：{hit}"


# ---------------------------------------------------------------- 机制本身


def test_modal_has_no_close_affordance():
    code = _code(_read(MODAL))
    assert "unlocked" in code
    # 没有关闭按钮、没有右上角 ×、点遮罩不关
    assert "ann-x" not in code, "公告出现了关闭按钮"
    assert 'aria-label="关闭' not in code
    assert not re.search(r"ann-page[^>]*onClick", code), "点空白能关，强制就不成立了"
    # 唯一的出口是「开始使用」，且它必须挂着 disabled。
    # ★ 必须排除 `aria-disabled`：那一行也有 `={!unlocked}`，
    #   裸子串 `disabled={!unlocked}` 会被它匹配上 —— 按钮真去掉 disabled 也照样绿
    #   （第 20 批的反证第 1 条就是这么漏的）。
    assert re.search(r"(?<!aria-)disabled=\{!unlocked\}", code), "按钮没有锁"


def test_unlock_condition_is_reaching_the_bottom():
    code = _code(_read(MODAL))
    assert "BOTTOM_EPS" in code
    # 内容不足一屏直接解锁 —— 否则大屏上「没东西可滚、按钮永远灰着」
    assert re.search(r"max\s*<=\s*BOTTOM_EPS", code), "不足一屏时没有直接解锁"
    assert re.search(r"scrollTop\s*>=\s*max\s*-\s*BOTTOM_EPS", code), "没有触底判定"


def test_esc_is_inert_before_unlock():
    code = _code(_read(MODAL))
    assert re.search(r'if\s*\(e\.key\s*!==\s*"Escape"\)\s*return;', code)
    esc = code.split('e.key !== "Escape"')[1][:260]
    assert "if (!unlocked)" in esc and "preventDefault" in esc, "没解锁时 Esc 没被吃掉"


def test_never_relocks_after_unlock():
    """往回滚一点又给锁上，是拿用户的手势惩罚用户。"""
    code = _code(_read(MODAL))
    assert not re.search(r"setUnlocked\(false\)", code), "存在回锁路径"


def test_guide_goes_first_for_new_users():
    """新用户先撞四步引导。两个都弹会叠成两层，所以公告要顺延。"""
    code = _code(_read(MODAL))
    assert "guideDone" in code
    assert re.search(r"!dismissed\s*&&\s*guideDone", code), "公告没有给引导让路"


def test_preview_link_is_the_only_way_in_without_login():
    code = _code(_read(MODAL))
    assert re.search(r"isDebug\s*\|\|", code), "?ann= 预览走不通了"
    assert re.search(r"if \(!open \|\| !ann\) return null", code)


def test_read_mark_carries_the_issue_id():
    """期号不进 key，换一期就不会重弹 —— 这是整个机制的地基。"""
    code = _code(_read(MODAL))
    m = re.search(r"const key = `([^`]+)`", code)
    assert m, "读不到已读键的拼法"
    assert "${ann?.id" in m.group(1) or "${ann.id" in m.group(1), (
        f"已读键里没有期号：{m.group(1)}"
    )


def test_modal_is_mounted_on_both_landing_pages():
    """首页与控制台各自挂一次。少挂一处，从那一处进来的人就看不到公告。"""
    home = _code(_read(HOME))
    console = _code(_read(CONSOLE_LAYOUT))
    assert "AnnounceModal" in home, "首页没挂公告"
    assert "AnnounceModal" in console, "控制台没挂公告"
    # 挂载点必须传 userId：没有 uid 记不住已读，未登录用户每次进来都会被拦
    assert re.search(r"<AnnounceModal[^>]*userId=", home + console), "没传 userId"


def test_copy_never_tells_users_posting_needs_no_login():
    """2026-11-15 16:42 用户要求：**这是实现细节，用户不需要知道**。

    当时公告第 01 条写的是「没登录也能发 —— 认的是你这台设备」，论坛右栏侧卡
    第一句是「不用注册，也不用登录」。两处都清掉了，并且不许以别的说法回来。

    ★ 论坛页的注释里有 5 处「免登录」（给维护者看的），必须先 `_code()` 剥掉，
      否则这条断言要么恒红、要么为了绿而放宽成永远不匹配。
    """
    forum = _code(_read(SRC / "app" / "forum" / "page.tsx"))
    ann = _code(_read(ANN_TS))
    assert len(forum) > 5000, "论坛页代码没读到，断言等于没跑"
    assert len(ann) > 1000, "公告代码没读到"
    banned = ("免注册", "不用注册", "不注册", "无需注册", "免登录发布", "不用登录也能", "没登录也能")
    for src, name in ((forum, "forum/page.tsx"), (ann, "announcements.ts")):
        hit = [w for w in banned if w in src]
        assert not hit, f"{name} 又出现了「不登录也能发」的说法：{hit}"
