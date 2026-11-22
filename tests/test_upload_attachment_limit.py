"""单次附件份数：普通用户最多 9 个，管理员（ADMIN_UIDS / ADMIN_USERNAMES）不限。

锁三件事：

  1. 第 10 个附件对普通用户是 400，而且**一个字节都不落盘**
     （份数闸在写盘循环之前，不是边写边数）；
  2. 正好 9 个能过；
  3. 管理员同一份请求不受份数约束 —— 但单张 20MB 与本次总量 UPLOAD_TOTAL_MB
     两条大闸不跟着放宽，它们挡的是内存和磁盘，跟"谁能多传几个"是两回事。
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

import api.chat as chat_mod
import core.config as config_mod


class _Part:
    """够用的 UploadFile 替身：只看三个字段（文件名 / 类型 / 声明大小）。"""

    def __init__(self, name: str, size: int = 1024, ctype: str = "image/png") -> None:
        self.filename = name
        self.content_type = ctype
        self.size = size


@pytest.fixture
def saved_files(monkeypatch, tmp_path):
    """把落盘、清理、配额、格式判断全换成记录器，返回"实际写了几次"。"""
    saved: list[str] = []
    monkeypatch.setattr(chat_mod, "CHAT_UPLOAD_DIR", str(tmp_path / "chat_uploads"))
    monkeypatch.setattr(chat_mod, "_cleanup_old_uploads", lambda: None)
    monkeypatch.setattr(chat_mod, "_is_supported_image", lambda name, ctype: True)

    async def fake_save_stream(f, path: str, limit: int) -> int:
        saved.append(path)
        return int(getattr(f, "size", 0) or 0)

    monkeypatch.setattr(chat_mod.upload, "save_stream", fake_save_stream)
    monkeypatch.setattr(chat_mod.quota, "consume", lambda *a, **k: 0)
    return saved


def _upload(files, monkeypatch, *, admin: bool):
    monkeypatch.setattr(chat_mod.quota, "_is_admin", lambda uid: admin)
    return asyncio.run(chat_mod.upload_attachments(files=files, uid="u-test"))


def test_normal_user_over_limit_is_rejected_before_writing(saved_files, monkeypatch):
    files = [_Part(f"a{i}.png") for i in range(10)]
    with pytest.raises(HTTPException) as exc:
        _upload(files, monkeypatch, admin=False)
    assert exc.value.status_code == 400
    assert "9" in str(exc.value.detail)
    # 闸在循环之前：不能出现"前 9 个已经落地、第 10 个才报错"的孤儿文件
    assert saved_files == []


def test_normal_user_exactly_nine_passes(saved_files, monkeypatch):
    files = [_Part(f"a{i}.png") for i in range(9)]
    out = _upload(files, monkeypatch, admin=False)
    assert len(out["files"]) == 9
    assert len(saved_files) == 9


def test_admin_has_no_count_cap(saved_files, monkeypatch):
    files = [_Part(f"a{i}.png") for i in range(12)]
    out = _upload(files, monkeypatch, admin=True)
    assert len(out["files"]) == 12
    assert len(saved_files) == 12


def test_admin_still_subject_to_total_size_cap(saved_files, monkeypatch):
    """份数不限 ≠ 大小不限：12×20MB 是一次 240MB 的请求，2G 内存的机器扛不住。"""
    monkeypatch.setattr(config_mod, "UPLOAD_TOTAL_MB", 200)
    files = [_Part(f"a{i}.png", size=20 * 1024 * 1024) for i in range(12)]
    with pytest.raises(HTTPException) as exc:
        _upload(files, monkeypatch, admin=True)
    assert exc.value.status_code == 400
    assert "200" in str(exc.value.detail)
    assert saved_files == []


def test_admin_still_rejects_non_image(saved_files, monkeypatch):
    """放开的是份数，不是格式：非图片照样整批拒。"""
    monkeypatch.setattr(chat_mod, "_is_supported_image", lambda name, ctype: False)
    files = [_Part("notes.pdf", ctype="application/pdf")]
    with pytest.raises(HTTPException) as exc:
        _upload(files, monkeypatch, admin=True)
    assert exc.value.status_code == 400
    assert saved_files == []
