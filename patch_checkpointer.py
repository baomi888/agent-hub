# -*- coding: utf-8 -*-
"""修复 agent/builder.py 里 checkpointer 用错导致的 TypeError。

症状：
    新建会话正常，但一发消息就报
    Agent 执行失败：TypeError: Invalid checkpointer provided. Expected an instance of
    `BaseCheckpointSaver`, `True`, `False`, or `None`. Received _GeneratorContextManager.

原因：
    `SqliteSaver.from_conn_string(path)` 是 @contextmanager，返回的是**上下文管理器**
    而不是 saver 实例，却被直接塞进了 create_agent(checkpointer=...)。

做法：
    让 `_make_checkpointer()` 直接返回 None（不用 LangGraph 检查点）。会话记忆本来就存在
    SQLite 会话库（core/session.py），api/chat.py 每轮都会把完整历史重建成 messages 一起
    喂给 agent；再挂检查点会重复累积（实测第 3 轮状态 12 条，本应 6 条）。详见新函数里的注释。

安全性：
    - 先备份为 builder.py.bak-<时间戳>
    - 改完立刻 py_compile 自检，**编译不过自动还原**
    - 幂等：已打过会识别并跳过，可放心重复运行

用法：
    python3 patch_checkpointer.py                    # 自动寻找 agent/builder.py
    python3 patch_checkpointer.py /root/agent/builder.py
"""
import os
import py_compile
import re
import shutil
import sys
import time

MARK = "故意返回 None"

NEW_FUNC = '''def _make_checkpointer():
    """Agent 的 checkpointer —— 这里**故意返回 None**，即不用 LangGraph 检查点。

    三条都是实测结论，别照着直觉改回去：

    1. 会话历史本来就存在 SQLite 会话库（core/session.py），api/chat.py 每轮都会把
       完整历史重建成 messages 一起喂给 agent。再挂一个 checkpointer 等于**记两份**，
       而且这份还只在进程内存里。
    2. 两份同时用会**重复累积**（已实测）：第 2 轮状态里第 1 轮的消息出现 2 次、
       第 3 轮出现 3 次，3 轮后状态 12 条（本应 6 条）。上下文平方级膨胀，
       白烧 token，还会让模型看到重复提问。
    3. 原实现 `SqliteSaver.from_conn_string(path)` 是**错的**：它是 @contextmanager，
       返回上下文管理器而不是 saver 实例，于是每次对话直接抛
       `TypeError: Invalid checkpointer provided ... Received _GeneratorContextManager`。
       注意改成 `SqliteSaver(sqlite3.connect(path))` 也不行 —— 同步 saver 不支持
       astream（会换成 NotImplementedError），必须用 AsyncSqliteSaver，
       而它要在 lifespan 里 async 打开。为了这点收益不值得，见下条。

    用 DB 当唯一事实来源反而更稳：重启不丢（比 MemorySaver 强）、受 MAX_HISTORY_TURNS
    截断保护、不用额外依赖。真要用检查点，必须同时让 api/chat.py 只发当前这一条，
    不要再重放历史 —— 否则就会踩第 2 条。
    """
    return None


# 模块级单例：目前是 None（不用 LangGraph 检查点，记忆交给 SQLite 会话库）
_CHECKPOINTER = _make_checkpointer()'''

NEW_GETCP_DOC = '''    """当前 checkpointer（默认 None）。

    保留这个入口，是为了将来真要换落盘版时不用改调用方。换的时候记住两条：
    ① 同步的 SqliteSaver 不支持 astream，必须用
       `langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver`，并在 lifespan 里 async 打开；
    ② 必须同时让 api/chat.py 只发当前这一条消息，别再重放历史，否则消息会重复累积。
    """'''

START = "def _make_checkpointer():"
END = "_CHECKPOINTER = _make_checkpointer()"
GETCP_ANNOT = "def get_checkpointer() -> MemorySaver:"
GETCP_DOC_RE = re.compile(r'    """暴露 checkpointer 供 API 层调用[^\n]*"""')

