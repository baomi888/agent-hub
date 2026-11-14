"""论坛配图：存储两条路径 + 上传接口。

这里的断言重点不是"能存下来"，而是：
  · 前端压好的图**真的没被解码**（走快路径）
  · 绕过前端的原图会重压，并把 EXIF（GPS）剥掉
  · 路径穿越、超量、不存在的 hash 都被挡住
"""

import os

import pytest
from PIL import Image

from core import config
from service import forum_image


@pytest.fixture()
def img_env(tmp_path, monkeypatch):
    """把数据目录指到临时目录。★ 必须 monkeypatch：`img_dir()` 每次现取
    `config.DATA_DIR`，就是为了让这里能生效。"""
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    return tmp_path


def _jpeg(path, size=(640, 480), exif=False, quality=90):
    im = Image.new("RGB", size, (180, 96, 48))
    if exif:
        ex = Image.Exif()
        ex[0x010F] = "TestPhone"  # Make
        ex[0x0110] = "TestModel"  # Model
        # 一个 GPS IFD，模拟手机照片里的位置信息
        ex[0x8825] = {1: "N", 2: (39.0, 54.0, 0.0)}
        im.save(path, "JPEG", quality=quality, exif=ex)
    else:
        im.save(path, "JPEG", quality=quality)
    return path


def _webp(path, size=(1200, 900), quality=80):
    im = Image.new("RGB", size, (60, 120, 180))
    im.save(path, "WEBP", quality=quality)
    return path


# ==================== ① 快路径：不解码 ====================


def test_webp_without_exif_takes_the_fast_path(img_env, tmp_path):
    """前端压好的 WebP（无 EXIF、体积达标）应当原样落盘，不解码。"""
    src = _webp(tmp_path / "a.webp", size=(1600, 1200))
    assert os.path.getsize(src) <= forum_image.FAST_MAX_BYTES

    info = forum_image.store(str(src))

    assert info["rescued"] is False, "这张图不该走重压"
    assert info["w"] == 1600 and info["h"] == 1200
    out = forum_image.path_of(info["hash"])
    assert out and os.path.isfile(out)


def test_fast_path_keeps_bytes_identical(img_env, tmp_path):
    """快路径不做任何转码 —— 落盘字节必须和上传的一模一样。

    这是"没解码"最硬的证据：只要解过码再存，像素一样但字节不会一样。
    """
    src = _webp(tmp_path / "b.webp", size=(1200, 900))
    raw = open(src, "rb").read()

    info = forum_image.store(str(src))

    out = forum_image.path_of(info["hash"])
    with open(out, "rb") as f:
        assert f.read() == raw


# ==================== ② 慢路径：原图重压 + 剥 EXIF ====================


def test_jpeg_with_exif_is_recompressed_and_stripped(img_env, tmp_path):
    """带 EXIF 的 JPEG 必须重压，产物里不能再有 EXIF。

    先断言样本**确实带 EXIF** —— 不先验证这一点的话，has_exif 恒返回 False 时
    这条测试照样是绿的，等于什么都没检查。
    """
    src = _jpeg(tmp_path / "phone.jpg", size=(3000, 2000), exif=True, quality=92)
    assert forum_image.has_exif(str(src)) is True, "样本没带上 EXIF，测试前提不成立"

    info = forum_image.store(str(src))

    assert info["rescued"] is True, "带 EXIF 的图必须走重压"
    out = forum_image.path_of(info["hash"])
    assert forum_image.has_exif(out) is False, "重压后 EXIF 还在，GPS 就泄露了"
    # 长边缩到 2048 附近（draft 允许降到 0.85 倍，所以不要求正好 2048）
    assert max(info["w"], info["h"]) <= 2048
    assert max(info["w"], info["h"]) >= int(2048 * 0.85)


def test_oversized_file_is_rescued_not_rejected(img_env, tmp_path):
    """体积超过快路径门槛但没到硬上限 → 重压，而不是拒收。"""
    src = _jpeg(tmp_path / "big.jpg", size=(2600, 2000), quality=98)
    # 造一张超过阈值的图；纯色压得很小，加点噪声
    im = Image.new("RGB", (2600, 2000))
    px = im.load()
    for y in range(0, 2000, 2):
        for x in range(0, 2600, 2):
            c = ((x * 13) & 255, (y * 7) & 255, ((x + y) * 5) & 255)
            px[x, y] = c
            if x + 1 < 2600:
                px[x + 1, y] = c
            if y + 1 < 2000:
                px[x, y + 1] = c
                if x + 1 < 2600:
                    px[x + 1, y + 1] = c
    im.save(src, "JPEG", quality=98)
    assert os.path.getsize(src) > forum_image.FAST_MAX_BYTES

    info = forum_image.store(str(src))
    assert info["rescued"] is True
    assert info["bytes"] <= forum_image.MAX_BYTES


