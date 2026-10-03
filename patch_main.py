# -*- coding: utf-8 -*-
"""服务器侧补丁 —— 修掉「页面能打开，但新建会话 / 发消息全部失败」。

现象
    浏览器打开 http://<公网IP>:3000 页面正常，但点「新建会话」报失败、
    发消息没有回复；而在服务器上 curl 后端 /health 又是 200。

根因
    前端 Next.js 用 rewrites 把 /api/* 代理到后端。前端调的是 /api/sessions/，
    Next.js 默认「去尾斜杠」先 308 到 /api/sessions；FastAPI 又默认「补尾斜杠」，
    把 /api/sessions 307 回 /api/sessions/ —— 致命的是这条重定向的 Location 指向
    http://localhost:8000/...（Next 代理把 Host 改成了 localhost:8000）。
    浏览器照做就是去连它自己那台机器的 8000 端口，必然失败，fetch 直接挂掉。
    所以不是网络问题、不是 IP 问题、也不是 API Key 问题。

本脚本只改后端 main.py，两处改动
    1) app = FastAPI(redirect_slashes=False, ...)   关掉 FastAPI 自己的斜杠重定向
    2) 在 include_router 之后插入中间件 normalize_api_slash
       进路由前把路径归一到「已注册的那一种」，两种写法都能命中

用法（在服务器上，项目根目录 = deploy.sh 所在目录，本项目实测是 /root）
    cd /root
    python3 patch_main.py                  # 自动寻找 main.py
    python3 patch_main.py /root/main.py    # 也可显式指定
    bash deploy.sh restart                 # 只需重启后端，不用重建前端

安全保证
    · 幂等：已打过的地方自动跳过，重复运行不会有副作用
    · 改动前自动备份为 main.py.bak-<时间戳>
    · 改完立刻 py_compile 自检；编译不过会自动回滚到备份
"""

import os
import py_compile
import re
import shutil
import sys
import time

# 要插入的中间件块（与开发机 main.py 中的实现逐字一致）
_BLOCK = '''# ---------- 修复：前后端尾部斜杠不一致导致的重定向黑洞 ----------
# 症状：页面能打开，但「新建会话 / 发消息 / 所有 /api/* 调用」全部失败。
# 成因：前端走 Next.js rewrite 把 /api/* 代理到后端。前端调的是 /api/sessions/，
#       而 Next.js 默认「去尾斜杠」会先 308 到 /api/sessions；FastAPI 又默认
#       「补尾斜杠」，把 /api/sessions 307 回 /api/sessions/ —— 致命的是这条
#       重定向的 Location 指向 http://localhost:8000/...（Next 代理把 Host 改成了
#       localhost:8000）。浏览器照做就是去连它自己那台机器的 8000 端口，必然失败，
#       fetch 直接挂掉 → 前端报「新建会话失败」。
# 本项目前端发起的路径与后端路由的斜杠约定其实完全一致（有斜杠的对有斜杠、无斜杠的
# 对无斜杠）。所以只要后端不再自作主张重定向（redirect_slashes=False），并在进入
# 路由前把路径归一到「已注册的那种」，无论 Next 是否先去尾斜杠，后端都能直接命中路由。
def _collect_api_paths(routes):
    """收集所有已注册路由的完整路径（含 /api 前缀）。

    注意：include_router 进来的路由是 _IncludedRouter，其 .path 为 None，
    真实 APIRoute 收在 effective_route_contexts() 里、path 是相对前缀的，
    必须用 route_path 才能拿到带前缀的完整路径。
    """
    acc: set[str] = set()

    def walk(rs):
        for r in rs:
            p = getattr(r, "route_path", None) or getattr(r, "path", None)
            if p:
                acc.add(p)
            erc = getattr(r, "effective_route_contexts", None)
            if callable(erc):
                try:
                    for ctx in erc():
                        cp = getattr(ctx, "route_path", None) or getattr(ctx, "path", None)
                        if cp:
                            acc.add(cp)
                except Exception:
                    pass
            sub = getattr(r, "routes", None)
            if sub is not None and sub is not rs:
                walk(sub)

    walk(routes)
    return acc


def _exact_matches(template: str, path: str) -> bool:
    """路径是否精确命中模板（尾斜杠严格、{param} 视为通配）。"""
    ts = template.split("/")
    ps = path.split("/")
    if len(ts) != len(ps):
        return False
    for a, b in zip(ts, ps):
        if a == b or (a.startswith("{") and a.endswith("}")):
            continue
        return False
    return True


@app.middleware("http")
async def normalize_api_slash(request, call_next):
    path = request.scope.get("path", "")
    if path.startswith("/api/") and path != "/api/":
        has_slash = path.endswith("/")
        alt = path[:-1] if has_slash else path + "/"
        # 把已注册路径集合缓存到 app.state，避免每次请求重建
        cache = getattr(request.app.state, "api_paths", None)
        if cache is None:
            cache = _collect_api_paths(request.app.routes)
            request.app.state.api_paths = cache

        def matches_any(p: str) -> bool:
            return any(_exact_matches(t, p) for t in cache)

        # 当前形式命中不了、但翻一个尾斜杠就能命中时，归一到能命中的那一种
        if not matches_any(path) and matches_any(alt):
            request.scope["path"] = alt
            if request.scope.get("raw_path") is not None:
                request.scope["raw_path"] = alt.encode()
    return await call_next(request)'''

