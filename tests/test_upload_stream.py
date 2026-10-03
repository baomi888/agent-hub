# -*- coding: utf-8 -*-
"""上传落盘自测：不联网、不依赖真实知识库。

覆盖三件事：
  1. 分块读，全程不出现"一次 read() 读整个文件"；
  2. 超限立刻中止，且不把半成品留在磁盘上；
  3. check_declared_size 能在读之前就挡掉超大文件。

可从任意位置运行：python tests/test_upload_stream.py
"""

import asyncio
import os
import sys
import tempfile
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException, UploadFile  # noqa: E402

from core import upload  # noqa: E402

FAILED: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  [ok] " if cond else "  [FAIL] ") + msg)
    if not cond:
        FAILED.append(msg)


class TrackingFile:
    """记录"单次最多被读了多少字节"的假文件对象。

    旧代码 `await f.read()` 会走到 read(-1)，等于把整个文件读成一个 bytes —— 这就是
    2G 机器上 OOM 的直接原因。这里专门盯着它。
    """

    def __init__(self, data: bytes):
        self._buf = BytesIO(data)
        self.max_read = 0
        self.saw_full_read = False

    def read(self, n=-1):
        if n is None or n < 0:
            self.saw_full_read = True
        chunk = self._buf.read(n)
        self.max_read = max(self.max_read, len(chunk))
        return chunk

    def write(self, b):
        return len(b)

    def seek(self, *a):
        return 0

    def close(self):
        pass


def make_upload(data: bytes, name: str = "doc.txt") -> tuple[UploadFile, TrackingFile]:
    raw = TrackingFile(data)
    return UploadFile(file=raw, size=len(data), filename=name), raw


def test_1_chunked_read() -> None:
    """5MB 文件必须分块读，单次不超过 1MB。"""
    print("\n[1] 分块读：单次不超 1MB，且不出现整体 read()")
    tmp = os.path.join(tempfile.mkdtemp(), "a.txt")
    data = b"x" * (5 * 1024 * 1024)
    f, raw = make_upload(data)

    n = asyncio.run(upload.save_stream(f, tmp, 100 * 1024 * 1024))
    check(n == len(data), f"落盘字节数正确（{n}）")
    check(not raw.saw_full_read, "没有调用过 read(-1)（旧写法会 OOM）")
    check(raw.max_read <= 1024 * 1024, f"单次最多读 {raw.max_read} 字节（<=1MB）")
    check(os.path.getsize(tmp) == len(data), "文件内容完整")


def test_2_over_limit() -> None:
    """超过上限立刻 400，且不留半成品。"""
    print("\n[2] 超限：抛 400 且删掉半成品")
    d = tempfile.mkdtemp()
    tmp = os.path.join(d, "big.txt")
    limit = 2 * 1024 * 1024
    f, _ = make_upload(b"y" * (5 * 1024 * 1024))

    raised = None
    try:
        asyncio.run(upload.save_stream(f, tmp, limit))
    except HTTPException as e:
        raised = e
    check(raised is not None, "抛出了 HTTPException")
    check(raised is not None and raised.status_code == 400, "状态码是 400")
    check(not os.path.exists(tmp), "半成品已被删除（不会留孤儿文件）")


def test_3_declared_size() -> None:
    """声明就超限的话，一个 chunk 都不该读。"""
    print("\n[3] check_declared_size：读之前就挡掉")
    f, raw = make_upload(b"z" * (10 * 1024 * 1024))
    raised = None
    try:
        upload.check_declared_size(f, 1024)
    except HTTPException as e:
        raised = e
    check(raised is not None and raised.status_code == 400, "超限直接 400")
    check(raw.max_read == 0, "一次都没读过 body（零成本拦截）")

    f2, _ = make_upload(b"small")
    try:
        upload.check_declared_size(f2, 1024)
        check(True, "未超限时不报错")
    except HTTPException:
        check(False, "未超限时不报错")


def test_4_cleanup() -> None:
    """cleanup() 清理孤儿文件，且不因为个别文件删不掉而中断。"""
    print("\n[4] cleanup：清得掉就清，清不掉也不炸")
    d = tempfile.mkdtemp()
    a = os.path.join(d, "a.txt")
    b = os.path.join(d, "b.txt")
    for p in (a, b):
        with open(p, "wb") as fh:
            fh.write(b"1")
    upload.cleanup([a, b, os.path.join(d, "not-exist.txt")])
    check(not os.path.exists(a) and not os.path.exists(b), "已落盘的文件被清掉")
    check(True, "不存在的文件不导致异常")


def main() -> int:
    print("=" * 60)
    print("上传落盘自测（分块 / 超限 / 预检 / 清理）")
    print("=" * 60)
    test_1_chunked_read()
    test_2_over_limit()
    test_3_declared_size()
    test_4_cleanup()
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
