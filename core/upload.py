# -*- coding: utf-8 -*-
"""上传落盘的统一入口。

两处上传（知识库文档 / 聊天附件）以前都是同一个写法：

    content = await f.read()                       # 1GB 也照读
    if len(content) > MAX * 1024 * 1024:           # 判在读完之后
        raise HTTPException(400, ...)

`f.read()` 不带参数会把整个文件读成一个 bytes —— 部署机是 2 核 2G，
一个 1GB 的 PDF 传上来，等判出"过大"的时候内存已经被吃光，uvicorn 被 OOM killer
带走，用户看到的只是"连接被重置"。上限写了等于没写。

这里统一成两步：
  ① 先看已经收到的字节数（Starlette 把 body 收在 SpooledTemporaryFile 里，
     超过 1MB 的部分本来就在磁盘上），声明就超限的直接拒，一个 chunk 都不读；
  ② 分块读、边写边累加，超过上限立刻停手并删掉半成品。
"""

import logging
import os

from fastapi import HTTPException, UploadFile

# 每块 1MB：读一块写一块，进程里始终只有这么大
_CHUNK = 1024 * 1024


def _mb(n: int) -> int:
    return n // 1024 // 1024


def check_declared_size(f: UploadFile, limit_bytes: int) -> None:
    """按已接收的字节数提前拦一次。

    UploadFile.size 是 Starlette 收完 body 后落在 SpooledTemporaryFile 里的大小，
    读它是零成本的——超大文件在这一步就被挡掉，不用再走一遍分块读。
    拿不到 size（老版本 Starlette / 已被读过）就跳过，交给 save_stream 兜。
    """
    size = getattr(f, "size", None)
    if isinstance(size, int) and size > limit_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"文件过大（>{_mb(limit_bytes)}MB）：{f.filename or '未命名'}",
        )


def cleanup(paths) -> None:
    """删掉已经落盘但没被任何记录认领的文件（孤儿原始文件）。

    上传走到一半失败时，前面几个文件已经躺在 data/kb_sources/ 里：
    它们既没进 kb_files.json 也没进 Chroma，rebuild 走的是记录读不到，
    只有删掉整个库才会被 rmtree 清走。用户改个扩展名重传一次，磁盘上就多一份永久垃圾。
    """
    for p in paths:
        try:
            if p and os.path.exists(p):
                os.unlink(p)
        except OSError as e:
            # 清不掉也要留个痕：静默的话磁盘会一点点被吃满
            logging.getLogger(__name__).warning("清理未入库的上传文件失败 %s：%s", p, e)


async def save_stream(f: UploadFile, path: str, limit_bytes: int) -> int:
    """分块写盘，超限立即中止并删掉半成品。返回落盘字节数。"""
    written = 0
    try:
        with open(path, "wb") as out:
            while True:
                chunk = await f.read(_CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit_bytes:
                    raise HTTPException(
                        status_code=400,
                        detail=f"文件过大（>{_mb(limit_bytes)}MB）：{f.filename or '未命名'}",
                    )
                out.write(chunk)
    except Exception:
        cleanup([path])
        raise
    return written
