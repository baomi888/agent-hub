"""头像上传：解码 → 裁方 → 192×192 → 落盘，只保留成品。

为什么单独一个模块
------------------
这件事有四个容易做错的地方（大小、类型、内存、清理），塞进路由函数会让
`api/auth.py` 变成一个既管鉴权又管图片处理的地方。更要紧的是：**这里所有的
限制都必须自己校验，一条都不能信前端** —— 前端压缩是省流量，不是安全边界。

三层限额（对应方案 §5.4）
------------------------
1. 前端 canvas 先把长边压到 640 / q0.85（`frontend/src/lib/imageCompress.ts`）
2. 这里硬限 5MB，走 `core.upload.save_stream` 分块写盘、超限立即中断
3. 落盘只存 192×192 JPEG q85（约 12~18KB）—— **原图处理完立刻删**

轻量服务器真正的风险不是磁盘，是峰值内存
----------------------------------------
一张 5MB 的 JPEG 解码成位图可能占 50~100MB；解压炸弹（几十 KB 的 PNG 声明
20000×20000）能直接吃掉几 GB。2 核 2G 的机器上两个这样的请求同时到，
挂的是整个后端，不是一次上传。对策见 `_render` 与 `store` 的注释。
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import tempfile
import threading

from fastapi import HTTPException, UploadFile
from PIL import Image, ImageOps

from core import config
from core import upload as upload_io

log = logging.getLogger(__name__)

# ---- 限额 ----
MAX_BYTES = 5 * 1024 * 1024  # 对齐微博/知乎/QQ 的 5MB；GitHub 1MB、Gmail 4MB
# multipart 边界/头 + 6MB 边界测试需要走到业务层给 400，因此应用入口留 2MB 余量。
# 真正面向公网的 Nginx 仍单独卡 6m，本常量是本地直连/换代理时的兜底。
REQUEST_BODY_LIMIT = MAX_BYTES + 2 * 1024 * 1024
MAX_SIDE = 4000  # 单边像素上限，解压炸弹在读文件头这一步就被挡掉
OUT_SIDE = 192  # 最终落盘尺寸（显示最大 64px，留了 3 倍视网膜余量）
JPEG_QUALITY = 85  # 肉眼无损的甜点；0.7 以下人像脸会发灰

# 明确不收 SVG：它能带 <script>，是存储型 XSS 的入口，而且 PIL 也打不开。
# 注意这**不是**安全边界 —— 只看 Content-Type 能被伪造，真正的判定是
# `Image.open()` 打得开；这里只是提前给一句人话错误。
MIME_OK = frozenset({"image/jpeg", "image/png", "image/webp"})
_MIME_ALIAS = {"image/jpg": "image/jpeg", "image/pjpeg": "image/jpeg"}
# Content-Type 和扩展名都能伪造；这是 Image.open 解析出来的**真实格式**白名单。
FORMAT_OK = frozenset({"JPEG", "PNG", "WEBP"})

# 内容 hash 做文件名：换图 URL 自动变（不用处理浏览器缓存）、
# 文件名不含任何用户输入（天然防路径穿越）、同图可共用一份。
_HASH_RE = re.compile(r"^[0-9a-f]{16}$")
IMG_PREFIX = "img:"

# ★ 显式设像素上限：Pillow 默认只在超限时**发个 warning**，不报错，等于没有保护。
#   与 MAX_SIDE 对齐：4000×4000 = 16M 像素正好是这个数，再大就抛 DecompressionBombError。
#   主闸门仍是 `_render` 里"先读尺寸再解码"，这一条是防"别处顺手 load 了一张炸弹图"。
Image.MAX_IMAGE_PIXELS = MAX_SIDE * MAX_SIDE

# ★ 用 threading.Semaphore 而不是 asyncio.Semaphore：
#   解码本身就丢在 `asyncio.to_thread` 里跑（CPU 密集，不能占事件循环），
#   而 asyncio.Semaphore 绑定事件循环 —— TestClient 每个用例一个新 loop 时会炸
#   "Future attached to a different loop"。线程信号量没有这个问题。
_DECODE_SLOTS = threading.Semaphore(2)


class AvatarError(Exception):
    """能直接翻成 400 给用户看的错误（中文、不含堆栈）。"""


def avatar_dir() -> str:
    """头像目录。★ 每次现取 `config.DATA_DIR`：测试会 monkeypatch 它，
    在 import 时算死会写进真实数据目录（本项目踩过这个坑）。"""
    return os.path.join(config.DATA_DIR, "avatars")


def path_of(hash_: str) -> str | None:
    """hash → 磁盘路径；hash 非法或文件不存在都返回 None。

    ★ 只认 16 位小写十六进制：路径直接参与拼接，这里是**唯一**的防穿越点。
      不校验的话 `/api/auth/avatar/..%2f..%2fsessions.db` 就能把主库读出去。
    """
    h = (hash_ or "").strip()
    if not _HASH_RE.match(h):
        return None
    p = os.path.join(avatar_dir(), h + ".jpg")
    return p if os.path.isfile(p) else None


def exists(avatar_value: str) -> bool:
    """`img:<hash>` 指向的文件是否真的在（用于 PATCH /profile 的兜底校验）。"""
    if not (avatar_value or "").startswith(IMG_PREFIX):
        return True
    return path_of(avatar_value[len(IMG_PREFIX) :]) is not None


def remove_hash(hash_: str) -> None:
    p = path_of(hash_)
    if not p:
        return
    try:
        os.unlink(p)
    except OSError as e:  # 删不掉不是业务错误，但别静默
        log.warning("删除旧头像失败 %s：%s", p, e)


# ---------- 第 2 层：收文件 ----------


async def save_upload(file: UploadFile) -> str:
    """把上传流落到临时文件，返回临时路径（调用方负责 finally 删除）。

    用 `save_stream` 而不是 `await file.read()`：后者会把整个 body 读成 bytes，
    5MB × 并发就是内存峰值 —— 和知识库上传是同一个理由，复用同一份实现。
    """
    ctype = (file.content_type or "").split(";")[0].strip().lower()
    ctype = _MIME_ALIAS.get(ctype, ctype)
    if ctype and ctype not in MIME_OK:
        raise AvatarError("只支持 JPG / PNG / WebP 图片")
    # 已经收完的 body 若声明就超限，一个 chunk 都不用读（Starlette 会把
    # 超过 1MB 的部分落在磁盘上，读 size 是零成本的）
    try:
        upload_io.check_declared_size(file, MAX_BYTES)
    except HTTPException as e:
        raise AvatarError(str(e.detail)) from e

    fd, tmp = tempfile.mkstemp(prefix="avatar_", suffix=".upload")
    os.close(fd)
    try:
        await upload_io.save_stream(file, tmp, MAX_BYTES)
    except HTTPException as e:
        remove_file(tmp)
        # save_stream 的文案是"文件过大（>5MB）：xxx"，对头像够用了
        raise AvatarError(str(e.detail)) from e
    except Exception:
        remove_file(tmp)
        raise
    if os.path.getsize(tmp) == 0:
        remove_file(tmp)
        raise AvatarError("这个文件是空的，换一张试试")
    return tmp


def remove_file(path: str) -> None:
    """删掉临时文件 / 不再需要的文件（幂等，失败只告警）。"""
    try:
        if path and os.path.exists(path):
            os.unlink(path)
    except OSError as e:
        log.warning("清理临时文件失败 %s：%s", path, e)


# ---------- 第 3 层：处理 + 落盘 ----------


def _render(src: str) -> bytes:
    """解码 → 摆正 → 居中裁方 → 缩到 192 → JPEG 字节。

    ★ 顺序是刻意的，每一步都在省内存：
      1. `Image.open` 只读文件头，**不 load** —— 所以 `im.size` 是免费的，
         解压炸弹在拿尺寸这一步就死了，一个像素都还没解
      2. `draft()` 让 libjpeg 按 1/2、1/4、1/8 直接解码（4000×4000 的 JPEG
         在 1/8 时只解 500×500，内存从 ~48MB 降到 <1MB）
      3. `thumbnail()` 而不是 `resize()`：前者渐进降采样，峰值更低
    """
    try:
        with Image.open(src) as im:
            actual_format = str(im.format or "").upper()
            if actual_format not in FORMAT_OK:
                raise AvatarError("只支持 JPG / PNG / WebP 图片")
            w, h = im.size
            if w <= 0 or h <= 0:
                raise AvatarError("这张图片的尺寸不合法")
            if w > MAX_SIDE or h > MAX_SIDE:
                raise AvatarError(
                    f"图片太大了（{w}×{h}），最长边请控制在 {MAX_SIDE} 像素以内"
                )

            # 只在 JPEG 上有效，其它格式是 no-op
            im.draft("RGB", (OUT_SIDE * 2, OUT_SIDE * 2))

            # 手机照片常带 EXIF 旋转，不摆正会躺着
            im = ImageOps.exif_transpose(im)

            # 透明图垫白底：直接 convert("RGB") 会把透明区变成黑色，
            # 一个白底表情包就变成黑框头像
            if im.mode in ("RGBA", "LA", "P"):
                rgba = im.convert("RGBA")
                bg = Image.new("RGB", rgba.size, (255, 255, 255))
                bg.paste(rgba, mask=rgba.split()[-1])
                im = bg
            else:
                im = im.convert("RGB")

            # 居中裁正方形：最终显示是圆的，先裁后缩，不白丢画质
            side = min(im.width, im.height)
            if side != im.width or side != im.height:
                left = (im.width - side) // 2
                top = (im.height - side) // 2
                im = im.crop((left, top, left + side, top + side))

            # 大图先渐进缩，控制峰值内存；小图 thumbnail 不会放大，再统一 resize
            # 到 192。最终文件尺寸必须恒定，否则前端 64px 容器里会出现不同清晰度，
            # 浏览器验收也不能只拿一张大图就当“192 已成立”。
            im.thumbnail((OUT_SIDE, OUT_SIDE), Image.LANCZOS)
            if im.size != (OUT_SIDE, OUT_SIDE):
                im = im.resize((OUT_SIDE, OUT_SIDE), Image.LANCZOS)

            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            return buf.getvalue()
    except AvatarError:
        raise
    except Image.DecompressionBombError as e:
        # 单边没超但总像素超了（细长图），也在解码前抛
        raise AvatarError("这张图片的像素太多，换一张试试") from e
    except (Image.UnidentifiedImageError, OSError, ValueError) as e:
        # 扩展名/Content-Type 都能伪造，打得开才算图片
        raise AvatarError("这个文件打不开，换一张 JPG / PNG / WebP 试试") from e


def store(src: str) -> str:
    """处理并落盘，返回 `img:<hash>`。

    并发用信号量限 2：宁可让第 3 个人多等 200ms，也不要三个解码同时顶满内存。
    写盘走 tmp + `os.replace` —— 原子替换，避免别的请求读到写了一半的图。
    """
    with _DECODE_SLOTS:
        data = _render(src)

    # 压完还超 5MB 说明图很怪（正常 192×192 是 12~18KB）。这条是兜底断言，
    # 也是"压缩参数有没有生效"的自检 —— 落盘超过 30KB 就该查参数了。
    if len(data) > 1024 * 1024:
        raise AvatarError("图片处理失败，换一张试试")

    hash_ = hashlib.sha256(data).hexdigest()[:16]
    dest = os.path.join(avatar_dir(), hash_ + ".jpg")
    os.makedirs(avatar_dir(), exist_ok=True)
    if not os.path.exists(dest):
        # 每个请求用独立临时文件。固定写 `dest + ".tmp"` 时，同图并发上传会
        # 多个线程争同一路径；在 Windows 上还会出现一个线程已经 replace、
        # 另一个线程再也找不到临时文件的 500。内容相同，最后谁覆盖 dest 都可以。
        fd, tmp = tempfile.mkstemp(prefix=hash_ + ".", suffix=".tmp", dir=avatar_dir())
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            try:
                os.replace(tmp, dest)
            except (FileExistsError, PermissionError):
                # Windows 上两个线程同时把同一内容 replace 到同一目标时，输家
                # 会拿 WinError 5。只要赢家已经把完整目标文件放好，就等同成功；
                # 目标不存在说明这是真的权限/磁盘错误，继续往上抛。
                if not os.path.isfile(dest):
                    raise
        finally:
            remove_file(tmp)
    return IMG_PREFIX + hash_
