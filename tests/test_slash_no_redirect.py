# -*- coding: utf-8 -*-
"""尾部斜杠重定向回归测试（不联网、不需要知识库）。

运行：python tests/test_slash_no_redirect.py

背景：线上曾出现「页面能打开，但新建会话 / 发消息全部失败」。根因是
Next.js 默认「去尾斜杠」把 /api/sessions/ 308 成 /api/sessions，而 FastAPI
默认「补尾斜杠」又 307 回 /api/sessions/，且这条重定向的 Location 指向
http://localhost:8000/...（代理把 Host 改成了 localhost:8000），浏览器照做就去连
自己那台机器的 8000 端口，必然失败。

修法（见 main.py）：redirect_slashes=False + 中间件 normalize_api_slash。
本测试守住两条底线：
  1) 任何 /api/* 请求都不允许返回 3xx（否则又会把浏览器带偏）
  2) 带尾斜杠与不带尾斜杠两种写法都要能命中已注册的路由
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-dummy-for-import")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main as m  # noqa: E402
from api.sessions import router as sessions_router  # noqa: E402

REDIRECTS = (301, 302, 303, 307, 308)
FAILS = []


def check(cond, msg):
    if cond:
        print("  ✔ " + msg)
    else:
        print("  ✘ " + msg)
        FAILS.append(msg)


def concrete(template):
    """把 /api/kb/{kb_id}/files 变成 /api/kb/x/files 便于发请求。"""
    return "/".join(
        "x" if s.startswith("{") and s.endswith("}") else s for s in template.split("/")
    )


print("=== 0. 修复前的行为（说明为什么要这么修） ===")
bare = FastAPI()  # redirect_slashes 默认 True
bare.include_router(sessions_router, prefix="/api/sessions")
with TestClient(bare) as c:
    r = c.get("/api/sessions", follow_redirects=False)
print(f"  默认 FastAPI  GET /api/sessions -> {r.status_code}  Location={r.headers.get('location')}")
check(r.status_code in REDIRECTS, "默认 FastAPI 确实会重定向（这就是病根）")

print("\n=== 1. main.py 里修复代码都在 ===")
src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"),
           encoding="utf-8").read()
check("redirect_slashes=False" in src, "已设置 redirect_slashes=False")
check("normalize_api_slash" in src, "已挂上 normalize_api_slash 中间件")

print("\n=== 2. 全量扫：任何 /api/* 都不许 3xx ===")
routes = sorted(p for p in m._collect_api_paths(m.app.routes) if p.startswith("/api"))
print(f"  已注册 /api 路由 {len(routes)} 条")
probes = 0
bad = []
with TestClient(m.app) as c:
    for t in routes:
        real = concrete(t)
        for form in sorted({real, real[:-1] if real.endswith("/") else real + "/"}):
            for meth in ("GET", "POST"):
                probes += 1
                r = c.request(meth, form, follow_redirects=False)
                if r.status_code in REDIRECTS:
                    bad.append((meth, form, r.status_code, r.headers.get("location")))
check(not bad, f"{probes} 次探测中 0 次重定向（实际 {len(bad)} 次）")
for b in bad[:10]:
    print("      ", b)

print("\n=== 3. 前端真正会调的几条路径 ===")
with TestClient(m.app) as c:
    for meth, path in (("GET", "/api/sessions/"), ("GET", "/api/sessions"),
                       ("POST", "/api/sessions/"), ("GET", "/api/kb")):
        r = c.request(meth, path, follow_redirects=False)
        ok = r.status_code in (200, 422)
        print(f"  {'✔' if ok else '✘'} {meth:5} {path:20} -> {r.status_code} "
              f"Location={r.headers.get('location')}")
        if not ok:
            FAILS.append(f"{meth} {path} -> {r.status_code}")

print()
if FAILS:
    print(f"结果：{len(FAILS)} 项失败 ✘")
    for f in FAILS:
        print("  - " + f)
    sys.exit(1)
print("结果：全部通过 ✔")
