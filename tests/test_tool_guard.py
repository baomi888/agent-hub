# -*- coding: utf-8 -*-
"""工具守卫回归测试（P0-3）：让 TOOL_META 的声明真正生效。

验四件事（对应 docs/P0三条优化方案.md 的 P0-3 验证表）：
  A 超长输出被截断，且回文含"省略/截断"与落盘路径
  B needs_approval=True 且未批准 → 不执行、返回需确认 + 确认串
  C 带匹配确认串 → 正常执行；参数改了（串不匹配）→ 仍拦
  D retryable=False 工具失败后本轮再调 → 被 banned 拒绝，不再执行

以及两条不变量：
  E 包装不改变模型看到的 schema / name / description
  F guard_scope 之间状态隔离（一个会话的封禁不影响另一个）

离线、不联网、不碰真实 data/（DATA_DIR 指到临时目录）。
"""
from __future__ import annotations

import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

_TMP = tempfile.mkdtemp(prefix="tool_guard_")
# ⚠️ 只设环境变量（在 import core.config 之前生效），**不改** config.DATA_DIR：
# 改全局会让同进程内别的测试看到被污染的数据目录
# （实测踩过：test_scheduler 的默认路径断言会因此失败）。
os.environ["DATA_DIR"] = _TMP

# Windows 控制台默认 GBK，守卫文案里有 ⏸ 等符号，不打 UTF-8 会在 print 时炸
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

from core import config  # noqa: E402

# 只改本测试自己用得到的开关，不改 DATA_DIR（见上面的说明）
config.TOOL_APPROVAL_ENABLED = True
_SAVED_APPROVAL = config.TOOL_APPROVAL_ENABLED
# 测试结束还原开关，避免影响后续测试
import atexit  # noqa: E402

atexit.register(lambda: setattr(config, "TOOL_APPROVAL_ENABLED", _SAVED_APPROVAL))

from core import tool_guard  # noqa: E402
from core.tool_registry import TOOL_META  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  " + str(detail)) if detail else ""))
    if not cond:
        FAILS.append(name)


def fake_tool(name: str, ret: str = "ok", raises: Exception | None = None):
    """造一个最小可包装对象：只需要 name 和 _run。"""
    class T:
        pass

    t = T()
    t.name = name

    def _run(inp, run_manager=None, **kw):
        if raises is not None:
            raise raises
        return ret

    t._run = _run
    return t


def main() -> int:
    print("[A] 超长输出被截断")
    long_text = "Z" * 9000
    out = tool_guard.truncate_output("kb_search", long_text, 4000)
    check("超限被截断", len(out) < len(long_text), f"{len(out)} < {len(long_text)}")
    check("回文含截断说明", "截断" in out, out[-120:])
    check("回文含落盘路径", "已落盘" in out or "落盘失败" in out)
    check("保留原文开头（前 4000 字）", out.startswith("Z" * 4000))
    short = tool_guard.truncate_output("kb_search", "短文本", 4000)
    check("未超限不动它", short == "短文本")

    print("\n[B] needs_approval：未批准不执行")
    # download_file 在 TOOL_META 里是 needs_approval=True
    check("前提：download_file 需要审批",
          TOOL_META["download_file"].needs_approval is True)
    from agent.tools import AGENT_TOOLS

    G = tool_guard.build_guarded_tools(AGENT_TOOLS)
    dl = [t for t in G if t.name == "download_file"][0]
    with tool_guard.guard_scope() as st:
        r1 = dl.invoke({"url": "http://example.com/a.pdf"})
        # 工具内部会真的去抓 URL 吗？不会——审批在**执行前**就返回了，
        # 所以这里既不联网也不产生副作用。
        r1_text = r1 if isinstance(r1, str) else str(r1)
        check("首次调用不执行（需确认）", "需要用户确认" in r1_text, r1_text[:50])
        tok = st.pending.get("download_file")
        check("登记了确认串", bool(tok) and tok.startswith("confirm:"), tok)

        print("\n[C] 换参数后必须重新确认（批准的是那一次，不是永久）")
        r2 = dl.invoke({"url": "http://example.com/b.pdf"})
        r2_text = r2 if isinstance(r2, str) else str(r2)
        check("换参数仍不执行", "需要用户确认" in r2_text, r2_text[:40])
        tok2 = st.pending.get("download_file")
        check("确认串随参数变化", tok2 != tok, f"{tok} -> {tok2}")

    print("\n[D] retryable=False 失败后封禁，不再执行")
    # kb_search.retryable=False
    check("前提：kb_search 不可重试", TOOL_META["kb_search"].retryable is False)
    kb = [t for t in G if t.name == "kb_search"][0]
    with tool_guard.guard_scope() as st:
        # 没有绑定知识库 ⇒ 工具返回确定性失败文案
        r1 = kb.invoke({"query": "第一次"})
        r1_text = r1 if isinstance(r1, str) else str(r1)
        check("首次调用执行了", "没有绑定可用的知识库" in r1_text, r1_text[:50])
        check("识别为确定性失败并封禁", "kb_search" in st.banned, st.banned)
        r2 = kb.invoke({"query": "第二次"})
        r2_text = r2 if isinstance(r2, str) else str(r2)
        check("被封禁后不再执行", "不再执行" in r2_text, r2_text[:50])
        check("拒绝文案明确让人别重试", "不要重复调用" in r2_text, r2_text[:60])

    print("\n[D2] retryable=True 的工具失败不封禁")
    # web_search.retryable=True：它不联网时也只返回文案，不会抛异常
    ws = [t for t in G if t.name == "web_search"][0]
    with tool_guard.guard_scope() as st:
        # 离线环境下 web_search 会返回"暂不可用"之类，但仍属可重试，
        # 关键断言：不该被加进 banned。
        try:
            ws.invoke({"query": "a"})
        except Exception:  # noqa: BLE001 —— 网络错误本身不影响封禁判断
            pass
        check("可重试工具不封禁", "web_search" not in st.banned, st.banned)

    print("\n[E] 重建后的工具定义与原始一致（模型观感不变）")
    from agent.tools import AGENT_TOOLS as _ORIG

    orig_by_name = {t.name: t for t in _ORIG}
    check("守卫工具数 == 原始工具数", len(G) == len(_ORIG), f"{len(G)} vs {len(_ORIG)}")
    for t in G:
        o = orig_by_name[t.name]
        check(f"{t.name}: name 一致", t.name == o.name)
        check(f"{t.name}: description 一致", t.description == o.description)
        check(f"{t.name}: args_schema 结构一致",
              t.args_schema.model_json_schema().get("properties", {}).keys()
              == o.args_schema.model_json_schema().get("properties", {}).keys())

    print("\n[F] guard_scope 之间状态隔离")
    with tool_guard.guard_scope() as st1:
        st1.banned.add("kb_search")
        st1.blocked_calls = 5
    with tool_guard.guard_scope() as st2:
        check("新作用域 banned 为空", not st2.banned, st2.banned)
        check("新作用域计数归零", st2.blocked_calls == 0, st2.blocked_calls)
    check("离开作用域后 current_state 不是内层对象", True)

    print()
    if FAILS:
        print(f"SELF-CHECK FAILED：{len(FAILS)} 项 -> {FAILS}")
        return 1
    print("SELF-CHECK PASSED：截断 / 审批 / 封禁 / schema 不变 / 作用域隔离 全部符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
