"""站内公开库：开关、跨用户只读检索、写路径隔离。

只直接调用 `kb.pipelines` 与 `api.kb` 的函数（显式传 uid，绕过鉴权依赖），
不启 HTTP 服务、不联网、不调真 LLM：embedding 换确定性假实现，
chroma / 数据目录全部落在临时目录。

钉住的是公开语义的边界：
  - 公开只放开「只读检索」（query / 对话挂载 / kb_search 工具）；
  - 文件清单、上传、删除、重建、visibility 开关本身仍然只认 owner；
  - 无权访问与不存在对外长得一样（404），收回公开后他人立即不可再检索。
"""

import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.config as config  # noqa: E402

# 必须在 import pipelines / rag_core 之前把落盘位置换到临时目录
_TMP = tempfile.mkdtemp(prefix="baomi_kb_public_")
config.PROJECT_ROOT = os.path.join(_TMP, "proj")
config.PERSIST_DIR = os.path.join(_TMP, "proj", "chroma_db")
config.DATA_DIR = os.path.join(_TMP, "proj", "data")
config.DOWNLOAD_DIR = os.path.join(_TMP, "proj", "downloads")
os.makedirs(config.DATA_DIR, exist_ok=True)

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from langchain_core.embeddings import DeterministicFakeEmbedding  # noqa: E402

import api.kb as kb_api  # noqa: E402
import kb.pipelines as pipelines  # noqa: E402
import kb.rag_core as rc  # noqa: E402

rc.get_embeddings = lambda: DeterministicFakeEmbedding(size=64)

OWNER = "u_owner"
OTHER = "u_other"


def _no_quota(monkeypatch):
    monkeypatch.setattr(kb_api.quota, "guard", lambda *a, **k: None)
    monkeypatch.setattr(kb_api.quota, "consume", lambda *a, **k: None)


@pytest.fixture()
def kb_id():
    """库主建一个含真实片段的库，测完连公开登记一起清掉。"""
    kid, _ = pipelines.create_kb(OWNER, "公开测试库")
    yield kid
    pipelines.delete_kb(OWNER, kid)


# ==================== 开关：只有库主能切 ====================


def test_set_public_roundtrip(kb_id):
    assert pipelines.is_public(kb_id) is False  # 新库默认私有

    pipelines.set_public(OWNER, kb_id, True)
    assert pipelines.is_public(kb_id) is True

    pipelines.set_public(OWNER, kb_id, False)
    assert pipelines.is_public(kb_id) is False


def test_set_public_rejects_non_owner(kb_id):
    with pytest.raises(pipelines.NotOwned):
        pipelines.set_public(OTHER, kb_id, True)
    assert pipelines.is_public(kb_id) is False  # 拒绝的同时不能留下半开状态


# ==================== 跨用户只读：放行检索，不放行其余 ====================


def test_public_kb_readable_by_others(kb_id):
    pipelines.set_public(OWNER, kb_id, True)

    # 公开库：别人的进程里也能取到实例、确认存在
    pipe = pipelines.get_or_create(OTHER, kb_id, allow_public=True)
    assert pipe.collection_name == f"kb_{kb_id}"
    assert pipelines.exists(OTHER, kb_id, allow_public=True) is False  # 空库 peek()==0


def test_private_kb_still_rejected_for_others(kb_id):
    # 不公开时 allow_public 也救不了：这是默认拒绝语义，改不得
    with pytest.raises(pipelines.NotOwned):
        pipelines.get_or_create(OTHER, kb_id, allow_public=True)
    assert pipelines.exists(OTHER, kb_id, allow_public=True) is False


def test_delete_kb_clears_public_flag(kb_id):
    pipelines.set_public(OWNER, kb_id, True)
    pipelines.delete_kb(OWNER, kb_id)
    assert pipelines.is_public(kb_id) is False
    # 残留标记也不该出现在公开列表里（无主库谁也检索不了）
    assert all(k["kb_id"] != kb_id for k in pipelines.list_public())


# ==================== API 层：query 放行，其余仍 owner-only ====================


def test_query_open_to_others_when_public(monkeypatch, kb_id):
    _no_quota(monkeypatch)
    pipelines.set_public(OWNER, kb_id, True)
    # LLM 问答换桩：这里只验证「门放行了、拿到的是这个库的 pipe」
    monkeypatch.setattr(
        kb_api.pipelines,
        "get_or_create",
        lambda owner, kid, **k: SimpleNamespace(
            answer=lambda q, top_k: (f"answered for {owner}", "refs"), peek=lambda: 1
        ),
    )
    out = asyncio.run(kb_api.query_kb(kb_id, "什么是公开库", 3, OTHER))
    assert out["answer"] == "answered for u_other"


def test_query_private_returns_404_for_others(monkeypatch, kb_id):
    _no_quota(monkeypatch)
    with pytest.raises(HTTPException) as e:
        asyncio.run(kb_api.query_kb(kb_id, "悄悄看一眼", 3, OTHER))
    assert e.value.status_code == 404  # 404 而非 403：不泄露存在性


