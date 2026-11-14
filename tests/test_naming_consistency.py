"""界面用词一致性：一个东西只能有一个名字（2026-11-14 第四批）。

两件事，都来自「同一件事在两个地方各叫一个名」：

1. **资料库**。侧栏叫「知识库」、页面标题叫「我的资料库」、右栏面板叫「资料库」、
   空状态叫「还没有资料库」。用户点侧栏进来会以为这是另一处 —— 事实上还有一个
   /library（公开广场），三个入口两个词，谁在哪儿说不清。
   「知识库」是后端命名（kb_*）和技术术语，不该出现在界面上。

2. **番茄钟同步条**。进页面、网络恢复、页面重新可见都会自动同步，所以
   「已同步 · 刚刚同步 · 云端共 N 条记录」+「立即同步」按钮是在复述一件本来就
   该成立的事。删掉；但「仅本机」「未同步」留着 —— 那两句说的是后果
   （换设备看不看得到、记录会不会丢），不是状态。

★ 每条都按"故意改坏一次必须变红"验证过（output/_counterproof_batch4.py）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"

NAV = SRC / "lib" / "nav.ts"
KNOWLEDGE = SRC / "app" / "(console)" / "knowledge" / "page.tsx"
LIBRARY = SRC / "app" / "library" / "page.tsx"
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
_ALLOWED_LINES = {
    "lib/chatStatus.ts": re.compile(r"\bre:\s*/"),
}

# 不扫的两处：
#  · _backup/          旧版快照，不参与构建
#  · app/forum/        论坛页由另一个会话在改（同一批里也会把「公开知识库」改成
#                      「公开资料库」）。等它合并后应当删掉这条豁免，否则论坛里的
#                      旧词永远扫不出来。
_SKIP_PREFIX = ("_backup/", "app/forum/")


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
            if "知识库" not in line:
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


def test_public_library_uses_same_word():
    lib = _code(LIBRARY)
    assert "<h1>公开资料库</h1>" in lib, "公开广场的 h1 没跟着统一（应为「公开资料库」）"


# ==================== ②b 后端返回的展示串也算界面文案 ====================

def test_backend_display_string_matches_ui():
    """`api/kb.py` 收藏接口里的 `source` 是**给用户看的字符串**，不是判定值。

    ★ 这条是补漏：第一轮只扫了 frontend/src，源码里干净了，线上「我的资料库」
      页面上却还挂着「来源官方知识库」—— 那句话是后端拼好返回的。
      判定字段是 `official`（布尔），`source` 只负责显示，所以它可以跟着改。
    """
    kb = (ROOT / "api" / "kb.py").read_text(encoding="utf-8")
    # 按"键值对"精确匹配，不用「"官方知识库" 在整个文件里不出现」那种写法：
    # 516 行的 docstring 里抄着用户原话（"除了用官方知识库，还可以…"），
    # 那是历史引语，改了就是篡改记录。
    assert '"source": "官方知识库"' not in kb, "api/kb.py 的展示串还叫「官方知识库」"
    assert '"source": "官方资料库"' in kb, "api/kb.py 没有返回「官方资料库」"


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
