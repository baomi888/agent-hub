"""论坛配图的存储层。

## 为什么这里要分两条路径

解码一张 1200 万像素的手机照片，峰值内存 53 MB；缩到长边再转 WebP，79 MB。
这台机器 `available` 只剩 400 MB，9 张图一起解码就是 714 MB —— uvicorn 会被
OOM 杀掉，用户看到的只是「连接被重置」。所以**能不解码就不解码**。

前端已经把图压好了（长边 2048 / WebP / q0.82，约 350–500 KB），服务端收到的
本来就是成品。默认路径因此是：

    读文件头校验（不 load 像素，峰值 ~5.6 MB）→ 内容哈希 → 落盘

一个像素都不解。这是快路径，覆盖绝大多数请求。

但客户端可以被绕过（curl 直接 POST 一张 6 MB 的 PNG，带 GPS 的 EXIF 原图），
所以留一条慢路径：体积超标、格式不对、或带 EXIF 时，才真正解码重压，而且用
信号量**串行**执行 —— 宁可让第 3 个人多等 200ms，也不要三个解码同时顶内存。

## EXIF 是顺便解决的一个隐私问题

手机照片的 EXIF 里带 GPS 坐标。学生拍宿舍、拍教室发上来，照片里就写着经纬度。
canvas 重绘天然丢光所有元数据，所以**前端压过的图必然没有 EXIF** —— 这既是
隐私保护，也刚好成了「这张图是不是前端压过」的判据：无 EXIF + 体积达标 + 格式
合法，三条同时成立才放行快路径。

绕过前端的原图一律走慢路径，重压时 `exif_transpose` 摆正方向后整块丢弃元数据。

## 去重

文件名就是内容的 sha256 前 16 位。同一张图（课本照片、表情包）被 20 个人发，
磁盘上只有一份。校园场景里这个命中率会很高。
"""

import hashlib
import io
import logging
import os
import tempfile
import threading

from PIL import Image, ImageOps

from core import config, session

log = logging.getLogger(__name__)

# ---------- 硬约束 ----------

# 单张体积硬上限。**8MB 这个数是按真实手机原图定的**：iPhone / 安卓默认拍出来
# 就在 3–8MB，卡到 2MB 的话，前端压缩一旦失效（微信内核异常、老浏览器没有
# canvas.toBlob）用户的原图会被当场拒收 —— 而压缩本来就该是优化，不是硬依赖。
# 超过 8MB 的不是手机直出（单反、截图拼接），走"换一张"的提示更合适。
# 真的到这一步的图会走慢路径重压，串行执行，峰值 79MB，安全。
MAX_BYTES = 8 * 1024 * 1024

# 快路径的体积门槛。前端压完通常 350–500KB，700KB 之上说明没压或压不动。
FAST_MAX_BYTES = 700 * 1024

# 快路径认的格式。WebP 是首选；JPEG 是微信内核不支持 WebP 编码时的降级产物，
# 同样是 canvas 重绘出来的（无 EXIF），所以也放行 —— 只是体积大些，门槛压低。
FAST_FORMATS = ("WEBP", "JPEG")

# 解码前的尺寸闸门：单边和总像素各一道，解压炸弹在读文件头这一步就死。
MAX_SIDE = 6000
MAX_PIXELS = 40_000_000

# 产物参数
OUT_SIDE = 2048
QUALITY = 82

# 一帖最多几张。9 张是九宫格的上限，也是百度贴吧的习惯；再多列表页会失控。
MAX_IMAGES = 9

ALNUM = set("0123456789abcdef")
HASH_LEN = 16

# 慢路径串行。进程内有效，多 worker 时每个 worker 各限 1（总量 = worker 数）。
# 单进程部署，这里就是全局。
_SLOW_SLOTS = threading.Semaphore(1)


class ImageError(Exception):
    """给用户看的错误。文案是中文、可操作，不含堆栈。"""


def img_dir() -> str:
    """论坛图目录。★ 每次现取 `config.DATA_DIR`：测试会 monkeypatch 它，
    import 时算死会写进真实数据目录（头像那边踩过这个坑）。"""
    return os.path.join(config.DATA_DIR, "forum_img")


# ---------- 序列化 ----------
#
# 一张帖的配图存成 `a1b2,c3d4` 这种逗号串，不用 JSON：
#   · 分隔符是 ASCII，hash 里不会出现
#   · 空串天然表示"没有图"，不用区分 "[]" 和 "" 两种空
#   · 解析失败也不会让整条帖打不开（split 永远有结果）

def dump(hashes) -> str:
    return ",".join(h for h in hashes if h)


def parse(raw: str) -> list[str]:
    return [h for h in (raw or "").split(",") if h]


def is_hash(s: str) -> bool:
    """只有 16 位小写 hex 才算 hash。

    这道校验是路径穿越的唯一防线：`hash` 会被拼进文件路径，
    放行 `../../etc/passwd` 就等于把整个磁盘读出去。
    """
    s = (s or "").strip()
    return len(s) == HASH_LEN and all(c in ALNUM for c in s)