_MARKERS = ("app = FastAPI(", "include_router")


def _looks_like_backend(path):
    """粗判这个文件是不是本项目的后端入口。"""
    try:
        s = open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        return False
    return all(m in s for m in _MARKERS)


def find_main():
    """按「命令行参数 -> 当前目录 -> 常见位置 -> 往下找一层」的顺序定位 main.py。"""
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        return os.path.abspath(sys.argv[1])

    here = os.getcwd()
    for c in (
        os.path.join(here, "main.py"),
        os.path.join(here, "baomiagent", "main.py"),
        "/root/baomiagent/main.py",
        "/root/main.py",
    ):
        if os.path.isfile(c) and _looks_like_backend(c):
            return c

    # 兜底：在 /root 与当前目录下找一层子目录
    for root in ("/root", here):
        if not os.path.isdir(root):
            continue
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            continue
        for name in entries:
            cand = os.path.join(root, name, "main.py")
            if os.path.isfile(cand) and _looks_like_backend(cand):
                return cand
    return None


def main():
    path = find_main()
    if not path:
        print("× 没找到后端入口 main.py")
        print("  请显式指定：python3 patch_main.py /root/main.py")
        return 2

    print("目标文件：" + path)

    if not _looks_like_backend(path):
        print("× 这个文件不像本项目后端（缺少 app = FastAPI( 或 include_router）")
        print("  如果确定要用它，请显式指定路径。")
        return 2

    src = open(path, encoding="utf-8").read()
    new = src
    steps = []

    # ---- 改动 1：关掉 FastAPI 的自动斜杠重定向 ----
    if "redirect_slashes" in new:
        steps.append("跳过：redirect_slashes 已经设置过了")
    elif "app = FastAPI(" in new:
        new = new.replace("app = FastAPI(", "app = FastAPI(\n    redirect_slashes=False,", 1)
        steps.append("已加 redirect_slashes=False")
    else:
        print("× 找不到 `app = FastAPI(`，无法改动。")
        return 2

    # ---- 改动 2：插入斜杠归一化中间件（放在最后一行 include_router 之后） ----
    if "normalize_api_slash" in new:
        steps.append("跳过：中间件 normalize_api_slash 已经存在")
    else:
        hits = list(re.finditer(r"(?m)^app\.include_router\(.*\)[ \t]*$", new))
        if not hits:
            print("× 找不到 `app.include_router(...)`，无法确定中间件插入位置。")
            return 2
        at = hits[-1].end()
        new = new[:at] + "\n\n" + _BLOCK.strip("\n") + "\n" + new[at:]
        steps.append("已插入中间件 normalize_api_slash")

    for s in steps:
        print("  · " + s)

    if new == src:
        print("\n== 已经是打过补丁的状态，文件未改动 ==")
        print("   直接执行：bash deploy.sh restart")
        return 0

    bak = path + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
    try:
        shutil.copy2(path, bak)
        print("  · 原文件已备份：" + bak)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new)
    except OSError as e:
        print("× 写入失败，文件未改动：" + str(e))
        return 1

    # ---- 编译自检，失败就回滚 ----
    try:
        py_compile.compile(path, doraise=True)
    except py_compile.PyCompileError as e:
        shutil.copy2(bak, path)
        print("× 改完编译不过，已自动回滚到备份。错误如下：")
        print(str(e))
        return 1
    print("  · py_compile 自检通过")

    print("\npatched OK")
    print("下一步：  bash deploy.sh restart        # 只重启后端；前端不用重建")
    print("自检：    for u in /api/sessions /api/sessions/; do curl -s -o /dev/null \\")
    print("            -w \"$u -> %{http_code}\\n\" http://127.0.0.1:3000$u; done")
    print("          两条都应该是 200")
    return 0


if __name__ == "__main__":
    sys.exit(main())