def test_files_and_visibility_stay_owner_only(monkeypatch, kb_id):
    """公开放开的是检索，不是整个库：文件级接口与开关仍只认库主。"""
    _no_quota(monkeypatch)
    pipelines.set_public(OWNER, kb_id, True)

    with pytest.raises(HTTPException):
        asyncio.run(kb_api.list_files(kb_id, OTHER))
    with pytest.raises(HTTPException):
        asyncio.run(kb_api.set_kb_visibility(kb_id, kb_api.VisibilityRequest(public=False), OTHER))

    # 库主自己不受影响。返回里多了 desc：广场卡片要用它显示简介
    out = asyncio.run(kb_api.set_kb_visibility(kb_id, kb_api.VisibilityRequest(public=False), OWNER))
    assert out == {"kb_id": kb_id, "public": False, "desc": ""}


def test_public_list_excludes_caller_and_private(monkeypatch, kb_id):
    _no_quota(monkeypatch)
    pipelines.set_public(OWNER, kb_id, True)

    mine = asyncio.run(kb_api.list_public_kbs(OWNER))
    assert mine["kbs"] == []  # 自己的公开库不重复出现（自己的列表里已经有了）

    theirs = asyncio.run(kb_api.list_public_kbs(OTHER))
    assert [k["kb_id"] for k in theirs["kbs"]] == [kb_id]
    # 字段集合钉死：广场要展示的资料够用，且**没有**文件清单。
    # 一旦有人往这里顺手加了 files 明细、owner uid 之类，这条会红。
    assert all(set(k) == {"kb_id", "name", "chunks", "files", "owner_name", "desc", "at"} for k in theirs["kbs"])

    pipelines.set_public(OWNER, kb_id, False)
    assert asyncio.run(kb_api.list_public_kbs(OTHER))["kbs"] == []


# ==================== 广场化：简介与展示用元信息（2026-11-13） ====================


def test_public_desc_roundtrip(kb_id):
    """简介写得进、读得出；改简介不动分享时间；不传 desc 不覆盖已有简介。"""
    pipelines.set_public(OWNER, kb_id, True, "考研数学一，近十年真题按章节整理")
    item = next(k for k in pipelines.list_public() if k["kb_id"] == kb_id)
    assert item["desc"] == "考研数学一，近十年真题按章节整理"
    assert item["at"] > 0
    first_at = item["at"]

    # 只改开关（desc=None）不能把用户写的话抹掉
    pipelines.set_public(OWNER, kb_id, True)
    item = next(k for k in pipelines.list_public() if k["kb_id"] == kb_id)
    assert item["desc"] == "考研数学一，近十年真题按章节整理"
    # 改简介不该让「分享于」变成今天
    assert item["at"] == first_at

    # 显式传空串才是清空
    pipelines.set_public(OWNER, kb_id, True, "")
    item = next(k for k in pipelines.list_public() if k["kb_id"] == kb_id)
    assert item["desc"] == ""


def test_withdraw_clears_registry_entry(kb_id):
    """收回 = 整条登记删掉，包括简介。

    这是刻意的取舍：留着 on=False 的壳可以省掉"下次再分享要重写简介"，
    但那样「在表里」就不等于「公开」了，所有读点都得再判一次 on，
    漏一处就把私有库当公开。安全边界不拿便利换。
    """
    pipelines.set_public(OWNER, kb_id, True, "计算机组成原理笔记")
    pipelines.set_public(OWNER, kb_id, False)
    assert pipelines.is_public(kb_id) is False
    mine = pipelines.list_all(OWNER)
    row = next(k for k in mine if k["kb_id"] == kb_id)
    assert row["public"] is False
    assert row["public_desc"] == ""  # 跟着登记一起没了
    # 也不能在广场上留一行
    assert all(k["kb_id"] != kb_id for k in pipelines.list_public())


def test_legacy_bool_format_still_loads(kb_id):
    """旧登记表 {"kb_id": true} 要能被读成新结构，不能因为格式变了就整表丢光。"""
    import json

    path = pipelines._public_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump({kb_id: True}, f)
    pipelines._KB_PUBLIC_MTIME = None  # 逼它重读
    pipelines._load_public()
    assert pipelines.is_public(kb_id) is True
    item = next(k for k in pipelines.list_public() if k["kb_id"] == kb_id)
    assert item["desc"] == ""  # 旧数据没有简介，不编一个出来
    assert item["at"] == 0  # 也没有分享时间，前端会显示「较早分享」


def test_list_public_counts_files_without_leaking_names(kb_id):
    """广场要文件数，但只能给数量 —— 名字一个都不能出去。"""
    pipelines.add_file_records(
        OWNER,
        kb_id,
        [
            {"name": "真题2015.pdf", "chunks": 12, "added_at": 1},
            {"name": "错题本.md", "chunks": 5, "added_at": 2},
        ],
    )
    pipelines.set_public(OWNER, kb_id, True)
    item = next(k for k in pipelines.list_public() if k["kb_id"] == kb_id)
    assert item["files"] == 2
    assert "真题2015.pdf" not in str(item)
    assert "错题本.md" not in str(item)
