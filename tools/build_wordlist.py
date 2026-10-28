"""把公开四六级词表整理成前端可直接用的 JSON（词 + 释义 + 例句）。

两个上游，各管一件事：
  · KyleBing/english-vocabulary（BSD-3-Clause）—— 词条与中文释义，四六级各一套；
  · exam-data/CETVocabulary（CC BY-NC-SA 4.0）—— 例句 + 译文 + 来源（真题原句优先）。
    它只覆盖考纲 5276 词，剩下的由 tools/gen_examples.py 用模型补齐，
    结果存在 tools/word_examples_extra.json（不可复现，必须随仓库走）。

为什么要预处理，而不是直接把 txt 丢给前端：
  1. 原始 txt 里同一个词会重复出现 2~3 次（不同释义），直接读会得到「背过的词又冒出来」；
  2. 有 2 行字段数不对（混进了音标列），按 tab 切会把音标当释义显示；
  3. 最长释义 86 字，卡片正面塞不下，需要按标点截断；
  4. 前端只认数组（不用对象）压缩体积：有例句时 [词, 释义, 例句, 译文]，
     没有例句时退化成 [词, 释义] —— 用长度判断，比加空串更省字节。

用法（在仓库根目录）：
    python tools/build_wordlist.py                 # 自动下载源文件到临时目录
    python tools/build_wordlist.py --src <目录>    # 用本地已下载的 cet4.txt / cet6.txt
    python tools/build_wordlist.py --no-examples   # 只出词和释义（不含例句）

产物：
    frontend/public/data/words/index.json     级别清单 + 分组大小 + 两个上游的署名
    frontend/public/data/words/cet4.json      四级词库
    frontend/public/data/words/cet6.json      六级词库
    frontend/public/data/words/LICENSE.txt    上游许可证（BSD-3 要求随发行保留）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import urllib.request
from collections import OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wordrules import is_sentence, mentions_word  # noqa: E402

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

# 例句上游：直接给文件地址，不通过 API（单文件 2.5MB，一次拿完）
EXAM_DATA_URL = (
    "https://raw.githubusercontent.com/exam-data/CETVocabulary/main/cet_full_list.json"
)
EXAM_DATA_LICENSE_URL = (
    "https://raw.githubusercontent.com/exam-data/CETVocabulary/main/LICENSE"
)
EXTRA_EXAMPLES = ROOT / "tools" / "word_examples_extra.json"

# 上游译文里有一批脏数据：正文后面又用括号补了一遍同义译法（`A( A')。`），
# 还有两条干脆把「释义」当译文塞了进来（emerge / divert）。
# 卡面上例句下面跟这么一行，比没有例句更糟 —— 用户在读"翻译"，
# 读到的却是 `改变( 资金、材料等) 的用途, 转移 / 娱乐`。
# 这些是**逐条修出来的**，不是正则能盖的：括号里有时是更好的那句，
# 有时是比喻解释，只能人看一眼取哪一句。重建词库时在这里统一覆盖。
REPAIR_TRANSLATIONS = {
    "entitle": "她读了一首题为《苹果树》的诗。",
    "concern": "这与你无关。",
    "equivalent": "八公里等于八千米。",
    "emerge": "太阳从云层后面露了出来。",
    "pour": "不下则已，一雨倾盆 —— 祸不单行。",
    "worm": "早起的鸟儿有虫吃。",
    "beneath": "他们认为她嫁给了地位比自己低的人。",
    "dim": "他对她那些挖苦话有点反应不过来。",
    "heaven": "天哪！",
    "scare": "你吓了我一跳。",
    "divert": "本来可能用于城市的资金已被转用到其他项目上。",
    "deteriorate": "他的健康随着年龄增长而恶化。",
    "coincide": "真是太巧了！",
}


def fetch(name: str, dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 1000:
        return dest
    url = f"{UPSTREAM}/{urllib.request.quote(name)}"
    print(f"  下载 {name} ...")
    with urllib.request.urlopen(url, timeout=60) as r:
        dest.write_bytes(r.read())
    return dest


def download(url: str, dest: Path) -> Path:
    """下载一个**绝对地址**到 dest（已存在且够大就跳过）。

    例句上游那个 2.5MB 的 json 不进仓库（它是可以重新拉的原始数据），
    所以第一次重建时要能自己拿回来 —— 这里就是那条路。
    """
    if dest.exists() and dest.stat().st_size > 1000:
        return dest
    print(f"  下载 {url.rsplit('/', 1)[-1]} ...")
    with urllib.request.urlopen(url, timeout=120) as r:
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


def clean_sentence(raw: str) -> str:
    """例句统一成「首字母大写 + 一个空格 + 句末标点」，去掉上游偶发的多余空白。"""
    s = SPACE_RE.sub(" ", (raw or "")).strip()
    if not s:
        return ""
    s = s[0].upper() + s[1:]
    if s[-1] not in ".!?":
        s += "."
    return s


def load_examples(offline: Path) -> dict[str, list[str]]:
    """小写词 -> [例句, 译文, 来源]。

    优先级：上游真题原句 > 上游模型句 > 我们自己补的。
    理由：真题句是真语料，模型句只是「说得通」，两者都留时没有理由选后者。
    """
    out: dict[str, list[str]] = {}

    def score(src: str) -> int:
        if src.startswith("真题"):
            return 2
        if src.startswith("DeepSeek"):
            return 1
        return 0

    path = offline / "_cet_full.json"
    if not path.exists():
        path = offline / "cet_full_list.json"
    if not path.exists():
        # 本地没缓存就去拉（2.5MB，只拉一次就落盘复用）。
        # 拉不到不算致命：EXAMPLES_EXTRA 那 2322 条还在，只是这 4339 条真题/模型句会缺。
        try:
            path = download(EXAM_DATA_URL, offline / "_cet_full.json")
        except Exception as e:  # noqa: BLE001 —— 断网/超时/被墙都只想警告，不想让重建挂掉
            print(f"  [警告] 上游例句下载失败：{e}")
            path = offline / "__none__"
    if path.exists():
        rows = json.loads(path.read_text(encoding="utf-8")).get("四六级词汇词频排序表", [])
        skipped = 0
        for r in rows:
            w = (r.get("单词") or "").strip().lower()
            z = (r.get("例句翻译") or "").strip()
            src = (r.get("例句来源") or "").strip()
            if not w or not z:
                continue
            # 判据见 tools/wordrules.py：没有句末标点的多半是试卷片段，不能用；
            # 另外句子里得真的出现过这个词（上游有把 clear 当 clearing 用的）
            if not is_sentence(r.get("例句", "")) or not mentions_word(w, r.get("例句", "")):
                skipped += 1
                continue
            s = clean_sentence(r.get("例句", ""))
            cur = out.get(w)
            if cur and score(cur[2]) >= score(src):
                continue
            out[w] = [s, z, src]
        print(f"  例句（上游）{len(out)} 词，跳过片段 {skipped} 条")
    else:
        print("  [警告] 没找到上游例句文件，跳过；可先下载 cet_full_list.json")

    if EXTRA_EXAMPLES.exists():
        extra = json.loads(EXTRA_EXAMPLES.read_text(encoding="utf-8"))
        added = 0
        for w, v in extra.items():
            k = w.strip().lower()
            if not k or k in out:      # 上游有就不用我们补的
                continue
            s = clean_sentence(v.get("s", ""))
            z = (v.get("z") or "").strip()
            if not s or not z:
                continue
            out[k] = [s, z, "补充"]
            added += 1
        print(f"  例句（补充）{added} 词")

    repaired = 0
    for w, z in REPAIR_TRANSLATIONS.items():
        if w in out and out[w][1] != z:
            out[w][1] = z
            repaired += 1
    if repaired:
        print(f"  译文修正 {repaired} 条")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="", help="放 cet4.txt / cet6.txt 的目录；不传则自动下载")
    ap.add_argument("--no-examples", action="store_true", help="不出例句，只出词和释义")
    args = ap.parse_args()

    tmp = Path(args.src) if args.src else Path(tempfile.mkdtemp(prefix="wordlist_"))
    tmp.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    examples: dict[str, list[str]] = {}
    if not args.no_examples:
        print("读例句：")
        examples = load_examples(ROOT / "tools")

    seen: dict[str, str] = {}       # 小写词 -> 已归属的级别（跨级去重）
    levels_meta = []
    for lvl_id, lvl_name, remote, local in SOURCES:
        src = fetch(remote, tmp / local) if not args.src else (tmp / local)
        if not src.exists():
            raise SystemExit(f"找不到源文件：{src}")
        pairs = parse(src)

        words: list[list[str]] = []
        dropped_cross = 0
        with_ex = 0
        for w, cands in pairs.items():
            if w in seen:            # 四级里已经出现过 → 六级不再重复
                dropped_cross += 1
                continue
            seen[w] = lvl_id
            row = [w, pick(cands)]
            ex = examples.get(w)
            if ex:
                # 四元组：[词, 释义, 例句, 译文]；来源不进产物（前端不显示，省体积）
                row += [ex[0], ex[1]]
                with_ex += 1
            words.append(row)

        payload = {
            "id": lvl_id,
            "name": lvl_name,
            "group_size": GROUP_SIZE,
            "with_examples": bool(examples),
            "words": words,
        }
        (OUT_DIR / f"{lvl_id.lower()}.json").write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        groups = (len(words) + GROUP_SIZE - 1) // GROUP_SIZE
        levels_meta.append(
            {"id": lvl_id, "name": lvl_name, "count": len(words), "groups": groups,
             "with_examples": with_ex}
        )
        dup_hits = sum(len(v) - 1 for v in pairs.values())
        print(
            f"{lvl_id}: {len(words)} 词 / {groups} 组，其中带例句 {with_ex}"
            f"（源条目 {sum(len(v) for v in pairs.values())}，"
            f"同词多释义 {dup_hits}，跨级重复丢弃 {dropped_cross}）"
        )

    index = {
        "version": 3,
        "group_size": GROUP_SIZE,
        "levels": levels_meta,
        "source": {
            "repo": "https://github.com/KyleBing/english-vocabulary",
            "license": "BSD-3-Clause",
            "copyright": "Copyright (c) 2022-2026, KyleBing",
            "covers": "词条与中文释义",
        },
        "source_examples": {
            "repo": "https://github.com/exam-data/CETVocabulary",
            "license": "CC-BY-NC-SA-4.0",
            "covers": "真题例句与译文",
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

    # 例句那份是 CC BY-NC-SA，要求随发行带许可声明 —— 单独存，不和 BSD 那份混
    if examples:
        try:
            with urllib.request.urlopen(EXAM_DATA_LICENSE_URL, timeout=30) as r:
                body = r.read().decode("utf-8", "ignore")
            (OUT_DIR / "LICENSE-examples.txt").write_text(
                "例句来源：https://github.com/exam-data/CETVocabulary\n"
                "许可：CC BY-NC-SA 4.0（署名 - 非商业性使用 - 相同方式共享）\n"
                "仅例句与译文来自该仓库；词条与释义来自 KyleBing/english-vocabulary（BSD-3）。\n"
                "其中标注为「补充」的例句由本项目用 DeepSeek 生成，未使用上游数据。\n\n"
                "------------------------------------------------------------------------\n\n" + body,
                encoding="utf-8",
            )
        except Exception as e:
            print(f"  [警告] 例句许可证下载失败：{e}")

    total = sum(m["count"] for m in levels_meta)
    got = sum(m["with_examples"] for m in levels_meta)
    print(f"共 {total} 词，其中带例句 {got}，写入 {OUT_DIR}")


if __name__ == "__main__":
    main()
