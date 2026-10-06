# -*- coding: utf-8 -*-
"""联网导入「空库也能写进去」自测（不联网、不花钱、用假 embedding）。

背景（2026-10-04 定位、10-05 修）：
  kb/importer.py 的 import_url / download_and_index 原本用 pipe.get_or_open() 拿 _vs，
  而 get_or_open() 只在磁盘上已有片段（peek() > 0）时才构造 Chroma 实例。
  前端 webImport 是「先 createKb 建一个空库，再 importUrl」，于是：

      _vs is None  →  _vs.add_documents()  →  AttributeError

  更糟的是失败发生在建 collection **之前**，库永远是空的 ⇒ 重试多少次都不可能成功，
  表现就是「联网建库 100% 失败」，且前端还把 0 篇成功报成 success。

本测试盯三件事：
  1. 空库上 get_or_open() 确实拿不到实例（这是 bug 的根因，先把它钉死）；
  2. import_url / download_and_index 在空库上不再抛异常，且库里真的有片段；
  3. 两条链路都能追加（第二次进来的片段数叠加）。

联网那一段用假 embedding + 本地 HTTP 服务替代，所以全程离线、可反复跑。
可从任意位置运行：python tests/test_web_import_creates_index.py
"""

import http.server
import os
import socketserver
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.embeddings import DeterministicFakeEmbedding  # noqa: E402

from kb import pipelines, rag_core  # noqa: E402
from kb import importer  # noqa: E402

FAILED: list[str] = []
PORT = 8766  # 避开常用端口；被占用时自动换
DOC = ("苞米agent 是一个基于 FastAPI 与 Next.js 的检索增强问答系统。"
       "文档上传后会先切片，再用阿里百炼 text-embedding-v3 生成向量，写入 Chroma；"
       "检索阶段先取候选池，再做余弦离群过滤与 MMR 多样性选择。\n") * 20

_fake = DeterministicFakeEmbedding(size=32)


def check(cond: bool, msg: str) -> None:
    print(("  [ok] " if cond else "  [FAIL] ") + msg)
    if not cond:
        FAILED.append(msg)


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def _serve(dirpath: str):
    """在本机起一个只服务 dirpath 的 HTTP 服务，返回 (httpd, base_url)。"""
    port = PORT
    for _ in range(20):
        try:
            httpd = socketserver.TCPServer(("127.0.0.1", port),
                                           lambda *a, **k: _QuietHandler(*a, directory=dirpath, **k))
            break
        except OSError:
            port += 1
    else:
        raise RuntimeError("找不到可用端口")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{port}"


def main() -> int:
    print("=" * 62)
    print("联网导入 · 空库入库自测（离线）")
    print("=" * 62)

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tmp = os.path.join(root, "data", "_test_web_import_tmp")
    os.makedirs(tmp, exist_ok=True)
    with open(os.path.join(tmp, "long.txt"), "w", encoding="utf-8") as f:
        f.write(DOC)

    httpd, base = _serve(tmp)
    url = f"{base}/long.txt"

    # 本用例的"网站"就是本机 127.0.0.1 上的临时 http.server，而 SSRF 防护
    # （core/netsafe.py）正是禁止服务端访问回环地址的 —— 这里放行它，
    # 是为了能离线测"导入链路"，与防护本身无关；防护由 test_agent_guard 单独验。
    importer.assert_safe_url = lambda u: u

    # 假 embedding：不联网、不花钱，维度固定 32
    rag_core.get_embeddings = lambda: _fake

    # 多用户改造后需要 owner：这里用一个固定的测试账号
    OWNER = "web_import_test"

    kb_id = None
    try:
        kb_id, _ = pipelines.create_kb(OWNER, "离线自测_联网导入")
        pipe = pipelines.get_or_create(OWNER, kb_id)

        # ---- 1. 根因：空库 get_or_open() 拿不到实例 ----
        print("\n[1] 空库语义")
        check(pipe.peek() == 0, "新库 peek() == 0（确实是个空库）")
        pipe.get_or_open()
        check(pipe._vs is None,
              "空库 get_or_open() 后 _vs 仍为 None（所以原写法必抛 AttributeError）")

        # ---- 2. import_url 能写进空库 ----
        print("\n[2] import_url 入库")
        try:
            r = importer.import_url(OWNER, kb_id, url, chunk_size=300, chunk_overlap=30)
            check(r["chunks"] > 0, f"import_url 返回片段数 {r['chunks']} > 0")
        except Exception as e:
            check(False, f"import_url 抛异常：{type(e).__name__}: {e}")
            raise SystemExit(1)
        c1 = pipelines.get_or_create(OWNER, kb_id).peek()
        check(c1 > 0, f"库内真的有片段：count = {c1}")
        check(pipelines.exists(OWNER, kb_id) is True, "exists() 由 False 变为 True")
        check(any(f["name"] == r["file"] for f in pipelines.get_files(OWNER, kb_id)),
              "文件清单里登记了这份联网资料")

        # ---- 3. download_and_index 追加 ----
        print("\n[3] download_and_index 追加")
        try:
            r2 = importer.download_and_index(OWNER, kb_id, url, chunk_size=300, chunk_overlap=30)
            check(r2.get("chunks", 0) > 0, f"download_and_index 返回片段数 {r2.get('chunks')} > 0")
        except Exception as e:
            check(False, f"download_and_index 抛异常：{type(e).__name__}: {e}")
            raise SystemExit(1)
        c2 = pipelines.get_or_create(OWNER, kb_id).peek()
        check(c2 > c1, f"追加生效：{c1} → {c2}")

        # ---- 4. 静态约束：导入路径不许再退回 get_or_open() ----
        print("\n[4] 源码约束")
        src = open(os.path.join(root, "kb", "importer.py"), encoding="utf-8").read()
        # 只匹配调用形式：注释里还会提到旧写法（说明为什么不能用它），别误伤
        check(".get_or_open()" not in src,
              "kb/importer.py 里已无 get_or_open() 调用（写入路径必须是 _ensure_vs 系）")
        check("add_chunks(" in src, "kb/importer.py 走的是 pipe.add_chunks()")
    finally:
        if kb_id:
            pipelines.delete_kb(OWNER, kb_id)
        httpd.shutdown()
        try:
            os.remove(os.path.join(tmp, "long.txt"))
            os.rmdir(tmp)
        except OSError:
            pass

    print("\n" + "=" * 62)
    if FAILED:
        print(f"失败 {len(FAILED)} 项：")
        for m in FAILED:
            print("  - " + m)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
