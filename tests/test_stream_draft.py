"""回归：assistant 消息的"草稿行"生命周期（2026-11-19，方案二）。

背景（线上问题：两台设备切换时，正在生成的回答"消失"）：
以前 assistant 消息是**流结束后才 INSERT**，所以 SSE 一断（锁屏、切网、
切设备、`--reload` 重启）正在生成的内容就没了，数据库里查无此条。

现在改成：
    1. 流开始前先 INSERT 一条空壳（`status='streaming'`）；
    2. 流式过程中每 400 字 / 4 秒 UPDATE 一次正文；
    3. 正常结束 → `status='done'`（同一条行，不再 INSERT，回答按"提问时刻"排序）；
       断连则 → `status='interrupted'`，一个字都没生成就把空壳删掉。

本文件锁死 store 层与组装层的配套语义（流式链路本身在 test_sse_disconnect.py）：
  1. `update_message_content` 能写正文/状态，但不许跨 owner、不许写非法状态；
  2. `get_messages` 带出 `status`；
  3. `_clean` 跳过 `streaming`、给 `interrupted` 标注"只有前半段"——否则半截
     回答会被当成完整历史喂给下一轮，模型会以为自己已经答过了；
  4. 启动收割：进程被 kill 时留下的 `streaming` 老行，重启后要变成 `interrupted`。
"""

from __future__ import annotations

import os

import pytest

import core.context_compress as cc
import core.session as session_mod


@pytest.fixture(autouse=True)
def _ensure_tables():
    session_mod._init_db()
    yield


@pytest.fixture
def fresh_db(monkeypatch, tmp_path):
    db = os.path.join(str(tmp_path), "sessions.db")
    monkeypatch.setattr(session_mod, "_db_path", lambda: db)
    session_mod._init_db()
    return db


def _conv(owner: str = "u-dev") -> str:
    return session_mod.create_conversation(owner, "草稿")["id"]


# ---------- 1. update_message_content ----------


def test_update_message_content_writes_text_and_status(fresh_db):
    sid = _conv()
    mid = session_mod.add_message(
        "u-dev", sid, "assistant", "", status=session_mod.MSG_STATUS_STREAMING
    )
    assert session_mod.get_message("u-dev", sid, mid)["status"] == "streaming"

    assert session_mod.update_message_content(
        "u-dev", sid, mid, "生成到一半", status=session_mod.MSG_STATUS_INTERRUPTED
    )
    row = session_mod.get_message("u-dev", sid, mid)
    assert row["content"] == "生成到一半"
    assert row["status"] == "interrupted"

    # refs 不传时保持原样（流末尾才定引用，中途 UPDATE 不能把它清空）
    assert session_mod.update_message_content(
        "u-dev", sid, mid, "写完了", status=session_mod.MSG_STATUS_DONE, refs="[1]"
    )
    assert session_mod.get_message("u-dev", sid, mid)["refs"] == "[1]"
    assert session_mod.update_message_content(
        "u-dev", sid, mid, "写完了，再改一次"
    )
    row = session_mod.get_message("u-dev", sid, mid)
    assert row["refs"] == "[1]" and row["status"] == "done"


def test_update_message_content_rejects_unknown_status(fresh_db):
    sid = _conv()
    mid = session_mod.add_message("u-dev", sid, "assistant", "x")
    with pytest.raises(ValueError):
        session_mod.update_message_content("u-dev", sid, mid, "x", status="weird")


def test_update_message_content_is_owner_isolated(fresh_db):
    sid = _conv("u-a")
    mid = session_mod.add_message("u-a", sid, "assistant", "原始")
    assert session_mod.update_message_content("u-b", sid, mid, "被篡改") is False
    assert session_mod.get_message("u-a", sid, mid)["content"] == "原始"


# ---------- 2. get_messages 带出 status ----------


def test_get_messages_exposes_status(fresh_db):
    sid = _conv()
    session_mod.add_message("u-dev", sid, "user", "问")
    session_mod.add_message(
        "u-dev", sid, "assistant", "", status=session_mod.MSG_STATUS_STREAMING
    )
    roles = [(m["role"], m["status"]) for m in session_mod.get_messages("u-dev", sid)]
    assert ("user", "done") in roles
    assert ("assistant", "streaming") in roles


# ---------- 3. 历史组装 ----------


def test_clean_skips_streaming_and_marks_interrupted():
    raw = [
        {"id": 1, "role": "user", "content": "问", "status": "done"},
        {"id": 2, "role": "assistant", "content": "另一台设备正在写", "status": "streaming"},
        {"id": 3, "role": "user", "content": "再问", "status": "done"},
        {"id": 4, "role": "assistant", "content": "答到一半", "status": "interrupted"},
    ]
    out = cc._clean(raw)
    assert [m["content"][:2] for m in out] == ["问", "再问", "答到"]
    assert out[-1]["content"].startswith("答到一半")
    assert "中断" in out[-1]["content"], "半截回答必须留下痕迹，不能被当成完整答案"


def test_clean_keeps_legacy_rows_without_status():
    """老库没有 status 字段的那批消息，读完仍是完整历史。"""
    out = cc._clean(
        [{"id": 1, "role": "user", "content": "问"}, {"id": 2, "role": "assistant", "content": "答"}]
    )
    assert [m["content"] for m in out] == ["问", "答"]


def test_maybe_compress_ignores_streaming_rows(fresh_db, monkeypatch):
    """另一台设备的半成品不能进压缩摘要。"""
    sid = _conv()
    session_mod.add_message("u-dev", sid, "user", "问")
    session_mod.add_message(
        "u-dev", sid, "assistant", "正在写", status=session_mod.MSG_STATUS_STREAMING
    )
    seen: list[list] = []
    monkeypatch.setattr(
        cc, "build_compressed_segment", lambda msgs, **kw: seen.append(msgs) or ""
    )
    cc.maybe_compress("u-dev", sid, keep_turns=1, force=True)
    for batch in seen:
        assert all(m.get("status") != "streaming" for m in batch)


# ---------- 4. 启动收割 ----------


def test_startup_reaper_flips_every_streaming_row(fresh_db):
    """进程重启时，库里所有 streaming 行都要改判 interrupted——**不看新旧**。

    这里刻意用一条"刚刚创建"的行：前端会整条跳过 streaming（另一台设备正在写
    的那条不能渲染），所以只要漏收割一条，那条回答就是永久消失，刷新也看不见。
    第一版实现加了 15 分钟时间窗，于是"进程 5 分钟前死了 → 重启 → 没收割 →
    前端永远跳过"，正是要消灭的现象换条路回来。本用例就是防止它再长出来。
    """
    sid = _conv()
    stale = session_mod.add_message(
        "u-dev", sid, "assistant", "半个答案", status=session_mod.MSG_STATUS_STREAMING
    )

    session_mod._init_db()  # 模拟重启

    row = session_mod.get_message("u-dev", sid, stale)
    assert row["status"] == "interrupted", "重启后不该有任何行还卡在 streaming"
    assert row["content"] == "半个答案", "收割只改状态，不能丢正文"
