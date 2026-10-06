# -*- coding: utf-8 -*-
"""离线自测：Agent 越权面收紧 + 配额限流 + SSRF 防护（多用户改造 P3 的验收点）。

不联网、不碰真实数据：临时目录 + 假 embedding + 打桩 DNS。

验的是四件事：
  1. 模型没法指定"查哪个库" —— kb_search / download_file 的 kb_id 只认
     服务端从 RunnableConfig 注入的那一份，塞别人的 kb_id 进不去
  2. 会话没绑库 / 没身份时，工具给出的是"确定性的、不诱导重试"的答复
  3. 配额：超限的那个请求**不会**继续把计数推高（否则第二天也解不开）
  4. SSRF：内网 / 回环 / 元数据地址一律拒，且重定向的下一跳也要重新验
"""

import os
import socket
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="baomi_guard_")
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

from kb import pipelines  # noqa: E402

# 假 embedding：离线、确定性，检索链路照常跑通（学自 test_web_import_creates_index）
from langchain_core.embeddings import DeterministicFakeEmbedding  # noqa: E402
import kb.rag_core  # noqa: E402

kb.rag_core.get_embeddings = lambda: DeterministicFakeEmbedding(size=32)

from agent.tools import _runtime, download_file, kb_search  # noqa: E402
from core import quota  # noqa: E402
from core.netsafe import UnsafeURLError, assert_safe_url  # noqa: E402

ALICE = "guard_alice"
BOB = "guard_bob"

PASS = []
FAIL = []