def test_tiny_png_is_recompressed(img_env, tmp_path):
    """PNG 不在快路径的格式白名单里 → 重压成 WebP。"""
    p = tmp_path / "shot.png"
    Image.new("RGBA", (900, 700), (255, 255, 255, 0)).save(p, "PNG")

    info = forum_image.store(str(p))
    assert info["rescued"] is True
    out = forum_image.path_of(info["hash"])
    assert out.endswith(".webp")


# ==================== ③ 拒绝 ====================


def test_image_too_large_is_rejected(img_env, tmp_path):
    p = tmp_path / "huge.webp"
    with open(p, "wb") as f:
        f.write(b"\0" * (forum_image.MAX_BYTES + 1024))
    with pytest.raises(forum_image.ImageError) as e:
        forum_image.store(str(p))
    assert "太大" in str(e.value)


def test_non_image_is_rejected(img_env, tmp_path):
    p = tmp_path / "fake.webp"
    p.write_bytes(b"this is definitely not an image" * 100)
    with pytest.raises(forum_image.ImageError):
        forum_image.store(str(p))


def test_empty_file_is_rejected(img_env, tmp_path):
    p = tmp_path / "empty.webp"
    p.write_bytes(b"")
    with pytest.raises(forum_image.ImageError):
        forum_image.store(str(p))


# ==================== ④ 路径穿越 ====================


def test_path_traversal_is_blocked():
    """hash 会被拼进文件路径，凡不是 16 位 hex 的一律不能过。"""
    for bad in [
        "../../etc/passwd",
        "..%2f..%2fetc",
        "a" * 15,
        "a" * 17,
        "ABCDEF0123456789",  # 大写不算
        "abcdef01234567z",  # 非 hex 字符
        "",
        ".",
    ]:
        assert forum_image.is_hash(bad) is False, f"{bad!r} 不该通过"
        assert forum_image.path_of(bad) is None


def test_valid_hash_passes():
    assert forum_image.is_hash("0123456789abcdef") is True


# ==================== ⑤ 去重 ====================


def test_same_content_stored_once(img_env, tmp_path):
    """同一张图传两次，磁盘上只有一份 —— 文件名就是内容哈希。"""
    a = _webp(tmp_path / "x.webp", size=(800, 600))
    b = tmp_path / "y.webp"
    with open(a, "rb") as f:
        b.write_bytes(f.read())

    h1 = forum_image.store(str(a))["hash"]
    h2 = forum_image.store(str(b))["hash"]

    assert h1 == h2
    files = [n for n in os.listdir(forum_image.img_dir()) if n.endswith(".webp")]
    assert len(files) == 1


# ==================== ⑥ 序列化 ====================


def test_dump_parse_roundtrip():
    assert forum_image.dump([]) == ""
    assert forum_image.parse("") == []
    assert forum_image.dump(["a1", "b2"]) == "a1,b2"
    assert forum_image.parse("a1,b2") == ["a1", "b2"]
    # 尾部多余的逗号不能产出空 hash
    assert forum_image.parse("a1,,b2,") == ["a1", "b2"]


# ==================== ⑦ 孤儿清理 ====================


def test_cleanup_removes_unreferenced_old_files(img_env, tmp_path, monkeypatch):
    """没有帖引用的图超过保留期就该删掉。"""
    p = _webp(tmp_path / "orphan.webp", size=(600, 400))
    info = forum_image.store(str(p))
    out = forum_image.path_of(info["hash"])
    assert os.path.isfile(out)

    # 库里没有任何帖（空表），且文件 mtime 拨到 25 小时前
    import core.session as session_mod

    monkeypatch.setattr(session_mod, "_get_conn", lambda: _EmptyConn())
    os.utime(out, (0, 0))
    n = forum_image.cleanup_orphans(keep_hours=24)
    assert n == 1
    assert not os.path.exists(out)


def test_cleanup_keeps_recent_files(img_env, tmp_path, monkeypatch):
    """刚传上来还没发帖的图不能删 —— 用户可能正开着面板在写。"""
    p = _webp(tmp_path / "fresh.webp", size=(600, 400))
    info = forum_image.store(str(p))
    out = forum_image.path_of(info["hash"])

    import core.session as session_mod

    monkeypatch.setattr(session_mod, "_get_conn", lambda: _EmptyConn())
    n = forum_image.cleanup_orphans(keep_hours=24)
    assert n == 0
    assert os.path.exists(out)


class _EmptyConn:
    """假装库里一条帖都没有。"""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a, **k):
        return _EmptyCur()


class _EmptyCur:
    def fetchall(self):
        return []
