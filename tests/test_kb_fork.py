"""同名库复用 + 公开库复制成自己的库。

钉住三件事（都是用户原话里点明的）：
  1. **同一个人**建同名库 → 复用同一个 kb_id。这是"更新自己的公开库"能成立的前提：
     否则别人收藏的那张卡会指向一个只剩半批资料的旧库。
  2. **不同的人**建同名库 → 必须是两个库，谁也不能改到别人头上。
  3. 复制公开库 = 真复制（原件 + 预览件 + 向量都在自己名下），
     不是引用。而且第二次复制同名 → 还是同一个 kb_id。
"""

from __future__ import annotations

import copy
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.config as config  # noqa: E402

# 必须在 import pipelines / rag_core 之前把落盘位置换到临时目录
_TMP = tempfile.mkdtemp(prefix="baomi_kb_fork_")
config.PROJECT_ROOT = os.path.join(_TMP, "proj")
config.PERSIST_DIR = os.path.join(_TMP, "proj", "chroma_db")
config.DATA_DIR = os.path.join(_TMP, "proj", "data")
config.DOWNLOAD_DIR = os.path.join(_TMP, "proj", "downloads")
os.makedirs(config.DATA_DIR, exist_ok=True)

import pytest  # noqa: E402
from langchain_core.embeddings import DeterministicFakeEmbedding  # noqa: E402

import kb.pipelines as pipelines  # noqa: E402
import kb.rag_core as rc  # noqa: E402

rc.get_embeddings = lambda: DeterministicFakeEmbedding(size=64)

ME = "u_fork_me"
OTHER = "u_fork_other"


@pytest.fixture(autouse=True)
def _rollback_registry():
    """每条用例前后把注册表恢复原样。

    为什么必须做：`pipelines` 的四个登记表是**全进程缓存**，chroma client 也是
    单例（绑在第一个 `PERSIST_DIR` 上），所以同一会话里各测试文件其实共用一份
    注册表。这里建的 6 个公开库如果不收回，`test_kb_public` 里
    「广场不该有别人的库」那条就会看到它们而变红 —— 我第一版就是这么红的。
    只恢复内存还不够：`find_by_name` 等读路径会按 mtime 重新读盘，
    所以恢复后要立刻回写。
    """
    names = ("_KB_NAMES", "_KB_OWNERS", "_KB_PUBLIC")
    saved = {n: copy.deepcopy(getattr(pipelines, n)) for n in names}
    saved_files = copy.deepcopy(pipelines._KB_FILES)
    yield
    for n, val in saved.items():
        setattr(pipelines, n, val)
    pipelines._KB_FILES = saved_files
    with pipelines._REGISTRY_LOCK:
        pipelines._save_names()
        pipelines._save_owners()
        pipelines._save_public()
        pipelines._save_files()


def _seed(owner: str, name: str, files: dict[str, str]) -> str:
    """建库 + 落原件 + 建向量，返回 kb_id。走的是上传同一条链路。"""
    kb_id, _ = pipelines.create_kb(owner, name)
    pipelines.ensure_source_dir(kb_id)
    records = []
    for rel, text in files.items():
        dest = pipelines.source_path(kb_id, rel)
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(text)
        with pipelines.kb_lock(kb_id):
            pipe = pipelines.get_or_create(owner, kb_id)
            chunks = pipe.build_index(500, 50, [dest], mode="append", source_names=[rel])
        records.append(
            {
                "name": rel,
                "chunks": int(chunks or 0),
                "added_at": 1763000000,
                "origin": "upload",
                "size": len(text.encode("utf-8")),
                "ext": os.path.splitext(rel)[1].lower().lstrip("."),
                "preview": "",
            }
        )
    pipelines.add_file_records(owner, kb_id, records)
    return kb_id


# ==================== 同名库 ====================


def test_same_owner_same_name_reuses_kb_id():
    """同一个人再建同名库 → 同一个 kb_id（更新语义，不新建）。

    ★ 这一条在正常数据上是**恒真**的：kb_id 就是 hash(owner|name)，
      两次算出来当然一样。它只钉住"名字两头的空格不影响 id"。
      真正打得到 `find_by_name` 的是下面那条历史记录用例。
    """
    kid1, name1 = pipelines.create_kb(ME, "高数错题本")
    kid2, name2 = pipelines.create_kb(ME, "高数错题本")

    assert kid1 == kid2
    assert name1 == name2 == "高数错题本"
    # 名字两头的空格不该造出第二个库
    kid3, _ = pipelines.create_kb(ME, "  高数错题本  ")
    assert kid3 == kid1


def _register_legacy(owner: str, current_name: str, old_name: str) -> str:
    """造一条历史记录：kb_id 是按**旧名字**算的，但登记表里名字已经改成新的。

    线上真出现过这种情况（库改名、以及 claim_orphan_kbs 认领回来的记录），
    此时 hash(owner, new_name) 得到的 id 和存量记录对不上。
    """
    kid = pipelines.new_kb_id(owner, old_name)
    with pipelines._REGISTRY_LOCK:
        pipelines._load_names()
        pipelines._load_owners()
        pipelines._KB_NAMES[kid] = current_name
        pipelines._KB_OWNERS[kid] = owner
        pipelines._save_names()
        pipelines._save_owners()
    return kid


