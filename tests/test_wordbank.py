"""词库数据契约（frontend/public/data/words/）。

为什么这个要用 pytest 钉住，而不是只靠前端探针：
  词库是 `tools/build_wordlist.py` 从上游 txt 生成的**产物**，不是手写常量。
  重新生成、换上游、改清洗规则都会悄悄改掉它的形状 —— 而形状一坏，
  前端不会报错，只会表现为"组数不对""某个词重复出现""释义空白"，
  探针里那些「第 1 组 · 1 / 20」的断言也跟着失去意义。
  所以在这里把契约写成断言：级别清单 / 数量 / 分组 / 去重 / 许可证。

★ 提醒：测试读的是仓库里的 JSON，不联网、不重新生成。
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import pytest

WORDS_DIR = Path(__file__).resolve().parent.parent / "frontend" / "public" / "data" / "words"
WORD_RE = re.compile(r"^[a-z][a-z'.\-]*$")


@pytest.fixture(scope="module")
def index() -> dict:
    path = WORDS_DIR / "index.json"
    assert path.is_file(), f"缺 {path}，先跑 python tools/build_wordlist.py"
    return json.loads(path.read_text(encoding="utf-8"))


def _load(level_id: str) -> dict:
    return json.loads((WORDS_DIR / f"{level_id.lower()}.json").read_text(encoding="utf-8"))


def test_index_declares_every_level_file(index: dict) -> None:
    """清单里每一级都得有对应文件，反过来也不许有野文件。"""
    ids = [lv["id"] for lv in index["levels"]]
    assert ids, "级别清单不能为空"
    for lid in ids:
        assert (WORDS_DIR / f"{lid.lower()}.json").is_file(), f"{lid} 缺词库文件"
    on_disk = {p.stem.upper() for p in WORDS_DIR.glob("*.json") if p.stem != "index"}
    assert on_disk == set(ids), f"文件与清单不一致：磁盘 {on_disk}，清单 {set(ids)}"


def test_counts_and_groups_match_files(index: dict) -> None:
    """清单里写的 count / groups 必须和词库文件真实内容对得上。"""
    gs = index["group_size"]
    assert gs > 0
    for lv in index["levels"]:
        words = _load(lv["id"])["words"]
        assert len(words) == lv["count"], f"{lv['id']} 数量对不上：{len(words)} vs {lv['count']}"
        assert lv["groups"] == math.ceil(len(words) / gs), f"{lv['id']} 组数对不上"


def test_every_entry_is_well_formed(index: dict) -> None:
    """[词, 释义] 二元组；词是小写纯英文，释义非空且含词性标记。"""
    for lv in index["levels"]:
        bad_word, bad_mean = [], []
        for item in _load(lv["id"])["words"]:
            assert isinstance(item, list) and len(item) == 2, f"条目形状不对：{item}"
            w, m = item
            if not isinstance(w, str) or not WORD_RE.match(w):
                bad_word.append(w)
            if not isinstance(m, str) or not m.strip():
                bad_mean.append(w)
        assert not bad_word, f"{lv['id']} 有非法拼写：{bad_word[:8]}"
        assert not bad_mean, f"{lv['id']} 有空释义：{bad_mean[:8]}"


def test_no_duplicate_words_across_levels(index: dict) -> None:
    """同一个词不许在两个级别里各出现一次 —— 否则六级组里会冒出四级词。"""
    seen: dict[str, str] = {}
    dup: list[str] = []
    for lv in index["levels"]:
        for w, _ in _load(lv["id"])["words"]:
            if w in seen:
                dup.append(f"{w}({seen[w]}/{lv['id']})")
            else:
                seen[w] = lv["id"]
    assert not dup, f"跨级别重复 {len(dup)} 个：{dup[:8]}"


def test_definitions_are_trimmed(index: dict) -> None:
    """释义不能过长（卡片放不下），也不许残留上游的「， 空格」排版。"""
    too_long, spaced = [], []
    for lv in index["levels"]:
        for w, m in _load(lv["id"])["words"]:
            if len(m) > 60:
                too_long.append((w, len(m)))
            if re.search(r"[，；、。：]\s", m):
                spaced.append(w)
    assert not too_long, f"释义过长：{too_long[:5]}"
    assert not spaced, f"释义里有多余空格：{spaced[:5]}"


def test_license_file_present(index: dict) -> None:
    """上游是 BSD-3-Clause，许可证要求随发行保留版权声明 —— 文件必须在。"""
    lic = WORDS_DIR / "LICENSE.txt"
    assert lic.is_file(), "缺 LICENSE.txt（BSD-3 要求随发行保留）"
    text = lic.read_text(encoding="utf-8")
    assert "BSD 3-Clause" in text
    assert index["source"]["license"] == "BSD-3-Clause"
    assert "KyleBing" in text