def path_of(hash_: str) -> str | None:
    """hash → 磁盘路径；非法 hash 或文件不存在都返回 None。"""
    if not is_hash(hash_):
        return None
    p = os.path.join(img_dir(), hash_ + ".webp")
    return p if os.path.isfile(p) else None


# ---------- 校验 ----------

def _head(path: str) -> tuple[str, int, int]:
    """只读文件头，不解码像素。返回 (真实格式, 宽, 高)。

    `Image.open()` 是懒加载，`im.size` 拿的是文件头的值 —— 这一步是免费的。
    真实格式从文件内容解析，**不看扩展名也不看 Content-Type**，两者都能伪造。
    """
    try:
        with Image.open(path) as im:
            fmt = str(im.format or "").upper()
            w, h = im.size
    except Image.UnidentifiedImageError as e:
        raise ImageError("这个文件打不开，换一张 JPG / PNG / WebP 图片") from e
    except Image.DecompressionBombError as e:
        raise ImageError("这张图的像素太多，换一张试试") from e
    except (OSError, ValueError) as e:
        raise ImageError("这个文件打不开，换一张 JPG / PNG / WebP 图片") from e

    if fmt not in ("JPEG", "PNG", "WEBP", "GIF", "BMP"):
        raise ImageError("只支持 JPG / PNG / WebP 图片")
    if w <= 0 or h <= 0:
        raise ImageError("这张图片的尺寸不合法")
    if w > MAX_SIDE or h > MAX_SIDE:
        raise ImageError(
            f"图片太大了（{w}×{h}），最长边请控制在 {MAX_SIDE} 像素以内"
        )
    if w * h > MAX_PIXELS:
        raise ImageError(f"图片的像素太多（{w}×{h}），换一张小一点的")
    return fmt, w, h


def has_exif(path: str) -> bool:
    """扫文件前 128KB 找元数据标记。纯字节操作，不解码。

    JPEG 的 EXIF 在 APP1 段（`Exif\\0\\0`），WebP 的是 RIFF 里一个 `EXIF` chunk。
    误判只会让这张图多走一次重压，无害；漏判才是问题，所以窗口给到 128KB
    （EXIF 再长也很少超过这个量）。
    """
    try:
        with open(path, "rb") as f:
            head = f.read(128 * 1024)
    except OSError:
        return False
    if head[:2] == b"\xff\xd8":
        return b"Exif\x00\x00" in head
    if head[:4] == b"RIFF":
        return b"EXIF" in head
    return False


# ---------- 慢路径：真正解码重压 ----------

