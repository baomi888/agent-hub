# -*- coding: utf-8 -*-
"""离线自测：工具注册表 —— 执行元数据 + 延迟发现（工单 03）。

不联网、不碰真实数据：临时目录 + FastAPI TestClient。

验的是五件事：
  1. `TOOL_META` 的键集合与 `AGENT_TOOLS` 完全一致；漏配必须**抛错**（宁可起不来）
  2. 现有 5 个工具**全部是 defer=False 的直接工具**——延迟机制是留给未来定时类工具的，
     不是用来"优化"现在这 5 个的
  3. `kb_search.retryable=False`（库不存在是确定性失败，不该诱导模型原地重试）、
     `download_file` 是 write + 需要审批
  4. 延迟发现：`select:` 精确激活、关键词模糊命中、未命中时抛出**带候选名单**的异常
  5. 两个端点的真实 HTTP 行为（鉴权、未命中 404 的响应体、400 空 query）
"""

import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="baomi_toolreg_")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import core.config  # noqa: E402

core.config.PROJECT_ROOT = os.path.join(_TMP, "proj")
core.config.PERSIST_DIR = os.path.join(_TMP, "proj", "chroma_db")
core.config.DATA_DIR = os.path.join(_TMP, "proj", "data")
core.config.DOWNLOAD_DIR = os.path.join(_TMP, "proj", "downloads")
os.makedirs(os.path.join(core.config.PROJECT_ROOT, "data"), exist_ok=True)

import core.session  # noqa: E402

core.session._db_path = lambda: os.path.join(_TMP, "sessions.db")
core.session._init_db()

from core import tool_registry as tr  # noqa: E402

FAILED = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  " + str(detail)) if detail else ""))
    if not cond:
        FAILED.append(name)


print("\n[1] 元数据与已注册工具一一对应")
tr.assert_meta_complete()
check("assert_meta_complete 通过", True)
check("键集合 == 工具名集合", set(tr.TOOL_META) == set(tr.tool_names()),
      f"{sorted(tr.TOOL_META)} vs {sorted(tr.tool_names())}")
check("工具数 = 5", len(tr.tool_names()) == 5, len(tr.tool_names()))

print("\n[2] 现有工具全是直接工具（defer=False）")
metas = {m["name"]: m for m in tr.all_meta()}
check("5 个工具 defer 全为 False", all(m["defer"] is False for m in metas.values()),
      {n: m["defer"] for n, m in metas.items()})
check("deferred_names() 为空", tr.deferred_names() == [], tr.deferred_names())
check("每个工具都有完整元数据字段",
      all(set(m) >= {"name", "description", "category", "defer", "retryable",
                     "needs_approval", "max_output_chars", "cost_hint", "discovered"}
          for m in metas.values()))

print("\n[3] 关键工具的语义正确")
check("kb_search.retryable=False（确定性失败）", metas["kb_search"]["retryable"] is False)
check("kb_search 是 read", metas["kb_search"]["category"] == "read")
check("download_file 是 write", metas["download_file"]["category"] == "write")
check("download_file 需要审批", metas["download_file"]["needs_approval"] is True)
check("download_file 成本档 expensive", metas["download_file"]["cost_hint"] == "expensive")
check("calculator 不需要联网也能算", metas["calculator"]["cost_hint"] == "cheap")

print("\n[4] 元数据校验：漏配/多配都要抛错")
_saved = dict(tr.TOOL_META)
try:
    tr.TOOL_META.pop("calculator")
    try:
        tr.assert_meta_complete()
        check("漏配一个工具 -> 抛错", False, "竟然没抛")
    except RuntimeError as e:
        check("漏配一个工具 -> 抛错", "calculator" in str(e), str(e)[:90])
finally:
    tr.TOOL_META.clear()
    tr.TOOL_META.update(_saved)

try:
    from dataclasses import replace

    tr.TOOL_META["__not_a_real_tool__"] = replace(_saved["calculator"])
    try:
        tr.assert_meta_complete()
        check("多配一个工具 -> 抛错", False, "竟然没抛")
    except RuntimeError as e:
        check("多配一个工具 -> 抛错", "not_a_real_tool" in str(e), str(e)[:90])
finally:
    tr.TOOL_META.pop("__not_a_real_tool__", None)
tr.assert_meta_complete()

print("\n[5] 非法元数据在构造时就挡住")
for bad_kwargs, label in (
    ({"category": "teleport"}, "未知 category"),
    ({"category": "read", "cost_hint": "free"}, "未知 cost_hint"),
    ({"category": "read", "max_output_chars": 0}, "非正数 max_output_chars"),
):
    try:
        tr.ToolMeta(**bad_kwargs)
        check(f"{label} -> 抛错", False, "竟然没抛")
    except ValueError:
        check(f"{label} -> 抛错", True)

