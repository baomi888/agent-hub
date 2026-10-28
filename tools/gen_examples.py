"""给词库里「没有例句」的词补一句，产物存 tools/word_examples_extra.json。

为什么需要这一步：
    exam-data/CETVocabulary（CC BY-NC-SA 4.0）的四六级词表自带例句，
    但只覆盖考纲 5276 个词。我们自己的词库是 6661 词（四级 4543 + 六级扩展 2118），
    命中率 65% —— 剩下 35% 的卡只能显示光秃秃的释义，同一页里一半有例句一半没有，
    用户会以为卡片坏了。

    ★ 这里的例句是模型生成的（DeepSeek），和上游那 3316 条同源同级。
      生成结果**不可复现**（同一词两次跑出来的句子不同），所以产物必须落到仓库里
      当作固定数据，而不是每次 build 时现生成 —— 否则词库会莫名其妙天天变。

用法（在仓库根目录）：
    python tools/gen_examples.py                    # 只补缺的
    python tools/gen_examples.py --limit 20         # 先跑 20 个看看质量
    python tools/gen_examples.py --redo             # 连已有缓存一起重生成

产物：
    tools/word_examples_extra.json   { 小写词: {s: 例句, z: 译文, m: 模型} }

★ 生成的每一条都要过校验（见 check），过不了的重试一次，仍不过就不写 ——
  宁可这张卡没有例句，也不能给用户看一句用不上这个词的「例句」。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wordrules import check_generated, is_sentence, mentions_word  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tools" / "word_examples_extra.json"
WORDS_DIR = ROOT / "frontend" / "public" / "data" / "words"

PROMPT = (
    "You write ONE short example sentence for an English vocabulary card used by "
    "Chinese college students preparing for CET-4/CET-6.\n"
    "Rules:\n"
    "- The sentence MUST contain the target word, or an inflected form of it "
    "(plural, past tense, -ing form).\n"
    "- One complete sentence, 8 to 20 words, ending with . ! or ?\n"
    "- Natural everyday or academic context, no proper nouns, no lists.\n"
    "- Then give a faithful Simplified Chinese translation.\n"
    "Reply with JSON only: {\"sentence\": \"...\", \"translation\": \"...\"}"
)


# ---------------------------------------------------------------- 校验

def check(word: str, sentence: str, translation: str) -> str:
    return check_generated(word, sentence, translation)


# ---------------------------------------------------------------- 调模型

_lock = threading.Lock()
_done = 0


def ask(key: str, base: str, model: str, word: str) -> tuple[str, str]:
    body = json.dumps(
        {
            "model": model,
            "temperature": 1.0,
            "max_tokens": 220,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": PROMPT},
                {"role": "user", "content": f"target word: {word}"},
            ],
        }
    ).encode()
    req = urllib.request.Request(
        base + "/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
    )
    with urllib.request.urlopen(req, timeout=45) as r:
        payload = json.load(r)
    text = payload["choices"][0]["message"]["content"]
    obj = json.loads(text)
    return obj.get("sentence", ""), obj.get("translation", "")


def fetch_one(key: str, base: str, model: str, word: str) -> dict | None:
    """最多试两次；两次都不合格就放弃这个词。"""
    global _done
    last = ""
    for _ in range(2):
        try:
            s, z = ask(key, base, model, word)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
            last = f"网络/接口：{e}"
            time.sleep(1.5)
            continue
        except (KeyError, json.JSONDecodeError) as e:
            last = f"返回格式：{e}"
            continue
        s, z = s.strip(), z.strip()
        why = check(word, s, z)
        if not why:
            with _lock:
                _done += 1
                if _done % 100 == 0:
                    print(f"  ... 已生成 {_done}", flush=True)
            return {"s": s, "z": z, "m": model}
        last = why
    print(f"  [跳过] {word}：{last}", flush=True)
    return None


# ---------------------------------------------------------------- 主流程

def load_words() -> list[str]:
    out: list[str] = []
    for lvl in ("cet4", "cet6"):
        p = WORDS_DIR / f"{lvl}.json"
        if not p.exists():
            raise SystemExit(f"找不到词库 {p}，先跑 tools/build_wordlist.py")
        for row in json.loads(p.read_text(encoding="utf-8"))["words"]:
            out.append(row[0])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只补前 N 个（试质量用）")
    ap.add_argument("--redo", action="store_true", help="忽略已有缓存，全部重生成")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    env = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    key = os.environ.get("DEEPSEEK_API_KEY") or env.get("DEEPSEEK_API_KEY", "")
    if not key:
        raise SystemExit("没找到 DEEPSEEK_API_KEY（.env 或环境变量）")
    base = (os.environ.get("DEEPSEEK_BASE_URL") or env.get("DEEPSEEK_BASE_URL")
            or "https://api.deepseek.com").rstrip("/")
    model = os.environ.get("DEEPSEEK_MODEL") or env.get("DEEPSEEK_MODEL") or "deepseek-chat"

    cache: dict[str, dict] = {}
    if OUT.exists() and not args.redo:
        cache = json.loads(OUT.read_text(encoding="utf-8"))
        print(f"已有缓存 {len(cache)} 条")

    # 上游已经给过「完整例句」的词不重复生成 —— 上游的（尤其真题原句）比模型写的可靠。
    # ★ 注意两个条件都要：「完整例句」而不是「有例句」—— 上游有三分之一是从试卷里截的
    #   片段（lose faith / a bedroom carpet），那些词照样要补一句真句子；
    #   还要「真的用到了这个词」—— 上游有一条 clearing 的例句写的是 clear explanation。
    covered = set()
    up = ROOT / "tools" / "_cet_full.json"
    if up.exists():
        rows = json.loads(up.read_text(encoding="utf-8"))["四六级词汇词频排序表"]
        done = [
            r for r in rows
            if is_sentence(r.get("例句", "")) and mentions_word(r["单词"], r.get("例句", ""))
        ]
        covered = {r["单词"].lower() for r in done}
        print(f"上游已有可用例句 {len(covered)} 词（共 {len(rows)} 条，其余要补）")

    todo = [w for w in load_words() if w not in covered and w not in cache]
    todo = list(dict.fromkeys(todo))
    if args.limit:
        todo = todo[: args.limit]
    print(f"待生成 {len(todo)} 词 · 模型 {model} · 并发 {args.workers}")

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda w: (w, fetch_one(key, base, model, w)), todo))

    for w, r in results:
        if r:
            cache[w] = r

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(dict(sorted(cache.items())), ensure_ascii=False, indent=0),
        encoding="utf-8",
    )
    ok = sum(1 for _, r in results if r)
    print(
        f"完成：本次成功 {ok} / {len(todo)}，缓存共 {len(cache)} 条，"
        f"耗时 {time.time() - t0:.0f}s → {OUT.relative_to(ROOT)}"
    )
    if ok < len(todo):
        print(f"[注意] 有 {len(todo) - ok} 个词没生成成功，这些卡会只显示释义", file=sys.stderr)


if __name__ == "__main__":
    main()
