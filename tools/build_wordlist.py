"""把公开四六级词表（KyleBing/english-vocabulary, BSD-3-Clause）整理成前端可直接用的 JSON。

为什么要这一步，而不是直接把 txt 丢给前端：
  1. 原始 txt 里同一个词会重复出现 2~3 次（不同释义），直接读会得到「背过的词又冒出来」；
  2. 有 2 行字段数不对（混进了音标列），按 tab 切会把音标当释义显示；
  3. 最长释义 86 字，卡片正面塞不下，需要按标点截断；
  4. 前端只认 [词, 释义] 二元组，压缩体积。

用法（在仓库根目录）：
    python tools/build_wordlist.py                 # 自动下载源文件到临时目录
    python tools/build_wordlist.py --src <目录>    # 用本地已下载的 cet4.txt / cet6.txt

产物：
    frontend/public/data/words/index.json     级别清单 + 分组大小 + 来源署名
    frontend/public/data/words/cet4.json      四级词库
    frontend/public/data/words/cet6.json      六级词库
    frontend/public/data/words/LICENSE.txt    上游许可证（BSD-3 要求随发行保留）
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
import urllib.request
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "frontend" / "public" / "data" / "words"

GROUP_SIZE = 20
MAX_DEF = 52

# 上游：BSD-3-Clause，全中文释义，四六级各一套
UPSTREAM = "https://raw.githubusercontent.com/KyleBing/english-vocabulary/master"
SOURCES = [
    ("CET4", "四级核心", "3 四级-乱序.txt", "cet4.txt"),
    ("CET6", "六级扩展", "4 六级-乱序.txt", "cet6.txt"),
]

WORD_RE = re.compile(r"^[A-Za-z][A-Za-z'.\-]*$")
PHONETIC_RE = re.compile(r"\[[^\]]*\]")          # 音标列，卡片上不显示
SPACE_RE = re.compile(r"[ \u3000]+")


def fetch(name: str, dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 1000:
        return dest
    url = f"{UPSTREAM}/{urllib.request.quote(name)}"
    print(f"  下载 {name} ...")
    with urllib.request.urlopen(url, timeout=60) as r:
        dest.write_bytes(r.read())
    return dest


def clean_def(raw: str) -> str:
    """丢掉音标、合并空白、截断到 MAX_DEF 且尽量断在分号/逗号处。"""
    s = PHONETIC_RE.sub("", raw)
    s = SPACE_RE.sub(" ", s).strip()
    # 上游在中文标点后常跟一个空格（"意图， 意向"），卡片上看着像排版错误
    s = re.sub(r"([，；、。：])\s+", r"\1", s)
    s = re.sub(r"\s+([，；、。：])", r"\1", s)
    if len(s) <= MAX_DEF:
        return s
    cut = -1
    for ch in ("；", "，", ";", ",", " "):
        idx = s.rfind(ch, 0, MAX_DEF)
        if idx > cut:
            cut = idx
    if cut < MAX_DEF // 2:      # 断点太靠前（说明整句没标点）就硬截
        cut = MAX_DEF
    return s[:cut].rstrip("；，; ,") + "…"


def parse(path: Path) -> "OrderedDict[str, list[str]]":
    """返回 小写词 -> [候选释义...]，保留首次出现顺序。"""
    out: "OrderedDict[str, list[str]]" = OrderedDict()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        word = parts[0].strip()
        # ★ 字段数不对的行（混入音标）合并后段，靠 clean_def 里的 PHONETIC_RE 洗掉
        raw = " ".join(parts[1:])
        if not WORD_RE.match(word):
            continue
        d = clean_def(raw)
        if not d:
            continue
        out.setdefault(word.lower(), []).append(d)
    return out


def pick(cands: list[str]) -> str:
    """同词多条释义时取最长的那条 —— 上游越长的条目通常越完整（含词性 + 多义）。"""
    uniq = list(dict.fromkeys(cands))
    return max(uniq, key=len)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="", help="放 cet4.txt / cet6.txt 的目录；不传则自动下载")
    args = ap.parse_args()

    tmp = Path(args.src) if args.src else Path(tempfile.mkdtemp(prefix="wordlist_"))
    tmp.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    seen: dict[str, str] = {}       # 小写词 -> 已归属的级别（跨级去重）
    levels_meta = []
    for lvl_id, lvl_name, remote, local in SOURCES:
        src = fetch(remote, tmp / local) if not args.src else (tmp / local)
        if not src.exists():
            raise SystemExit(f"找不到源文件：{src}")
        pairs = parse(src)

        words: list[list[str]] = []
        dropped_cross = 0
        for w, cands in pairs.items():
            if w in seen:            # 四级里已经出现过 → 六级不再重复
                dropped_cross += 1
                continue
            seen[w] = lvl_id
            words.append([w, pick(cands)])

        payload = {
            "id": lvl_id,
            "name": lvl_name,
            "group_size": GROUP_SIZE,
            "words": words,
        }
        (OUT_DIR / f"{lvl_id.lower()}.json").write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        groups = (len(words) + GROUP_SIZE - 1) // GROUP_SIZE
        levels_meta.append(
            {"id": lvl_id, "name": lvl_name, "count": len(words), "groups": groups}
        )
        dup_hits = sum(len(v) - 1 for v in pairs.values())
        print(
            f"{lvl_id}: {len(words)} 词 / {groups} 组"
            f"（源条目 {sum(len(v) for v in pairs.values())}，"
            f"同词多释义 {dup_hits}，跨级重复丢弃 {dropped_cross}）"
        )

    index = {
        "version": 2,
        "group_size": GROUP_SIZE,
        "levels": levels_meta,
        "source": {
            "repo": "https://github.com/KyleBing/english-vocabulary",
            "license": "BSD-3-Clause",
            "copyright": "Copyright (c) 2022-2026, KyleBing",
        },
    }
    (OUT_DIR / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lic = Path(tempfile.mkdtemp()) / "LICENSE"
    try:
        with urllib.request.urlopen(f"{UPSTREAM}/LICENSE", timeout=30) as r:
            lic.write_bytes(r.read())
        (OUT_DIR / "LICENSE.txt").write_text(lic.read_text(encoding="utf-8"), encoding="utf-8")
    except Exception as e:  # 许可证取不到不该挡住词库生成
        print(f"  [警告] 许可证下载失败，请手动补 LICENSE.txt：{e}")

    total = sum(m["count"] for m in levels_meta)
    print(f"共 {total} 词，写入 {OUT_DIR}")


if __name__ == "__main__":
    main()