def test_same_name_reuses_renamed_legacy_record():
    """历史记录名下的同名库 → 复用那条，不按新 hash 另建一个只有半批资料的库。

    这是"同学更新自己的公开库"真正依赖的分支：如果这里另建，
    别人收藏的卡片会停在旧库上，更新完全不会被人看到。
    """
    # 名字必须唯一：前面那条用例已经在同一个人名下建过"高数错题本"，
    # 同名的话 find_by_name 会先命中那条，测不到这里要验的分支。
    legacy = _register_legacy(ME, "信号与系统", "信号")
    assert pipelines.new_kb_id(ME, "信号与系统") != legacy  # 确认样本选对了

    kid, _ = pipelines.create_kb(ME, "信号与系统")
    assert kid == legacy
    assert pipelines.name_of(kid) == "信号与系统"
    # 别人同名照样拿不到这条
    assert pipelines.create_kb(OTHER, "信号与系统")[0] != legacy


def test_different_owner_same_name_gets_own_kb():
    """不同的人建同名库 → 各是各的。绝不能合到一起改到别人头上。"""
    kid_me, _ = pipelines.create_kb(ME, "考研英语")
    kid_other, _ = pipelines.create_kb(OTHER, "考研英语")

    assert kid_me != kid_other
    assert pipelines.owner_of(kid_me) == ME
    assert pipelines.owner_of(kid_other) == OTHER


def test_find_by_name_scoped_to_owner():
    kid_me, _ = pipelines.create_kb(ME, "线性代数")

    assert pipelines.find_by_name(ME, "线性代数") == kid_me
    # 别人查这个名字 → 查不到（他名下没有），这是"不能乱改别人的"的地基
    assert pipelines.find_by_name(OTHER, "线性代数") is None
    # 空名字不参与匹配，否则会把一堆无名词典串成一个
    assert pipelines.find_by_name(ME, "   ") is None
    assert pipelines.find_by_name(ME, "没建过的库") is None


# ==================== 复制公开库 ====================


def test_fork_copies_files_and_chunks_into_my_kb():
    src = _seed(OTHER, "数据结构真题", {"chapter1.txt": "栈和队列" * 200})
    pipelines.set_public(OTHER, src, True, desc="历年真题", access="download")

    out = pipelines.fork_public(ME, src)

    kid = out["kb_id"]
    assert kid != src
    assert pipelines.owner_of(kid) == ME
    assert out["from_kb_id"] == src
    # 源库只有一份，复制过来也应当是一份
    assert out["files"] == 1
    assert out["from_files"] == 1
    assert out["chunks"] > 0
    # 显示名沿用源库名 —— 用户不希望复制出来叫"未命名"
    assert out["name"] == "数据结构真题"

    # 真复制：原件在自己名下、向量能查到
    assert os.path.exists(pipelines.source_path(kid, "chapter1.txt"))
    assert pipelines.get_or_create(ME, kid).count() == out["chunks"]
    # 源库一点没动
    assert pipelines.get_or_create(OTHER, src).count() > 0


def test_fork_with_custom_name_uses_it():
    src = _seed(OTHER, "大学物理", {"p1.txt": "刚体定轴转动" * 100})
    pipelines.set_public(OTHER, src, True)

    out = pipelines.fork_public(ME, src, name="物理复习（我的）")
    assert out["name"] == "物理复习（我的）"
    assert pipelines.name_of(out["kb_id"]) == "物理复习（我的）"


def test_fork_twice_same_name_updates_instead_of_duplicating():
    """第二次复制同一个库 → 还是同一个 kb_id（收藏卡片指向稳定）。"""
    src = _seed(OTHER, "概率论", {"p.txt": "大数定律" * 100})
    pipelines.set_public(OTHER, src, True)

    first = pipelines.fork_public(ME, src)
    second = pipelines.fork_public(ME, src)

    assert first["kb_id"] == second["kb_id"]
    # 更新不是追加：同名文件记录仍只有一条
    assert [r["name"] for r in pipelines.get_files(ME, first["kb_id"])] == ["p.txt"]


def test_fork_records_marked_as_fork():
    src = _seed(OTHER, "毛概", {"m.txt": "主要矛盾" * 100})
    pipelines.set_public(OTHER, src, True)

    kid = pipelines.fork_public(ME, src)["kb_id"]
    recs = pipelines.get_files(ME, kid)
    # 出处可查：界面上要能告诉用户"这份是从哪个库复制来的"
    assert [r["origin"] for r in recs] == ["fork"]


def test_fork_unknown_kb_raises():
    with pytest.raises(pipelines.NotOwned):
        pipelines.fork_public(ME, "nosuchkb000")


def test_fork_skips_missing_source_file():
    """原件丢了（磁盘被清过）→ 跳过那一份，不整批失败。"""
    src = _seed(OTHER, "残缺库", {"gone.txt": "会消失" * 100})
    pipelines.set_public(OTHER, src, True)
    os.remove(pipelines.source_path(src, "gone.txt"))

    out = pipelines.fork_public(ME, src)
    assert out["from_files"] == 1  # 源库清单里还有它
    assert out["files"] == 0  # 但一份也没复制过来
    assert out["chunks"] == 0


# ==================== 公开访问方式 ====================


def test_access_defaults_to_preview_and_can_be_set():
    kid, _ = pipelines.create_kb(ME, "访问方式库")

    pipelines.set_public(ME, kid, True)
    # 老库/没传 access 时，别人至少能看清单和预览，但下载要库主点头
    assert pipelines.public_meta(kid).get("access") == "preview"

    pipelines.set_public(ME, kid, True, access="download")
    assert pipelines.public_meta(kid)["access"] == "download"
    # 广场条目也要带上这个标记，否则前端只能自己猜
    entry = next(x for x in pipelines.list_public() if x["kb_id"] == kid)
    assert entry["access"] == "download"

    # 传别的值一律收敛成 preview，不能因为拼错就意外放开下载
    pipelines.set_public(ME, kid, True, access="whatever")
    assert pipelines.public_meta(kid)["access"] == "preview"