def check(name: str, cond: bool, extra: str = ""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  <- " + extra) if extra and not cond else ""))


def cfg_for(owner, kb_id):
    return {"configurable": {"thread_id": "s1", "owner_id": owner, "kb_id": kb_id}}


# ---------------------------------------------------------------- [1] 运行时身份
print("\n[1] kb_id 只认服务端注入的那一份")
check("_runtime 能取出 (owner, kb)",
      _runtime(cfg_for(ALICE, "kb_x")) == (ALICE, "kb_x"))
check("config 为 None 时返回 (None, None)", _runtime(None) == (None, None))
check("config 里没有 configurable 时返回 (None, None)", _runtime({}) == (None, None))

# 工具签名里不能再出现 kb_id：只要模型还能填这个参数，提示词注入就能改检索目标
import inspect  # noqa: E402

kb_params = inspect.signature(kb_search.func).parameters
dl_params = inspect.signature(download_file.func).parameters
check("kb_search 已不接受 kb_id 参数", "kb_id" not in kb_params, str(list(kb_params)))
check("download_file 已不接受 kb_id 参数", "kb_id" not in dl_params, str(list(dl_params)))
check("两个工具都保留了 config 注入口",
      "config" in kb_params and "config" in dl_params)

# ---------------------------------------------------------------- [2] 未绑定 / 越权
print("\n[2] 没绑库、以及绑了别人的库")
r = kb_search.invoke({"query": "苞米"}, config=cfg_for(None, None))
check("无身份 -> 明确告知没绑库", "没有绑定" in r, r)
check("无身份 -> 不诱导重试（指路 web_search）", "web_search" in r, r)

r = kb_search.invoke({"query": "苞米"}, config=cfg_for(ALICE, None))
check("有身份但没绑库 -> 同样挡住", "没有绑定" in r, r)

ka, _ = pipelines.create_kb(ALICE, "alice 的库")
kb, _ = pipelines.create_kb(BOB, "bob 的库")
check("两人的库 id 不同", ka != kb, f"{ka} vs {kb}")

# Bob 拿着 Alice 的 kb_id 塞进 configurable（模拟服务端被绕过 / 配置串号）
r = kb_search.invoke({"query": "苞米"}, config=cfg_for(BOB, ka))
check("Bob 用 Alice 的 kb_id -> 拿不到内容", "无法访问" in r or "没有绑定" in r, r)
check("被拒时给出确定结论（让模型别反复重试）", "不要重复调用" in r or "改用 web_search" in r, r)

# ---------------------------------------------------------------- [3] 配额
print("\n[3] 配额：超限不继续计数")
quota._HITS.clear()
saved_limit = core.config.RATE_PER_MINUTE
core.config.RATE_PER_MINUTE = 3
try:
    for _ in range(3):
        quota.hit("u_rate")
    try:
        quota.hit("u_rate")
        check("超过每分钟上限 -> 抛 RateLimited", False, "没抛")
    except quota.RateLimited as e:
        check("超过每分钟上限 -> 抛 RateLimited", True)
        check("RateLimited 带 retry_after", e.retry_after > 0)
finally:
    core.config.RATE_PER_MINUTE = saved_limit

saved_ask = core.config.DAILY_ASK_LIMIT
core.config.DAILY_ASK_LIMIT = 2
try:
    quota.consume("u_quota", "ask")
    quota.consume("u_quota", "ask")
    check("两次用满后当日计数 = 2", quota.usage_today("u_quota", "ask") == 2)
    try:
        quota.consume("u_quota", "ask")
        check("第三次 -> 抛 QuotaExceeded", False, "没抛")
    except quota.QuotaExceeded as e:
        check("第三次 -> 抛 QuotaExceeded", True)
        check("异常里带上 kind 与 limit", e.kind == "ask" and e.limit == 2)
    # 关键：超限时不能把计数继续往上推，否则用户第二天零点之前永远解不开
    check("超限请求不计数（仍是 2）", quota.usage_today("u_quota", "ask") == 2,
          str(quota.usage_today("u_quota", "ask")))
    try:
        quota.ensure("u_quota", "ask")
        check("ensure 在触顶时抛错", False, "没抛")
    except quota.QuotaExceeded:
        check("ensure 在触顶时抛错", True)
    check("ensure 只检查不记账（仍是 2）", quota.usage_today("u_quota", "ask") == 2)
    snap = quota.snapshot("u_quota")
    check("snapshot 给出 used/limit", snap["ask"] == {"used": 2, "limit": 2}, str(snap))
finally:
    core.config.DAILY_ASK_LIMIT = saved_ask

# ---------------------------------------------------------------- [4] SSRF
print("\n[4] SSRF：内网一律拒（DNS 用打桩，不联网）")


def _reject(url):
    try:
        assert_safe_url(url)
        return None
    except UnsafeURLError as e:
        return str(e)


for bad in (
    "http://127.0.0.1:8000/health",
    "http://localhost:3000/",
    "http://169.254.169.254/latest/meta-data/",   # 云元数据，SSRF 头号靶子
    "http://10.0.3.7/admin",
    "http://192.168.1.1/",
    "http://172.17.233.143/",                     # 本项目服务器的内网 IP
    "http://[::1]/",
    "http://0.0.0.0/",
    "file:///etc/passwd",
    "http://user:pass@example.com/",
    "http://metadata.google.internal/",
):
    check(f"拒绝 {bad}", _reject(bad) is not None)

check("缺主机名的 URL 被拒", _reject("http:///path") is not None)
check("空字符串被拒", _reject("") is not None)

_real_getaddrinfo = socket.getaddrinfo


def _fake_dns(host, port, *a, **kw):
    table = {
        "good.example.com": [(_, _, _, _, ("93.184.216.34", 0)) for _ in range(1)],
        "evil.example.com": [(_, _, _, _, ("127.0.0.1", 0)) for _ in range(1)],
        # DNS 返回多个 IP 时，只要有一个是内网就判危险 —— 只看第一个会被绕过
        "mix.example.com": [
            (socket.AF_INET, 0, 0, "", ("93.184.216.34", 0)),
            (socket.AF_INET, 0, 0, "", ("10.1.2.3", 0)),
        ],
    }
    if host in table:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", v[4]) for v in table[host]]
    raise socket.gaierror("no such host")


socket.getaddrinfo = _fake_dns
try:
    check("公网域名放行", assert_safe_url("https://good.example.com/a?b=1").endswith("/a?b=1"))
    check("解析到回环的域名被拒", _reject("http://evil.example.com/") is not None)
    check("多 IP 里含内网 -> 拒", _reject("http://mix.example.com/") is not None)
    check("解析失败的域名被拒", _reject("http://nope.example.com/") is not None)
finally:
    socket.getaddrinfo = _real_getaddrinfo

# ---------------------------------------------------------------- 汇总
print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    for f in FAIL:
        print("  FAILED:", f)
    sys.exit(1)
print("全部通过")
