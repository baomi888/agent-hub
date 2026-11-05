"""知识库注册表的跨进程可见性。

板块 D 起，建库不再只发生在 API 进程里：后台 worker 的自动沉淀也会建库，
而 API 与 worker 是**两个服务**（生产上是两个 systemd unit）。两者唯一的
交汇点是 ``data/kb_owners.json`` / ``data/kb_names.json`` 两个文件。

原来的读法是"内存表为空才读一次" —— 于是 API 进程启动后就再也不看磁盘了，
worker 建的库在网页上永远不存在，重启后端才冒出来。这不是显示问题：
用户接着要往里存东西、要拿它做检索，看不见就等于没建成。

★ 用例的前提必须让内存表**非空**再写盘。第一版忘了这一步：初始表为空时
  "空表就读"恰好也会重读，于是把修复改回去测试照样全绿 —— 负向验证才发现的。
  真实环境里注册表有十几条记录，永远不会是空的。

这里不碰真库、不联网：PROJECT_ROOT 指到 tmp_path，直接用文件模拟"另一个进程"。
"""

from __future__ import annotations

import json
import os

import pytest

from core import config
from kb import pipelines

OWNER = "reload_owner"


@pytest.fixture()
def registry(tmp_path, monkeypatch):
    """把注册表落到临时目录，并把进程内缓存清空。"""
    root = tmp_path / "proj"
    (root / "data").mkdir(parents=True)
    monkeypatch.setattr(config, "PROJECT_ROOT", str(root))
    monkeypatch.setattr(pipelines, "_KB_OWNERS", {}, raising=False)
    monkeypatch.setattr(pipelines, "_KB_NAMES", {}, raising=False)
    monkeypatch.setattr(pipelines, "_KB_OWNERS_MTIME", None, raising=False)
    monkeypatch.setattr(pipelines, "_KB_NAMES_MTIME", None, raising=False)
    monkeypatch.setattr(pipelines, "_KB_LOCKS", {}, raising=False)
    return root / "data"


def _write(dirpath, owners: dict, names: dict, *, bump: float = 5.0) -> None:
    """模拟另一个进程落盘。

    ★ 写完要把 mtime 往后推：同一毫秒内写两次，mtime 可能和本进程记下的那个
      一样，于是"文件变了"这件事看不出来 —— 生产上两次建库至少隔几秒，
      但测试里是连着的，不显式推一下就会变成偶发失败。
    """
    for fname, data in (("kb_owners.json", owners), ("kb_names.json", names)):
        p = dirpath / fname
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        st = os.stat(p)
        os.utime(p, (st.st_atime, st.st_mtime + bump))


def _boot_with_existing_kb(registry) -> None:
    """模拟"后端已经跑起来了，注册表里本来就有别的库"。

    ★ 这一步是用例有效性的前提：内存表非空之后，旧实现就再也不读盘了。
    """
    _write(registry, {"kb_seed": OWNER}, {"kb_seed": "早就有的库"})
    assert pipelines.owned_kb_ids(OWNER) == {"kb_seed"}, "前置：内存表得先有内容"
    assert pipelines.name_of("kb_seed") == "早就有的库"


def test_owner_created_by_worker_becomes_visible(registry):
    """worker 建的库，API 进程不重启也能列出来。"""
    _boot_with_existing_kb(registry)

    _write(registry, {"kb_seed": OWNER, "kb_worker001": OWNER}, {"kb_seed": "早就有的库"})

    assert pipelines.owned_kb_ids(OWNER) == {"kb_seed", "kb_worker001"}
    assert pipelines.owner_of("kb_worker001") == OWNER


def test_name_created_by_worker_becomes_visible(registry):
    """库名同步可见 —— 否则列表里会出现"名字 = kb_id"这种半生不熟的一行。"""
    _boot_with_existing_kb(registry)

    _write(
        registry,
        {"kb_seed": OWNER},
        {"kb_seed": "早就有的库", "kb_worker002": "对话沉淀·周会纪要"},
    )

    assert pipelines.name_of("kb_worker002") == "对话沉淀·周会纪要"


def test_list_all_shows_worker_kb_with_real_name(registry):
    """端到端那一层：worker 建成但还没传过文件的空库，也要带着名字出现在列表里。"""
    _boot_with_existing_kb(registry)

    _write(
        registry,
        {"kb_seed": OWNER, "kb_worker003": OWNER},
        {"kb_seed": "早就有的库", "kb_worker003": "对话沉淀·选课"},
    )

    rows = pipelines.list_all(OWNER)
    got = [r for r in rows if r["kb_id"] == "kb_worker003"]
    assert got, f"列表里没有这个库：{[r['kb_id'] for r in rows]}"
    assert got[0]["name"] == "对话沉淀·选课", "名字没刷新出来，回退成 kb_id 了"


def test_other_owners_kb_stays_hidden(registry):
    """刷新不能把归属判错：别人的库依旧不可见。"""
    _boot_with_existing_kb(registry)

    _write(
        registry,
        {"kb_seed": OWNER, "kb_theirs": "someone_else"},
        {"kb_seed": "早就有的库", "kb_theirs": "别人的"},
    )
    assert pipelines.owned_kb_ids(OWNER) == {"kb_seed"}
    assert pipelines.owner_of("kb_theirs") == "someone_else"