# 改完后可能变成"未使用"的 import：(整行, 判定是否仍在用的正则列表)
MAYBE_UNUSED = [
    ("import os", [r"\bos\."]),
    ("from core import config", [r"\bconfig\."]),
    ("from langgraph.checkpoint.memory import MemorySaver",
     [r"MemorySaver\s*[\(\)\[\],]", r"->\s*MemorySaver", r"=\s*MemorySaver\b"]),
]


def find_target():
    if len(sys.argv) > 1:
        return sys.argv[1]
    here = os.getcwd()
    for c in (os.path.join(here, "agent", "builder.py"),
              "/root/agent/builder.py",
              "/root/baomiagent/agent/builder.py"):
        if os.path.isfile(c):
            return c
    for root in ("/root", here, "/opt", "/home", "/srv"):
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if d not in (".git", "node_modules", ".next", "venv",
                                        "__pycache__", ".venv", "dist")]
            if os.path.basename(dirpath) == "agent" and "builder.py" in filenames:
                return os.path.join(dirpath, "builder.py")
    return None


def _tidy_import_header(text):
    """删掉 import 行后压掉留下的多余空行（只针对 import 前的连续空行）。"""
    return re.sub(r"\n{3,}(?=(?:from|import) )", "\n\n", text)


def main():
    path = find_target()
    if not path:
        print("x 没找到 agent/builder.py")
        print("  请显式指定：python3 patch_checkpointer.py /root/agent/builder.py")
        return 1
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        print("x 目标文件不存在：", path)
        return 1

    print("目标文件：", path)
    with open(path, encoding="utf-8", newline="") as f:
        s = f.read()

    if MARK in s:
        print("= 已经打过这个补丁了（检测到标记），跳过。")
        return 0
    if START not in s or END not in s:
        print("x 文件结构和预期不一致，未做任何改动。")
        print("  缺少标记：", [x for x in (START, END) if x not in s])
        return 1

    # ---- 1) 整段替换 _make_checkpointer ----
    i = s.index(START)
    j = s.index(END, i) + len(END)
    new_s = s[:i] + NEW_FUNC + s[j:]

    # ---- 2) 修 get_checkpointer 的返回注解与说明 ----
    if GETCP_ANNOT in new_s:
        new_s = new_s.replace(GETCP_ANNOT, "def get_checkpointer():", 1)
    else:
        print("! 没找到 get_checkpointer 的注解行，已跳过这一处")
    new_s, n = GETCP_DOC_RE.subn(NEW_GETCP_DOC, new_s, count=1)
    if n == 0:
        print("! 没找到 get_checkpointer 的说明行，已跳过这一处")

    # ---- 3) 删掉因此不再使用的 import（每个都先确认真的没在用）----
    dropped = []
    for line, pats in MAYBE_UNUSED:
        if (line + "\n") not in new_s:
            continue
        body = new_s.replace(line + "\n", "", 1)
        if any(re.search(p, body) for p in pats):
            continue
        new_s = body
        dropped.append(line)
    if dropped:
        new_s = _tidy_import_header(new_s)

    # ---- 4) 备份 + 写入 + 编译自检（失败自动还原）----
    bak = path + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(path, bak)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(new_s)
    try:
        py_compile.compile(path, doraise=True)
    except Exception as e:
        shutil.copy2(bak, path)
        print("x 编译自检失败，已还原原文件：", e)
        return 1

    print("+ 已替换 _make_checkpointer()（改为返回 None）")
    for d in dropped:
        print("+ 已删除不再使用的 import：", d)
    print("+ py_compile 自检通过")
    print("+ 备份：", bak)
    print()
    print("=> 现在重启后端：bash deploy.sh restart")
    print("   想还原：cp", bak, path, "&& bash deploy.sh restart")
    return 0


sys.exit(main())
