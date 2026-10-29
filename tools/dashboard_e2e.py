"""数据看板端到端验收（起真 uvicorn 子进程，真登录，真打接口）。

与 `tests/test_dashboard.py` 的分工：
  单测用 TestClient + 临时库，验的是"代码分支"；
  本脚本起**真进程**，验的是"接线"——中间件有没有挂上、定时 flush 有没有跑、
  cookie 能不能带上、异常会不会被吞。上次做审计时，两个只有真进程才暴露的
  bug（路由模板是相对前缀、低流量不 flush）就是靠这一类脚本抓到的。

隔离：靠 `AGENT_HUB_DB` 指向临时库（core/session.py::_db_path 的测试缝），
**不会碰 data/sessions.db**。

用法：
    python tools/dashboard_e2e.py            # 跑完打印 PASS/FAIL，退出码即信号
    python tools/dashboard_e2e.py --keep     # 保留临时目录（排障）
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADMIN = "e2e_admin"
NORMAL = "e2e_user"
PASSWORD = "e2e-pass-123456"


class Reporter:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.rows.append((name, bool(ok), detail))

    def dump(self) -> int:
        width = max(len(n) for n, _, _ in self.rows) + 2
        for name, ok, detail in self.rows:
            print(f"[{'PASS' if ok else 'FAIL'}] {name:<{width}} {detail}")
        bad = [r for r in self.rows if not r[1]]
        print("=" * 78)
        print(f"结果：{len(self.rows) - len(bad)}/{len(self.rows)} 项通过")
        for name, _, detail in bad:
            print(f"  失败：{name}  {detail}")
        return 0 if not bad else 1


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _opener() -> urllib.request.OpenerDirector:
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def _http(
    op: urllib.request.OpenerDirector,
    method: str,
    url: str,
    body: dict | None = None,
    headers: dict | None = None,
) -> tuple[int, str]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("content-type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with op.open(req, timeout=20) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _wait_health(base: str, timeout: float = 60.0) -> bool:
    op = urllib.request.build_opener()
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            code, _ = _http(op, "GET", f"{base}/health")
            if code == 200:
                return True
        except OSError:
            pass
        time.sleep(0.5)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="保留临时目录")
    args = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix="dashboard_e2e_")
    db = os.path.join(tmp, "e2e.db")
    port = _free_port()
    base = f"http://127.0.0.1:{port}"

    env = os.environ.copy()
    env.update(
        {
            "AGENT_HUB_DB": db,
            "ADMIN_USERNAMES": ADMIN,
            "ALLOW_SIGNUP": "1",
            "RATE_LIMIT_SHARED": "0",
            "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    env.pop("DEEPSEEK_API_KEY", None)  # 不调模型，去掉真实 key 免得误打外部接口
    env.pop("EMBEDDING_API_KEY", None)  # RAG 预热同理：本脚本不碰知识库

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port),
         "--log-level", "warning"],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    rep = Reporter()
    try:
        rep.check("服务启动（/health 200）", _wait_health(base), base)
        anon = _opener()
        admin = _opener()
        normal = _opener()

        # ---------- 注册与登录 ----------
        code, body = _http(anon, "POST", f"{base}/api/auth/register/",
                           {"username": ADMIN, "password": PASSWORD})
        rep.check("管理员账号注册", code == 200, f"{code} {body[:60]}")
        code, body = _http(anon, "POST", f"{base}/api/auth/register/",
                           {"username": NORMAL, "password": PASSWORD})
        rep.check("普通账号注册", code == 200, f"{code} {body[:60]}")

        code, body = _http(admin, "POST", f"{base}/api/auth/login/",
                           {"username": ADMIN, "password": PASSWORD})
        rep.check("管理员登录（Cookie 下发）", code == 200, f"{code}")
        code, body = _http(normal, "POST", f"{base}/api/auth/login/",
                           {"username": NORMAL, "password": PASSWORD})
        rep.check("普通用户登录", code == 200, f"{code}")

        code, body = _http(admin, "GET", f"{base}/api/auth/me/")
        me = json.loads(body) if code == 200 else {}
        rep.check("/me 暴露 is_admin", bool(me.get("is_admin")), json.dumps(me)[:80])

        # ---------- 权限矩阵 ----------
        # 必须用一个**全新的** opener：注册接口会顺手下发 Cookie（注册即登录），
        # 上面那个 anon 已经带上了 e2e_user 的登录态，拿它测"未登录"会得到 404
        # （非管理员的响应被刻意伪装成"接口不存在"，见 api/admin.py::_require_admin）。
        fresh = _opener()
        code, _ = _http(fresh, "GET", f"{base}/api/admin/overview")
        rep.check("未登录 → 401", code == 401, str(code))
        code, body_normal = _http(normal, "GET", f"{base}/api/admin/overview")
        rep.check("非管理员 → 404（伪装成不存在，不是 403）", code == 404, str(code))
        code2, body_fake = _http(normal, "GET", f"{base}/api/admin/__not_a_route__")
        rep.check(
            "非管理员 404 与真不存在无法区分",
            code == code2 == 404 and body_normal == body_fake,
            f"{code}/{code2} {body_normal[:48]!r}",
        )
        code, _ = _http(admin, "GET", f"{base}/api/admin/overview")
        rep.check("管理员 → 200", code == 200, str(code))
        code, _ = _http(admin, "GET", f"{base}/api/admin/export?kind=users")
        rep.check("导出接口已下线（404）", code == 404, str(code))

        # ---------- 制造业务流量 ----------
        for _ in range(3):
            _http(admin, "GET", f"{base}/api/sessions/")
            _http(normal, "GET", f"{base}/api/auth/me/")
        _http(normal, "GET", f"{base}/api/does-not-exist-{int(time.time())}")

        time.sleep(4.5)  # 等定时 flush（3 秒一轮）

        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT path, method, status FROM api_call_logs").fetchall()
        paths = [r["path"] for r in rows]
        rep.check("真进程写入日志（≥6 条）", len(rows) >= 6, f"{len(rows)} 条")
        rep.check("/health 未入库", not any(p == "/health" for p in paths), str(paths[:4]))
        rep.check("/api/admin/* 未入库", not any(p.startswith("/api/admin") for p in paths), "")
        rep.check("业务接口已入库", any(p == "/api/auth/me/" for p in paths), str(sorted(set(paths))[:6]))

        # ---------- 看板数字 ----------
        code, body = _http(admin, "GET", f"{base}/api/admin/overview?days=1")
        ov = json.loads(body) if code == 200 else {}
        total = ov.get("calls", {}).get("total")
        rep.check("总览可解析", code == 200 and isinstance(total, int), f"{code} total={total}")
        rep.check("总览调用数 = 日志行数", total == len(rows), f"{total} vs {len(rows)}")
        rep.check("注册用户数 = 2", ov.get("users", {}).get("total") == 2,
                  str(ov.get("users")))
        rep.check("趋势按天返回", len(ov.get("series") or []) == 1,
                  str(len(ov.get("series") or [])))

        # 观察者效应：连续刷看板不应推高数字
        for _ in range(3):
            _http(admin, "GET", f"{base}/api/admin/overview?days=1")
        time.sleep(4.5)
        code, body = _http(admin, "GET", f"{base}/api/admin/overview?days=1")
        total2 = json.loads(body)["calls"]["total"]
        rep.check("刷新看板不产生统计（无观察者效应）", total2 == total, f"{total} -> {total2}")

        # ---------- 排行三档 ----------
        for sort in ("calls", "slow", "fail"):
            code, body = _http(admin, "GET", f"{base}/api/admin/apis?days=1&sort={sort}")
            js = json.loads(body) if code == 200 else {}
            rep.check(f"排行 sort={sort} 可用", code == 200 and js.get("sort") == sort,
                      f"{code} items={len(js.get('items') or [])}")
            if sort == "fail":
                bad = [x for x in js.get("items") or [] if x["failed"] > 0]
                rep.check("failed 档每条都真的失败过", all(x["failed"] > 0 for x in js["items"]),
                          f"{len(bad)} 条")
        code, body = _http(admin, "GET", f"{base}/api/admin/apis?days=1&sort=bogus")
        rep.check("非法 sort 回落 calls", json.loads(body).get("sort") == "calls", "")

        # ---------- 反馈闭环 ----------
        code, body = _http(normal, "GET", f"{base}/api/user-feedback/dict")
        d = json.loads(body) if code == 200 else {}
        rep.check("反馈字典可取", code == 200 and bool(d.get("categories")), f"{code}")
        code, body = _http(normal, "POST", f"{base}/api/user-feedback/",
                           {"category": d["categories"][0]["value"],
                            "content": "e2e 提交的反馈：看板数字要能对上"})
        fid = json.loads(body).get("id") if code == 200 else None
        rep.check("普通用户可提交反馈", code == 200 and fid, f"{code} id={fid}")
        code, body = _http(admin, "GET", f"{base}/api/admin/feedback")
        fb = json.loads(body) if code == 200 else {}
        rep.check("管理员能看到反馈", fb.get("total") == 1, str(fb.get("total")))
        code, _ = _http(admin, "PATCH", f"{base}/api/admin/feedback/{fid}", {"status": "done"})
        rep.check("状态流转 open→done", code == 200, str(code))
        code, _ = _http(admin, "PATCH", f"{base}/api/admin/feedback/{fid}", {"status": "doing"})
        rep.check("回退 done→doing 被 409 拦住", code == 409, str(code))
        code, body = _http(admin, "GET", f"{base}/api/admin/feedback?status=done")
        rep.check("按状态筛选生效", json.loads(body).get("total") == 1, "")

        # ---------- 服务端日志必须干净 ----------
        proc.terminate()
        try:
            out = proc.communicate(timeout=15)[0].decode("utf-8", "replace")
        except subprocess.TimeoutExpired:
            proc.kill()
            out = proc.communicate()[0].decode("utf-8", "replace")
        noisy = [
            ln for ln in out.splitlines()
            if ("Traceback" in ln or "never retrieved" in ln or "ERROR" in ln)
        ]
        rep.check("服务端无未捕获异常", not noisy, " / ".join(noisy[:2]))
    finally:
        if proc.poll() is None:
            proc.kill()
        if args.keep:
            print(f"临时目录：{tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    return rep.dump()


if __name__ == "__main__":
    raise SystemExit(main())
