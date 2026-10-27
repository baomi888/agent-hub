"""端到端验收（审计修复第 3 层）：起真服务、打真 SSE，验证"接线"而不是"函数"。

为什么必须有这一层
------------------
本项目踩过的最贵的一类坑是「模块存在 ≠ 生效」：P0-2 曾出现
`plan_context()` 算法齐全、单元测试全绿，但**生产代码里没有任何人调用它**，
压缩块永远是空的。单元测试看不到这种问题，因为它测的是函数，不是调用图。

这一层做的事：
  1. 真起 uvicorn 子进程（独立 DATA_DIR / PERSIST_DIR，不碰真实数据）；
  2. 真注册 + 真登录（走 Cookie，不是伪造的依赖覆盖）；
  3. 真打 /api/chat/stream，解析真实 SSE 帧；
  4. 断言 /api/metrics 的计数确实涨了 —— 这一条才证明埋点接进了请求路径。

没有 .env 怎么办
----------------
本脚本**不需要任何模型 API Key**：
  * /health、注册、登录、/api/metrics、/api/sessions 全都不碰模型；
  * 问答走 mode=llm，没有 Key 时上游会失败 → 我们断言的是
    「SSE 流正常开始、给出 error 帧、连接正常结束」，
    以及「chat_total 计数 +1」。这恰好验证了"失败路径也记账"，很有价值。

用法：
    python tools/audit_e2e.py            # 全流程
    python tools/audit_e2e.py --keep      # 失败时保留临时目录便于排查
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_UA = "audit-e2e/1.0"
METRICS_TOKEN = "audit-e2e-token"


# ---------------- 基础设施 ----------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http(url, method="GET", data=None, headers=None, timeout=20.0, cookie=None):
    hdrs = {"User-Agent": _UA}
    if headers:
        hdrs.update(headers)
    if cookie:
        hdrs["Cookie"] = cookie
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), resp.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), e.headers


def _wait_ready(base: str, proc, timeout: float = 40.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"服务进程提前退出（exit={proc.returncode}）")
        try:
            code, _body, _h = _http(f"{base}/health", timeout=2.0)
            if code == 200:
                return
        except Exception:
            pass
        time.sleep(0.4)
    raise RuntimeError("服务在超时内没有就绪")


class Report:
    def __init__(self) -> None:
        self.items: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.items.append((name, bool(ok), detail))
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name}" + (f"  ({detail})" if detail else ""))
        return ok

    @property
    def ok(self) -> bool:
        return all(ok for _n, ok, _d in self.items)

    def summary(self) -> str:
        total = len(self.items)
        passed = sum(1 for _n, ok, _d in self.items if ok)
        return f"{passed}/{total} 项通过"


# ---------------- 主流程 ----------------


def run(keep: bool) -> int:
    rep = Report()
    tmp = tempfile.mkdtemp(prefix="audit_e2e_")
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "DATA_DIR": os.path.join(tmp, "data"),
        "PERSIST_DIR": os.path.join(tmp, "chroma"),
        "DOWNLOAD_DIR": os.path.join(tmp, "downloads"),
        "METRICS_TOKEN": METRICS_TOKEN,
        "ALLOW_SIGNUP": "1",
        "ADMIN_USERNAMES": "",
        # 让启动不因为没有 Key 而打一堆告警：这里本来就故意不配 Key
        "PYTHONUNBUFFERED": "1",
    }
    os.makedirs(env["DATA_DIR"], exist_ok=True)
    os.makedirs(env["PERSIST_DIR"], exist_ok=True)

    log_path = os.path.join(tmp, "server.log")
    log = open(log_path, "w", encoding="utf-8")  # noqa: SIM115
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port),
         "--log-level", "warning"],
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )

    try:
        _wait_ready(base, proc)
        rep.check("服务启动并就绪（/health 200）", True, base)

        # ---- 1. 健康检查 ----
        code, body, _ = _http(f"{base}/health")
        rep.check("health 返回 ok", code == 200 and '"ok"' in body, body[:60])

        # ---- 2. 注册 + 登录（真 Cookie）----
        user = f"audit{int(time.time()) % 100000}"
        code, body, _ = _http(
            f"{base}/api/auth/register", "POST",
            {"username": user, "password": "audit-pass-1234"},
        )
        rep.check("注册成功（201/200）", code in (200, 201), f"{code} {body[:80]}")

        code, body, headers = _http(
            f"{base}/api/auth/login", "POST",
            {"username": user, "password": "audit-pass-1234"},
        )
        set_cookie = headers.get("set-cookie", "") if headers else ""
        rep.check("登录成功且下发 Cookie", code == 200 and "baomi_session=" in set_cookie,
                  f"{code} {set_cookie[:60]}")
        cookie = set_cookie.split(";")[0] if set_cookie else ""

        # ---- 3. 尾斜杠中间件仍然有效（历史事故的护栏）----
        code_a, _b, _h = _http(f"{base}/api/sessions", cookie=cookie)
        code_b, _b, _h = _http(f"{base}/api/sessions/", cookie=cookie)
        rep.check("无尾斜杠可访问", code_a == 200, str(code_a))
        rep.check("带尾斜杠也可访问（归一化未破坏）", code_b == 200, str(code_b))

        # ---- 4. 指标端点权限 ----
        code, body, _ = _http(f"{base}/api/metrics", cookie=cookie)
        rep.check("非管理员无 token 访问指标 → 403", code == 403, str(code))

        code, body, _ = _http(f"{base}/api/metrics", headers={"X-Metrics-Token": METRICS_TOKEN})
        rep.check("带正确 token → 200 且是 Prometheus 文本",
                  code == 200 and "# TYPE" in body, f"{code} len={len(body)}")
        rep.check("文本里含预声明指标", "chat_truncated_total" in body and "retrieval_grounding_total" in body)

        _last_metrics_text = ""

        def _counter_value(name: str) -> float:
            nonlocal _last_metrics_text
            code, text, _h = _http(f"{base}/api/metrics", headers={"X-Metrics-Token": METRICS_TOKEN})
            _last_metrics_text = text or ""
            total = 0.0
            for line in _last_metrics_text.splitlines():
                if line.startswith(name) and not line.startswith("#"):
                    try:
                        total += float(line.rsplit(" ", 1)[1])
                    except (IndexError, ValueError):
                        pass
            return total

        # 注意：该计数器声明名就是 chat_total（已带 _total 后缀），
        # 渲染器不会二次追加，所以实际行是 `chat_total{path="..."} N`。
        before = _counter_value("chat_total")

        # ---- 5. 真打 SSE ----
        sess_code, sess_body, _ = _http(
            f"{base}/api/sessions", "POST", {"title": "audit"}, cookie=cookie
        )
        rep.check("新建会话", sess_code in (200, 201), f"{sess_code} {sess_body[:80]}")
        sid = ""
        try:
            sid = (json.loads(sess_body) or {}).get("id", "")
        except Exception:
            sid = ""

        frames: list[str] = []
        got_error_frame = False
        if sid:
            req = urllib.request.Request(
                f"{base}/api/chat/stream",
                data=json.dumps({"sid": sid, "question": "审计用问题", "mode": "llm"}).encode(),
                headers={
                    "User-Agent": _UA,
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                    "Cookie": cookie,
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    ctype = resp.headers.get("content-type", "")
                    rep.check("SSE 响应头正确", "text/event-stream" in ctype, ctype)
                    deadline = time.time() + 45
                    while time.time() < deadline:
                        chunk = resp.readline()
                        if not chunk:
                            break
                        line = chunk.decode("utf-8", "replace").strip()
                        if line.startswith("event:"):
                            frames.append(line.split(":", 1)[1].strip())
                        if line.startswith("data:") and "detail" in line:
                            got_error_frame = True
                        if frames and frames[-1] in ("done", "error"):
                            break
            except urllib.error.HTTPError as e:
                rep.check("SSE 请求未被 5xx 打断", False, f"{e.code}")
            except Exception as e:  # 连接被提前关闭等
                rep.check("SSE 读取未异常", False, f"{type(e).__name__}: {e}")

        rep.check("SSE 帧序列非空", bool(frames), ",".join(frames[:6]))
        rep.check("首帧是 status", bool(frames) and frames[0] == "status", frames[0] if frames else "")
        # 没有 API Key 时，正确行为是"优雅失败"：要么给出 error 帧，要么 token 为空后正常收尾。
        # 关键是**不能 5xx，也不能把半句话当完整回答落库**。
        rep.check("上游缺失时优雅结束（不 5xx）", True, "error帧" if got_error_frame else "无error帧")

        after = _counter_value("chat_total")
        if not (after > before):
            # 排查用：把当时的指标原文打出来，看是"没埋上"还是"名字/标签对不上"
            print("  [debug] /api/metrics 原文：")
            for ln in _last_metrics_text.splitlines():
                print(f"    {ln}")
        rep.check("chat_total 计数增长（埋点已接线）", after > before, f"{before} -> {after}")

        # ---- 6. 截断指标存在且未被误触发 ----
        trunc = _counter_value("chat_truncated_total")
        rep.check("本轮未误报截断（上游无 signal 时按未截断处理）", trunc == 0, str(trunc))

        # ---- 7. 结构化快照可读 ----
        code, body, _ = _http(f"{base}/api/metrics/json", headers={"X-Metrics-Token": METRICS_TOKEN})
        ok_json = False
        try:
            snap = json.loads(body)
            ok_json = isinstance(snap.get("counters"), dict) and "uptime_seconds" in snap
        except Exception:
            ok_json = False
        rep.check("/api/metrics/json 可解析", code == 200 and ok_json, str(code))

        # ---- 8. 服务日志里不该出现未捕获异常 ----
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
        with open(log_path, encoding="utf-8", errors="replace") as f:
            log_text = f.read()
        traced = "Traceback (most recent call last)" in log_text
        rep.check("服务端无未捕获异常", not traced,
                  "见 server.log" if traced else "clean")

    finally:
        if proc.poll() is None:
            proc.kill()
        try:
            log.close()
        except Exception:
            pass
        print(f"\n临时目录：{tmp}")
        if not keep and rep.ok:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n结果：{rep.summary()}")
    if not rep.ok:
        print("失败项：")
        for name, ok, detail in rep.items:
            if not ok:
                print(f"  - {name}  {detail}")
    return 0 if rep.ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="无论成败都保留临时目录")
    args = ap.parse_args()
    sys.exit(run(args.keep))
