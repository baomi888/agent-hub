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
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORDS_DIR = ROOT / "frontend" / "public" / "data" / "words"
WORD_RE = re.compile(r"^[a-z][a-z'.\-]*$")

# 例句判定复用生成脚本那一套 (tools/wordrules.py)，不在这里另写一份 ——
# 两边判据一旦分叉，就会出现"生成时判不合格、入库时判合格"的鬼故事。
sys.path.insert(0, str(ROOT / "tools"))
import wordrules  # noqa: E402


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
    """条目不外两种形状：`[词, 释义]` 或带例句的 `[词, 释义, 例句, 译文]`。

    ★ 为什么允许两种：没有例句时退化成二元组比补两个空串更省字节（6661 词 × 2 空串
      在 gzip 后也要多出几 KB），前端 `it[2] && it[3]` 一句就判完了。
      但长度只许是 2 或 4 —— 3 元组或 5 元组一定是生成脚本哪里写歪了。
    """
    for lv in index["levels"]:
        bad_word, bad_mean, bad_shape = [], [], []
        for item in _load(lv["id"])["words"]:
            if not isinstance(item, list) or len(item) not in (2, 4):
                bad_shape.append(item)
                continue
            w, m = item[0], item[1]
            if not isinstance(w, str) or not WORD_RE.match(w):
                bad_word.append(w)
            if not isinstance(m, str) or not m.strip():
                bad_mean.append(w)
        assert not bad_shape, f"{lv['id']} 条目形状不对：{bad_shape[:3]}"
        assert not bad_word, f"{lv['id']} 有非法拼写：{bad_word[:8]}"
        assert not bad_mean, f"{lv['id']} 有空释义：{bad_mean[:8]}"


def test_no_duplicate_words_across_levels(index: dict) -> None:
    """同一个词不许在两个级别里各出现一次 —— 否则六级组里会冒出四级词。"""
    seen: dict[str, str] = {}
    dup: list[str] = []
    for lv in index["levels"]:
        for item in _load(lv["id"])["words"]:
            w = item[0]
            if w in seen:
                dup.append(f"{w}({seen[w]}/{lv['id']})")
            else:
                seen[w] = lv["id"]
    assert not dup, f"跨级别重复 {len(dup)} 个：{dup[:8]}"


def test_definitions_are_trimmed(index: dict) -> None:
    """释义不能过长（卡片放不下），也不许残留上游的「， 空格」排版。"""
    too_long, spaced = [], []
    for lv in index["levels"]:
        for item in _load(lv["id"])["words"]:
            w, m = item[0], item[1]
            if len(m) > 60:
                too_long.append((w, len(m)))
            if re.search(r"[，；、。：]\s", m):
                spaced.append(w)
    assert not too_long, f"释义过长：{too_long[:5]}"
    assert not spaced, f"释义里有多余空格：{spaced[:5]}"


# ---------------------------------------------------------------- 例句 --
# 2026-10-28 用户："然后这个单词卡就只有 40 个单词吗，这么少" → "带例句吧"。
# 例句是这次加的**内容**，不是排版，所以契约也钉在这里：
# 每个词都得有、句子得真是句子、译文得是中文、句子里得真出现这个词。


def test_every_word_has_examples(index: dict) -> None:
    """清单里的 with_examples 必须等于 count —— 一个都不能少。

    ★ 别让这条被"大部分有"糊弄过去：前端只在 `it[2] && it[3]` 成立时才渲染例句块，
      少一个词就是那一张卡翻过来光秃秃，用户不会报错、只会觉得"有时候没有"。
    """
    for lv in index["levels"]:
        words = _load(lv["id"])["words"]
        assert lv["with_examples"] == lv["count"], (
            f"{lv['id']} 例句覆盖不齐：{lv['with_examples']} / {lv['count']}"
        )
        missing = [it[0] for it in words if len(it) < 4]
        assert not missing, f"{lv['id']} 有 {len(missing)} 个词没例句：{missing[:8]}"


def test_examples_are_real_sentences(index: dict) -> None:
    """例句过关：是完整句、含目标词、译文是中文。判定用 tools/wordrules.py 同一套。"""
    bad: list[tuple[str, str]] = []
    for lv in index["levels"]:
        for it in _load(lv["id"])["words"]:
            why = wordrules.check_bank(it[0], it[2], it[3])
            if why:
                bad.append((it[0], why))
    assert not bad, f"{len(bad)} 条例句不合格：{bad[:8]}"


def test_example_translations_are_clean(index: dict) -> None:
    """译文里不许留上游的括号补注 / 把释义当译文。

    上游原文长这样：`她读了一首被命名为《苹果树》的诗( 她读了一首题为《苹果树》的诗)。`
    卡面把这句话当成"翻译"给用户读，比没有例句更糟。修复表在
    tools/build_wordlist.py::REPAIR_TRANSLATIONS，这里只负责钉住结果。
    """
    dirty = []
    for lv in index["levels"]:
        for it in _load(lv["id"])["words"]:
            if re.search(r"[(（]", it[3]):
                dirty.append((it[0], it[3]))
            elif it[3].strip() == it[1].strip():
                dirty.append((it[0], "译文等于释义"))
    assert not dirty, f"{len(dirty)} 条译文不干净：{dirty[:5]}"


def test_license_file_present(index: dict) -> None:
    """上游是 BSD-3-Clause，许可证要求随发行保留版权声明 —— 文件必须在。"""
    lic = WORDS_DIR / "LICENSE.txt"
    assert lic.is_file(), "缺 LICENSE.txt（BSD-3 要求随发行保留）"
    text = lic.read_text(encoding="utf-8")
    assert "BSD 3-Clause" in text
    assert index["source"]["license"] == "BSD-3-Clause"
    assert "KyleBing" in text