def _recompress(path: str) -> tuple[bytes, int, int]:
    """解码 → 摆正 → 缩到长边 OUT_SIDE → 丢掉全部元数据 → (WebP 字节, 宽, 高)。

    每一步都在省内存：
      1. `draft()` 让 libjpeg 就按 1/2、1/4、1/8 解码。**必须显式给整数倍**：
         实测 `draft("RGB", (1600,1600))` 对 4032 宽的图会被 Pillow 静默忽略
         （4032/1600 = 2.52 不是整数），尺寸原样返回、内存一点没省。
         写成 `(w // 2, h // 2)` 立刻生效，峰值从 62 MB 掉到 18 MB。
      2. `thumbnail()` 而不是 `resize()`：渐进降采样，峰值更低。
      3. 产物不硬拉回正好 OUT_SIDE：draft 到 2016 就存 2016。为了凑整
         把图放大 1.6%，只会糊一点、不会更清楚。
    """
    try:
        with Image.open(path) as im:
            w, h = im.size

            # 找最省内存又不过头的降采样档。用长边判断：4032×3024 的 1/2 是
            # 2016×1512，长边 2016 已经很接近目标 2048，这一档值得要。
            div = 1
            for d in (8, 4, 2):
                if max(w // d, h // d) >= OUT_SIDE * 0.85:
                    div = d
                    break
            if div > 1:
                im.draft("RGB", (w // div, h // div))

            # 手机照片常带 EXIF 旋转，不摆正会躺着。这一步同时把元数据读进来，
            # 后面保存时不传 exif 参数就等于彻底丢掉了。
            im = ImageOps.exif_transpose(im)

            # 透明图垫白底。直接 convert("RGB") 会把透明区变成黑色，
            # 一个透明底截图贴到浅色页面上就是一块黑斑。
            if im.mode in ("RGBA", "LA", "P"):
                rgba = im.convert("RGBA")
                bg = Image.new("RGB", rgba.size, (255, 255, 255))
                bg.paste(rgba, mask=rgba.split()[-1])
                im = bg
            else:
                im = im.convert("RGB")

            im.thumbnail((OUT_SIDE, OUT_SIDE), Image.LANCZOS)

            buf = io.BytesIO()
            # 不传 exif/icc_profile：产物里不带任何元数据，GPS 在这一步消失
            im.save(buf, format="WEBP", quality=QUALITY, method=4)
            return buf.getvalue(), im.width, im.height
    except Image.DecompressionBombError as e:
        raise ImageError("这张图的像素太多，换一张试试") from e
    except (OSError, ValueError) as e:
        raise ImageError("图片处理失败，换一张试试") from e


# ---------- 落盘 ----------

def _write(data: bytes) -> str:
    """内容哈希命名 + 原子写。返回 hash。"""
    hash_ = hashlib.sha256(data).hexdigest()[:HASH_LEN]
    dest = os.path.join(img_dir(), hash_ + ".webp")
    os.makedirs(img_dir(), exist_ok=True)

    if os.path.exists(dest):
        return hash_  # 同一张图已经有人发过，去重命中

    # 每个请求用独立临时文件。固定写 `dest + ".tmp"` 时，同图并发上传会多个
    # 线程争同一路径；Windows 上还会出现一个线程已 replace、另一个再也找不到
    # 临时文件的 500。内容相同，最后谁覆盖 dest 都可以。
    fd, tmp = tempfile.mkstemp(prefix=hash_ + ".", suffix=".tmp", dir=img_dir())
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        try:
            os.replace(tmp, dest)
        except (FileExistsError, PermissionError):
            # Windows 上两个线程同时把同一内容 replace 到同一目标，输家拿
            # WinError 5。赢家已把完整文件放好就等同成功；目标不存在说明
            # 这是真的权限/磁盘错误，继续往上抛。
            if not os.path.isfile(dest):
                raise
    finally:
        _remove(tmp)
    return hash_


def _remove(p: str) -> None:
    try:
        if p and os.path.exists(p):
            os.unlink(p)
    except OSError as e:
        log.warning("清理临时文件失败 %s：%s", p, e)


# ---------- 主入口 ----------

def store(path: str, original_name: str = "") -> dict:
    """校验 → 决定走快路径还是慢路径 → 落盘。返回 {hash, w, h, bytes, maybe_slow}。

    调用方负责把 `path` 删掉（通常是 UploadFile 的临时文件）。
    """
    size = os.path.getsize(path)
    if size <= 0:
        raise ImageError("这个文件是空的")
    if size > MAX_BYTES:
        raise ImageError(
            f"图片太大了（{size // 1024 // 1024 + 1}MB），单张请控制在 {MAX_BYTES // 1024 // 1024}MB 以内"
        )

    fmt, w, h = _head(path)

    # 快路径：前端压过的成品，一个像素都不解
    if (
        fmt in FAST_FORMATS
        and size <= FAST_MAX_BYTES
        and not has_exif(path)
    ):
        with open(path, "rb") as f:
            data = f.read()
        return {"hash": _write(data), "w": w, "h": h, "bytes": len(data), "rescued": False}

    # 慢路径：不是成品（原图 / PNG / 带 GPS 的 JPEG / 体积超标），重压。
    # 串行执行，宁可多等一会儿也不让几个解码同时顶内存。
    with _SLOW_SLOTS:
        data, w2, h2 = _recompress(path)
    if len(data) > MAX_BYTES:
        raise ImageError("这张图压缩后还是太大，换一张试试")
    return {"hash": _write(data), "w": w2, "h": h2, "bytes": len(data), "rescued": True}


# ---------- 孤儿清理 ----------

def cleanup_orphans(keep_hours: int = 24) -> int:
    """删掉没有任何帖引用的图片。

    用户选了图、面板关了却没发帖，图就躺在磁盘上永远没人认领。一张 500KB，
    攒多了会把 21GB 吃出洞来。所以按 "文件 mtime 早于 keep_hours 且不被任何
    kb_posts.images 引用" 清。

    为什么留 24 小时而不是立刻清：用户可能开着面板去翻资料，半小时后才发。
    """
    import time

    used: set[str] = set()
    try:
        with session._get_conn() as conn:  # noqa: SLF001
            rows = conn.execute(
                "SELECT images FROM kb_posts WHERE images != ''"
            ).fetchall()
        for r in rows:
            used.update(parse(r[0]))
    except Exception as e:  # 库都读不到就别删了，宁可占着磁盘
        log.warning("查已引用图片失败，跳过清理：%s", e)
        return 0

    cutoff = time.time() - keep_hours * 3600
    removed = 0
    try:
        names = os.listdir(img_dir())
    except OSError:
        return 0

    for name in names:
        if not name.endswith(".webp"):
            continue
        hash_ = name[:-5]
        if hash_ in used:
            continue
        p = os.path.join(img_dir(), name)
        try:
            if os.path.getmtime(p) > cutoff:
                continue
            os.unlink(p)
            removed += 1
        except OSError as e:
            log.warning("清理孤儿图失败 %s：%s", p, e)
    if removed:
        log.info("清理孤儿图 %d 个", removed)
    return removed


def dir_size() -> tuple[int, int]:
    """(文件数, 总字节)。给管理台/巡检看用。"""
    n = 0
    total = 0
    try:
        for name in os.listdir(img_dir()):
            if not name.endswith(".webp"):
                continue
            try:
                total += os.path.getsize(os.path.join(img_dir(), name))
                n += 1
            except OSError:
                pass
    except OSError:
        pass
    return n, total
