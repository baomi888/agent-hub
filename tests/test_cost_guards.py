"""成本与内存护栏回归（2026-10-13 第一档）：不联网、不依赖真实知识库 / LLM。

覆盖三项：
  1. 标题生成护栏：问题截断到 200 字、max_tokens=32（长文首问不再烧 token）；
  2. has_source 轻量判存：只取 1 条 id（limit=1 + 空 include），
     不再像 get_source_chunks 那样把整个文件的片段正文拉回内存；
  3. _read_capped / _decode_html：网页抓取超限立即中止，charset 解码兜底正常。

可从任意位置运行：python tests/test_cost_guards.py
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import chat as chat_api  # noqa: E402
from kb import importer  # noqa: E402
from kb.rag_core import RagPipeline  # noqa: E402

FAILED: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  [ok] " if cond else "  [FAIL] ") + msg)
    if not cond:
        FAILED.append(msg)


# ==================== 1. 标题生成护栏 ====================


class _FakeLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def invoke(self, messages):
        self.prompts.append(messages[-1].content)
        return SimpleNamespace(content="测试标题")


def test_1_title_guard() -> None:
    """长问题被截到 200 字进 prompt，且 max_tokens=32。"""
    print("\n[1] 标题生成：输入截断 + 输出上限")
    fake = _FakeLLM()
    captured: dict = {}

    def fake_get_llm(temperature=0.1, max_tokens=None):
        captured["temperature"] = temperature
        captured["max_tokens"] = max_tokens
        return fake

    orig = chat_api.get_llm
    chat_api.get_llm = fake_get_llm
    try:
        long_q = "请介绍一下深度学习在推荐系统中的应用" * 30  # 约 570 字
        title = chat_api._generate_title(long_q)
    finally:
        chat_api.get_llm = orig

    check(captured.get("temperature") == 0, "temperature=0（标题生成沿用低温）")
    check(captured.get("max_tokens") == 32, "max_tokens=32（输出有兜底，防跑偏烧钱）")
    check(len(fake.prompts) == 1, "只调用了一次 LLM")
    prompt = fake.prompts[0] if fake.prompts else ""
    check(long_q[:200] in prompt, "截断后的前 200 字进入了 prompt")
    check(long_q not in prompt, "完整长问题没有进入 prompt（被截断）")
    check(title == "测试标题", "返回模型生成的标题")


# ==================== 2. has_source 轻量判存 ====================


class _FakeCollection:
    """记录 get() 调用参数的假 collection。"""

    def __init__(self, ids):
        self._ids = ids
        self.calls: list[dict] = []

    def get(self, where=None, limit=None, include=None):
        self.calls.append({"where": where, "limit": limit, "include": include})
        ids = self._ids if limit is None else self._ids[:limit]
        return {"ids": ids}


def test_2_has_source() -> None:
    """判存只取 1 条 id；空库 / 异常都返回 False。"""
    print("\n[2] has_source：limit=1 + 空 include，不拉片段内容")
    pipe = RagPipeline("kb_fake")
    col = _FakeCollection(["id1", "id2", "id3"])
    pipe._vs = SimpleNamespace(_collection=col)

    check(pipe.has_source("a.pdf") is True, "有片段时返回 True")
    args = col.calls[0]
    check(args["where"] == {"source": "a.pdf"}, "按 source 过滤")
    check(args["limit"] == 1, "limit=1（无论文件多大只取一条）")
    check(args["include"] == [], "include=[]（不取任何正文/元数据）")

    col._ids = []
    check(pipe.has_source("a.pdf") is False, "无片段时返回 False")

    class _BadCollection:
        def get(self, **kwargs):
            raise RuntimeError("boom")

    pipe._vs = SimpleNamespace(_collection=_BadCollection())
    check(pipe.has_source("a.pdf") is False, "查询异常时返回 False（不炸导入流程）")


# ==================== 3. 抓取大小闸与解码 ====================


class _FakeStreamResp:
    def __init__(self, chunks):
        self._chunks = chunks
        self.headers: dict = {}

    def iter_bytes(self, chunk_size=64 * 1024):
        yield from self._chunks


def test_3_read_capped_and_decode() -> None:
    """超限立即中止；gbk/utf-8 解码兜底正常。"""
    print("\n[3] _read_capped：超限中止；_decode_html：charset 兜底")
    small = _FakeStreamResp([b"a" * 10, b"b" * 10])
    data = importer._read_capped(small, 100)
    check(data == b"a" * 10 + b"b" * 10, "未超限内容完整拼回")

    big = _FakeStreamResp([b"x" * (3 * 1024 * 1024), b"y" * (3 * 1024 * 1024)])
    raised = None
    try:
        importer._read_capped(big, 1 * 1024 * 1024)
    except ValueError as e:
        raised = e
    check(raised is not None, "超限抛 ValueError（中止抓取）")
    check(raised is not None and "上限" in str(raised), "错误提示带上限说明")

    gbk = SimpleNamespace(headers={"content-type": "text/html; charset=gbk"})
    check(
        importer._decode_html(gbk, "中文标题".encode("gbk")) == "中文标题",
        "响应头带 charset=gbk 时按 gbk 解码",
    )

    utf8 = SimpleNamespace(headers={"content-type": "text/html"})
    check(
        importer._decode_html(utf8, "中文标题".encode()) == "中文标题",
        "无 charset 时探测解码 utf-8",
    )

    bad = SimpleNamespace(headers={"content-type": "text/html; charset=gbk"})
    out = importer._decode_html(bad, "中文".encode())
    check(isinstance(out, str) and out, "声明 charset 解码失败时兜底不乱抛")


def main() -> int:
    print("=" * 60)
    print("成本与内存护栏回归（标题 / 判存 / 抓取闸）")
    print("=" * 60)
    test_1_title_guard()
    test_2_has_source()
    test_3_read_capped_and_decode()
    print("\n" + "=" * 60)
    if FAILED:
        print(f"失败 {len(FAILED)} 项：")
        for m in FAILED:
            print("  - " + m)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