print("\n[6] 延迟发现：把现有工具临时标记为 defer 来验代码路径")
# 现有工具都是 defer=False，延迟那条路平时走不到；这里临时改元数据把它跑起来。
_orig = tr.TOOL_META["get_weather"]
tr.TOOL_META["get_weather"] = tr.ToolMeta(category="read", defer=True, max_output_chars=2000)
tr.reset_activated()
try:
    check("deferred_names() 现在含 get_weather", tr.deferred_names() == ["get_weather"], tr.deferred_names())
    check("未激活时 discovered=False",
          [m for m in tr.all_meta() if m["name"] == "get_weather"][0]["discovered"] is False)

    names, schemas = tr.discover("select:get_weather")
    check("select: 精确激活命中", names == ["get_weather"], names)
    check("返回了 schema 且含 input_schema", bool(schemas) and "input_schema" in schemas[0])
    check("激活后 discovered=True",
          [m for m in tr.all_meta() if m["name"] == "get_weather"][0]["discovered"] is True)
    # 语义分清：deferred_names() = 所有 defer 工具（含已激活），用于"你能激活哪些"；
    # 而"尚未发现"要看 discovered 字段 / list_meta(deferred=True)。
    check("deferred_names() 仍列出该工具（它是 defer 工具）", tr.deferred_names() == ["get_weather"],
          tr.deferred_names())
    check("但已激活 -> 不在待发现列表",
          tr.list_meta(deferred=True) == [], tr.list_meta(deferred=True))

    tr.reset_activated()
    names2, _ = tr.discover("weather")
    check("关键词模糊命中（大小写不敏感）", names2 == ["get_weather"], names2)

    tr.reset_activated()
    names3, _ = tr.discover("WEATHER")
    check("大写关键词也命中", names3 == ["get_weather"], names3)

    tr.reset_activated()
    try:
        tr.discover("完全不存在的工具")
        check("未命中 -> NoMatchError", False, "竟然没抛")
    except tr.NoMatchError as e:
        check("未命中 -> NoMatchError", True)
        check("异常里带候选名单", e.candidates == ["get_weather"], e.candidates)

    try:
        tr.discover("")
        check("空 query -> NoMatchError", False, "竟然没抛")
    except tr.NoMatchError:
        check("空 query -> NoMatchError", True)

    # 直接工具不该被搜索到（只搜延迟工具）
    tr.reset_activated()
    try:
        tr.discover("calculator")
        check("直接工具不进延迟搜索", False, "竟然命中了 calculator")
    except tr.NoMatchError:
        check("直接工具不进延迟搜索", True)
finally:
    tr.TOOL_META["get_weather"] = _orig
    tr.reset_activated()
tr.assert_meta_complete()

print("\n[7] HTTP 端点：走真实路由（TestClient）")
try:
    from fastapi.testclient import TestClient

    import main as appmod

    client = TestClient(appmod.app, raise_server_exceptions=False)

    r = client.get("/api/agent/tools")
    check("未登录 GET /api/agent/tools -> 401", r.status_code == 401, r.status_code)

    # 造一个账号拿到 Cookie
    import core.account as account

    uname = "toolreg_user"
    try:
        account.create_user(uname, "test123456")
    except Exception:
        pass
    from core.auth import create_token

    uid = account.get_user_by_name(uname)["id"] if hasattr(account, "get_user_by_name") else None
    if uid is None:
        # 兼容没有 get_user_by_name 的情况：从 users 表直接取
        with core.session._get_conn() as conn:
            row = conn.execute("SELECT id FROM users WHERE username = ?", (uname,)).fetchone()
            uid = row["id"] if row else None
    cookie = {"baomi_session": create_token(uid)}

    r = client.get("/api/agent/tools", cookies=cookie)
    check("已登录 GET /api/agent/tools -> 200", r.status_code == 200, r.status_code)
    body = r.json()
    check("返回 5 个工具", body.get("total") == 5, body.get("total"))
    check("全部 defer=false", all(i["defer"] is False for i in body["items"]))
    check("kb_search.retryable=false",
          [i for i in body["items"] if i["name"] == "kb_search"][0]["retryable"] is False)

    r = client.get("/api/agent/tools?q=search", cookies=cookie)
    check("q=search 过滤命中 2 个", r.json().get("total") == 2, r.json().get("total"))

    r = client.get("/api/agent/tools?deferred=true", cookies=cookie)
    check("deferred=true 时为空（现有全是直接工具）", r.json().get("total") == 0, r.json().get("total"))

    r = client.post("/api/agent/tools/discover", json={"query": ""}, cookies=cookie)
    check("空 query -> 400", r.status_code == 400, r.status_code)

    r = client.post("/api/agent/tools/discover", json={"query": "weather"}, cookies=cookie)
    check("未命中 -> 404", r.status_code == 404, r.status_code)
    check("404 响应体给出候选名单说明", "可激活的工具" in r.json().get("detail", ""),
          r.json().get("detail", "")[:80])

    r = client.post("/api/agent/tools/discover", json={"query": "select:get_weather"}, cookies=cookie)
    check("select: 精确激活 -> 404（因为 get_weather 是直接工具，不是延迟工具）",
          r.status_code == 404, r.status_code)
except Exception as e:  # TestClient 起不来时不要把整份自测拖垮
    check("TestClient 段可运行", False, f"{type(e).__name__}: {e}")

print()
if FAILED:
    print(f"SELF-CHECK FAILED：{len(FAILED)} 项 -> {FAILED}")
    sys.exit(1)
print("SELF-CHECK PASSED：工具注册表元数据 + 延迟发现 + 端点行为全部符合预期")
