# -*- coding: utf-8 -*-
"""组员作业验收器（X1–X10）—— 收到文件先跑一遍，机器能查的别用手查。

用法：
    python ops/verify_submissions.py x1 vocab_杨镇邦.csv
    python ops/verify_submissions.py x2 queries_刘立轩.csv
    python ops/verify_submissions.py x3 golden_林宝澄.csv --vocab vocab_杨镇邦.csv
    python ops/verify_submissions.py x4 ambiguous_罗绍祺.md
    python ops/verify_submissions.py x5 eval_杨镇邦.jsonl --docs docs/备料_X5_样本文档
    python ops/verify_submissions.py x6 roadmap_林宝澄.md
    python ops/verify_submissions.py x7 ui_review_罗绍祺.md
    python ops/verify_submissions.py x8 boundary_甲.csv
    python ops/verify_submissions.py x9 en_alias_乙.csv
    python ops/verify_submissions.py x10 walkthrough_丙.md

它会替你做四类事，都只做「能确定对错」的检查：
  1. 结构：字段齐不齐、列数对不对、能不能解析
  2. 数量：条数够不够、分布满不满足
  3. 重复：精确重复 + 「只改标点」的伪重复（后者人眼最容易漏）
  4. 交叉引用：X3 的 replace_to 在不在词表里；X5 的 gold_span 在不在原文里 ← 这两条最值钱

**它不判质量**：用词准不准、口语像不像、反例有没有难度，这些只能你人工抽。
所以每个任务都会给「人工还要看一眼什么」。

退出码：0 = 无 FAIL；1 = 有 FAIL（可退回）。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import unicodedata

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_PUNCT = "，。？！、；：\"\"''（）《》,.?!;:\"'()[]<>-_/\\|%…·*#`~"


# ==================== 小工具 ====================

def norm(s: str) -> str:
    """归一化：全角转半角、去空白、去标点、去 Markdown 标记、小写。

    为什么必须去 `*` `#` `` ` `` 这类：样本文档里有 `**不考概率**` 这种加粗，
    gold_span 照抄原文时带不带 `**` 都可能，不去掉会把**真句子误判成"编造"**。
    误报比漏报更伤——会冤枉组员，也会让人不信这个工具。
    """
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = re.sub(r"[\s\u3000]", "", s)
    for ch in _PUNCT:
        s = s.replace(ch, "")
    return s.lower()


class Report:
    def __init__(self, title: str) -> None:
        self.title = title
        self.items: list[tuple[str, str]] = []

    def ok(self, m: str) -> None:
        self.items.append(("PASS", m))

    def warn(self, m: str) -> None:
        self.items.append(("WARN", m))

    def fail(self, m: str) -> None:
        self.items.append(("FAIL", m))

    def info(self, m: str) -> None:
        self.items.append(("INFO", m))

    def show(self) -> int:
        print(f"\n{'=' * 66}\n{self.title}\n{'=' * 66}")
        for lvl, msg in self.items:
            print(f"  [{lvl:4}] {msg}")
        fails = sum(1 for l, _ in self.items if l == "FAIL")
        warns = sum(1 for l, _ in self.items if l == "WARN")
        if fails:
            v = f"退回（{fails} 项硬伤）"
        elif warns:
            v = f"通过，但有 {warns} 处需你确认"
        else:
            v = "通过（格式与数量层面）"
        print(f"\n  结论：{v}")
        return 1 if fails else 0


def read_csv(path: str, rep: Report) -> tuple[list[dict], list[str]]:
    """读 CSV，顺带查 BOM / 空文件 / 列数错位。返回 (rows, header)。"""
    if not os.path.exists(path):
        rep.fail(f"文件不存在：{path}")
        return [], []
    raw = open(path, "rb").read()
    if not raw.strip():
        rep.fail("文件是空的")
        return [], []
    if raw[:3] == b"\xef\xbb\xbf":
        rep.info("检测到 UTF-8 BOM（Excel 导出的典型特征），已自动处理")
    text = raw.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(text.splitlines()))
    if not rows:
        rep.fail("解析不出任何行")
        return [], []
    header = [h.strip() for h in rows[0]]
    bad = [(i + 1, len(r)) for i, r in enumerate(rows) if len(r) != len(header)]
    if bad:
        rep.fail(
            f"有 {len(bad)} 行列数与表头不符（{len(header)} 列）：行 {bad[:5]}"
            " —— 多半是格子里写了英文逗号没加引号，表格错位了"
        )
    dicts = []
    for i, r in enumerate(rows[1:], start=2):
        if not any(x.strip() for x in r):
            continue
        d = {header[j]: (r[j] if j < len(r) else "") for j in range(len(header))}
        d["__line__"] = i
        dicts.append(d)
    return dicts, header


def check_header(rep: Report, header: list[str], want: list[str]) -> bool:
    missing = [c for c in want if c not in header]
    extra = [c for c in header if c not in want]
    if missing:
        rep.fail(f"缺字段：{missing}（要求 {want}）")
        return False
    rep.ok(f"字段齐全：{'/'.join(want)}")
    if extra:
        rep.warn(f"多了字段：{extra}（不影响，但说明和任务书不一致）")
    return True


def dup_report(rep: Report, rows: list[dict], field: str, limit: int = 6) -> int:
    """精确重复 + 归一化伪重复，各报一次。返回伪重复条数。"""
    exact: dict[str, list[int]] = {}
    fuzzy: dict[str, list[int]] = {}
    for r in rows:
        v = (r.get(field) or "").strip()
        if not v:
            continue
        exact.setdefault(v, []).append(r["__line__"])
        fuzzy.setdefault(norm(v), []).append(r["__line__"])
    ex_dups = {k: v for k, v in exact.items() if len(v) > 1}
    fz_dups = {k: v for k, v in fuzzy.items() if len(v) > 1}
    if ex_dups:
        sample = list(ex_dups.items())[:limit]
        rep.fail(f"`{field}` 有 {len(ex_dups)} 组完全重复，例："
                 + "；".join(f"「{k[:18]}」行{v}" for k, v in sample))
    else:
        rep.ok(f"`{field}` 无完全重复")
    pseudo = len(fz_dups) - len(ex_dups)
    if pseudo > 0:
        sample = [(k, v) for k, v in fz_dups.items() if k not in
                  {norm(x) for x in ex_dups}][:limit]
        rep.warn(f"另有 {pseudo} 组「只改标点/空格」的伪重复，例："
                 + "；".join(f"行{v}" for _, v in sample))
    return pseudo


def count_md_table_rows(path: str) -> int:
    """数 Markdown 表格数据行（排除表头与 |---| 分隔行）。"""
    n = 0
    for line in open(path, encoding="utf-8", errors="replace"):
        s = line.strip()
        if not s.startswith("|"):
            continue
        if re.fullmatch(r"\|[\s:|-]+\|?", s):
            continue
        n += 1
    return max(0, n - 1)  # 减去表头


# 多值单元格的分隔符：任务书写的是 `|`，但组员常顺手用顿号/分号。
# 只认这三个，**不认 `/`** —— `英语/语言` 这种本身就是带斜杠的词。
_MULTI_SEP = re.compile(r"[|｜；;]")


def split_multi(s: str) -> list[str]:
    """把 `高等数学|线性代数` 拆成两个词。

    为什么必须拆：X3 允许一条 query 替换成多个标准词，
    不拆的话 `高等数学|线性代数` 会被整串拿去词表里找，**必然找不到**，
    于是把合格的作业判成「命中率过低」——这种人命级的误报必须提前掐掉。
    """
    out = []
    for part in _MULTI_SEP.split(str(s or "")):
        p = part.strip().strip("　 ")
        if p:
            out.append(p)
    return out


def parse_md_tables(path: str) -> list[tuple[list[str], list[list[str]]]]:
    """把 Markdown 里所有表格解析成 [(表头, [行...])]。用于交叉核对表格里的数字。"""
    tables: list[tuple[list[str], list[list[str]]]] = []
    header: list[str] | None = None
    rows: list[list[str]] = []

    def flush():
        nonlocal header, rows
        if header is not None:
            tables.append((header, rows))
        header, rows = None, []

    for line in open(path, encoding="utf-8", errors="replace"):
        s = line.strip()
        if not s.startswith("|"):
            flush()
            continue
        if re.fullmatch(r"\|[\s:|-]+\|?", s):   # |---|---| 分隔行
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if header is None:
            header = cells
        else:
            rows.append(cells)
    flush()
    return tables


def read_xlsx(path: str, rep: Report) -> tuple[list[dict], list[str]]:
    """读 xlsx，自动跳过标题块与统计块，找到真正的表头行。

    判定表头：一行里**所有单元格都非空，且没有纯数字**。
    这条规则能同时排除「X4 歧义词初筛」这种大标题行（有空格）、
    「词条数/70/歧义义项数/210…」这种统计行（含纯数字）。
    """
    try:
        import openpyxl
    except ImportError:
        rep.fail("没装 openpyxl，读不了 xlsx。请 `pip install openpyxl` 或让组员另存为 CSV")
        return [], []
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    rep.info(f"工作表：{ws.title}（{ws.max_row} 行 × {ws.max_column} 列）")

    raw = [list(r) for r in ws.iter_rows(values_only=True)]
    header_idx = None
    for i, row in enumerate(raw[:20]):
        cells = ["" if c is None else str(c).strip() for c in row]
        if not cells or not any(cells):
            continue
        if any(not c for c in cells):          # 有空单元格 → 不是表头
            continue
        if any(re.fullmatch(r"-?\d+(\.\d+)?", c) for c in cells):  # 纯数字 → 统计行
            continue
        header_idx = i
        break
    if header_idx is None:
        rep.fail("前 20 行里找不到表头（要求一行全部非空且不含纯数字）")
        return [], []

    header = [str(c).strip() for c in raw[header_idx]]
    rows = []
    for i, row in enumerate(raw[header_idx + 1:], start=header_idx + 2):
        vals = ["" if c is None else str(c).strip() for c in row]
        if not any(vals):
            continue
        d = {header[j]: (vals[j] if j < len(vals) else "") for j in range(len(header))}
        d["__line__"] = i
        rows.append(d)
    return rows, header


def read_table(path: str, rep: Report) -> tuple[list[dict], list[str]]:
    """按扩展名分派：csv / xlsx / md 统一读成 [{列名: 值}]。"""
    low = path.lower()
    if low.endswith((".xlsx", ".xlsm")):
        return read_xlsx(path, rep)
    if low.endswith(".csv"):
        return read_csv(path, rep)
    if low.endswith((".md", ".markdown")):
        tables = parse_md_tables(path)
        if not tables:
            rep.fail("Markdown 里没有表格")
            return [], []
        header, body = max(tables, key=lambda t: len(t[1]))
        rows = []
        for n, vals in enumerate(body, start=1):
            d = {header[j]: (vals[j] if j < len(vals) else "") for j in range(len(header))}
            d["__line__"] = n
            rows.append(d)
        return rows, header
    rep.fail(f"不认识的扩展名：{os.path.basename(path)}（支持 csv/xlsx/md）")
    return [], []


# ==================== 任务检查 ====================

def check_x1(path: str, rep: Report, args) -> None:
    rows, header = read_csv(path, rep)
    if not rows:
        return
    if not check_header(rep, header, ["term_id", "canonical", "domain",
                                      "definition", "aliases", "negative_aliases"]):
        return
    rep.ok(f"条数 {len(rows)}（要求 ≥80）") if len(rows) >= 80 else \
        rep.fail(f"条数 {len(rows)}，不足 80")

    doms = [r["domain"].strip() for r in rows]
    allowed = {"数学", "计算机", "英语/语言", "考研公共课", "科研论文", "校园通用",
               "英语", "考研", "科研", "校园"}
    bad_dom = sorted({d for d in doms if d and d not in allowed})
    if bad_dom:
        rep.warn(f"出现任务书外的 domain：{bad_dom}（合并时得手动映射）")
    from collections import Counter
    c = Counter(doms)
    rep.info("domain 分布：" + "、".join(f"{k or '(空)'} {v}" for k, v in c.most_common()))
    thin = [k for k, v in c.items() if v < 12]
    if thin:
        rep.warn(f"这些域不足 12 个：{thin}（要求每域 ≥12）")

    no_neg = [r["__line__"] for r in rows if not (r.get("negative_aliases") or "").strip()]
    if no_neg:
        rep.fail(f"{len(no_neg)} 条没有反例（行 {no_neg[:8]}）—— 无反例的词条不合格")
    else:
        rep.ok("每条都有反例")

    no_alias = [r["__line__"] for r in rows if not (r.get("aliases") or "").strip()]
    if no_alias:
        rep.warn(f"{len(no_alias)} 条没有口语别名（行 {no_alias[:8]}）")

    dup_report(rep, rows, "canonical")
    rep.info("人工还要看：抽 10 个 canonical，看 aliases 里是否真有学生这么说的口语词")
    rep.info("人工还要看：抽 5 条 negative_aliases，确认「确实不是同义」而不是硬凑")


def check_x2(path: str, rep: Report, args) -> None:
    rows, header = read_csv(path, rep)
    if not rows:
        return
    if not check_header(rep, header, ["query", "subject", "scenario"]):
        return
    rep.ok(f"条数 {len(rows)}（要求 ≥120）") if len(rows) >= 120 else \
        rep.fail(f"条数 {len(rows)}，不足 120")
    from collections import Counter
    c = Counter(r["subject"].strip() for r in rows)
    rep.info("学科分布：" + "、".join(f"{k or '(空)'} {v}" for k, v in c.most_common()))
    if len([k for k in c if k]) < 5:
        rep.fail(f"只覆盖 {len([k for k in c if k])} 个学科，要求 ≥5")
    else:
        rep.ok(f"覆盖 {len([k for k in c if k])} 个学科")
    dup_report(rep, rows, "query")
    rep.info("人工还要看：抽 10 条读一遍，像不像学生打的字（书面腔太重退回）")


def check_x3(path: str, rep: Report, args) -> None:
    rows, header = read_csv(path, rep)
    if not rows:
        return
    if not check_header(rep, header, ["query", "replace_to", "must_not_replace", "reason"]):
        return
    rep.ok(f"条数 {len(rows)}（要求 ≥80）") if len(rows) >= 80 else \
        rep.fail(f"条数 {len(rows)}，不足 80")

    neg = [r for r in rows if (r.get("must_not_replace") or "").strip()]
    if len(neg) >= 30:
        rep.ok(f"带反例 {len(neg)} 条（要求 ≥30）")
    else:
        rep.fail(f"带反例只有 {len(neg)} 条，要求 ≥30")

    no_reason = [r["__line__"] for r in rows if len((r.get("reason") or "").strip()) < 4]
    if no_reason:
        rep.warn(f"{len(no_reason)} 条 reason 过短或为空（行 {no_reason[:8]}）")

    dup_report(rep, rows, "query")

    # ---- 反例有效性：must_not_replace 里的词必须真的出现在 query 里 ----
    # 一条 query 里根本没有这个词，这条反例就无法被任何匹配器测到，等于没写。
    dead_neg = []
    for r in rows:
        negs = split_multi(r.get("must_not_replace"))
        if not negs:
            continue
        q = norm(r.get("query"))
        gone = [n for n in negs if norm(n) not in q]
        if gone:
            dead_neg.append((r["__line__"], gone))
    if dead_neg:
        rep.warn(f"{len(dead_neg)} 条的 must_not_replace 里有词在 query 中不存在"
                 f"（反例测不到），例：" + "；".join(f"行{ln}{g}" for ln, g in dead_neg[:5]))
    else:
        rep.ok("反例词都真的出现在 query 里（可被匹配器测到）")

    # ---- 自相矛盾：既要求替换成 X，又要求不能替换 X ----
    contra = []
    for r in rows:
        tgt = {norm(t) for t in split_multi(r.get("replace_to"))}
        neg = {norm(t) for t in split_multi(r.get("must_not_replace"))}
        both = tgt & neg
        if both:
            contra.append((r["__line__"], sorted(both)))
    if contra:
        rep.fail(f"{len(contra)} 条自相矛盾（replace_to 与 must_not_replace 撞了）："
                 + "；".join(f"行{ln}{w}" for ln, w in contra[:5]))

    # ★ 交叉检查：replace_to 能不能落到词表上
    if args.vocab and os.path.exists(args.vocab):
        vrows, _ = read_csv(args.vocab, Report("tmp"))
        canon, alias = set(), set()
        for r in vrows:
            if r.get("canonical"):
                canon.add(norm(r["canonical"]))
            for a in split_multi(r.get("aliases") or ""):
                alias.add(norm(a))

        # 一个条可以给多个标准词，所以按「词」统计，不按「条」
        hit_c = hit_a = miss_n = 0
        miss_samples = []
        for r in rows:
            for t in split_multi(r.get("replace_to")):
                k = norm(t)
                if k in canon:
                    hit_c += 1
                elif k in alias:
                    hit_a += 1
                    miss_samples.append((r["__line__"], t + "（只命中别名）"))
                else:
                    miss_n += 1
                    miss_samples.append((r["__line__"], t))
        total = hit_c + hit_a + miss_n
        if total:
            rep.info(f"replace_to 命中词表 canonical：{hit_c}/{total} = {hit_c/total:.1%}"
                     f"（另 {hit_a} 个只命中别名，{miss_n} 个完全对不上）")
            if miss_n:
                rep.warn(f"{miss_n} 个 replace_to 在词表里查不到，例："
                         + "；".join(f"行{ln}「{t}」" for ln, t in miss_samples[:6]))
            if hit_c / total < 0.6:
                rep.fail("命中率低于 60% —— 金标集和词表对不上，开发没法用它做回归")
            elif hit_c / total < 0.85:
                rep.warn("命中率 60~85%，多数能对上但需要人工过一遍拼写差异")
    else:
        rep.info("未提供 --vocab，跳过「replace_to 是否在词表里」的交叉检查"
                 "（等 X1 的 vocab_v1.csv 到货再补跑一次）")

    rep.info("人工还要看：抽 10 条 must_not_replace，问自己「这个词真的必须不替换吗」")
    rep.info("人工还要看：抽到的反例如果全是「复习/题/咋」，判凑数退回")


def check_x4(path: str, rep: Report, args) -> None:
    if not os.path.exists(path):
        rep.fail(f"文件不存在：{path}")
        return
    rows, header = read_table(path, rep)
    if not rows:
        return

    # ---- 找列：不硬编码列名，只认「词」和「含义*」这两类 ----
    word_col = None
    for h in header:
        if h in ("词", "词语", "term", "术语") or ("词" in h and "数" not in h and "条" not in h):
            word_col = h
            break
    meaning_cols = [h for h in header if h.startswith("含义") or h.upper().startswith("MEAN")]
    if not word_col:
        rep.fail(f"找不到「词」这一列（现有列：{header}）")
        return
    if len(meaning_cols) < 2:
        rep.fail(f"含义列只有 {len(meaning_cols)} 个（{meaning_cols}），要求每词至少 2 种含义")
        return
    rep.ok(f"列结构可用：词 = {word_col}，含义列 = {meaning_cols}")

    rep.ok(f"词条 {len(rows)}（要求 ≥40）") if len(rows) >= 40 else \
        rep.fail(f"只有 {len(rows)} 条，要求 ≥40")

    # ---- 每词含义数 ----
    thin = []
    total_senses = 0
    min_sense = 99
    for r in rows:
        n = sum(1 for c in meaning_cols if (r.get(c) or "").strip())
        total_senses += n
        min_sense = min(min_sense, n)
        if n < 2:
            thin.append((r["__line__"], (r.get(word_col) or "")[:8], n))
    if thin:
        rep.fail(f"{len(thin)} 个词的有效含义不足 2 种，例：{thin[:6]}")
    else:
        rep.ok(f"每个词都有 ≥2 种含义（最少 {min_sense} 种，合计 {total_senses} 个义项）")

    dup_report(rep, rows, word_col)

    # ---- 序号连续性（有「序号」列才查）----
    id_col = next((h for h in header if h in ("序号", "编号", "id", "ID")), None)
    if id_col:
        ids = [(r.get(id_col) or "").strip() for r in rows]
        nums = []
        for v in ids:
            m = re.search(r"(\d+)\s*$", v)
            nums.append(int(m.group(1)) if m else None)
        bad_seq = [i for i, n in enumerate(nums) if n != i + 1]
        if bad_seq:
            rep.warn(f"序号不连续/不匹配，首个异常在第 {bad_seq[0] + 1} 行：{ids[bad_seq[0]]}")
        else:
            rep.ok(f"{id_col} 连续：{ids[0]} … {ids[-1]}")

    # ---- ★ 自报统计数字 vs 实际单元格 ----
    # 组员常在表头上方写一行「词条数 70 / 歧义义项数 210 / 高风险 58」，
    # 这些数字最容易和正文对不上（多算了、删了几行没改）。机器一秒能核对完。
    if path.lower().endswith((".xlsx", ".xlsm")):
        try:
            import openpyxl
            ws = openpyxl.load_workbook(path, data_only=True).active
            claimed: dict[str, int] = {}
            for row in ws.iter_rows(min_row=1, max_row=20, values_only=True):
                cells = list(row)
                for i, c in enumerate(cells[:-1]):
                    nxt = cells[i + 1]
                    if isinstance(c, str) and isinstance(nxt, (int, float)) \
                            and re.search(r"词条数|义项数|高风险|高频|最低含义", c):
                        claimed[c.strip()] = int(nxt)
            actual = {
                "词条数": len(rows),
                "歧义义项数": total_senses,
            }
            for label, val in claimed.items():
                if label in actual:
                    if actual[label] == val:
                        rep.ok(f"自报「{label} {val}」与实际一致")
                    else:
                        rep.fail(f"自报「{label} {val}」，实际是 {actual[label]} —— 表头数字和正文对不上")
                elif "最低含义" in label:
                    # 这一项是**口径**（「我们保证每词至少 N 种含义」），不是实测计数。
                    # 所以比的是「实际最小值有没有达到这个口径」，不能直接等号比。
                    if min_sense >= val:
                        rep.ok(f"自报口径「每词最低含义数 {val}」已满足（实际最少 {min_sense} 种）")
                    else:
                        rep.fail(f"自报口径「每词最低含义数 {val}」，但有词只有 {min_sense} 种 —— 口径没兜住")
                elif "高风险" in label:
                    col = next((h for h in header if "风险" in h), None)
                    if col:
                        real = sum(1 for r in rows if (r.get(col) or "").strip() == "高")
                        (rep.ok if real == val else rep.fail)(
                            f"自报「{label} {val}」，实际 {real}"
                            + ("（一致）" if real == val else "（对不上）"))
                elif "高频" in label:
                    col = next((h for h in header if "频" in h), None)
                    if col:
                        # ★ 必须精确等于「高频」：该列还有「较高频」，
                        #   用 `in` 判断会把「较高频」也算成高频（70 条全部命中，误报）。
                        real = sum(1 for r in rows if (r.get(col) or "").strip() == "高频")
                        (rep.ok if real == val else rep.fail)(
                            f"自报「{label} {val}」，实际 {real}"
                            + ("（一致）" if real == val else "（对不上）"))
        except Exception as e:  # noqa: BLE001
            rep.info(f"读自报统计时出错（不影响其他检查）：{e}")

    # ---- 分布概览（不判对错，只看偏不偏）----
    from collections import Counter
    for h in header:
        if any(k in h for k in ("类型", "风险", "频率")):
            c = Counter((r.get(h) or "(空)").strip() for r in rows)
            rep.info(f"{h} 分布：" + "、".join(f"{k} {v}" for k, v in c.most_common()))

    # 频率/风险这类列如果出现自创取值，下游做统计时会和别的人对不上
    for h, allowed in ((next((x for x in header if "频" in x), None),
                        {"高频", "中频", "低频"}),
                       (next((x for x in header if "风险" in x), None),
                        {"高", "中", "低"})):
        if not h:
            continue
        vals = {(r.get(h) or "").strip() for r in rows} - {""}
        odd = sorted(vals - allowed)
        if odd:
            rep.warn(f"`{h}` 出现任务书外的取值：{odd}（标准是 {sorted(allowed)}，下游要能映射）")

    # ---- 空值检查 ----
    hollow = [(h, sum(1 for r in rows if not (r.get(h) or "").strip())) for h in header]
    hollow = [(h, n) for h, n in hollow if n]
    if hollow:
        rep.warn("这些列有空格子：" + "、".join(f"{h} 缺 {n}" for h, n in hollow))

    rep.info("人工还要看：抽 10 个词，确认「确实是一词多义」而非两个不同的词（那是 X8）")
    rep.info("人工还要看：抽 5 条「歧义场景举例」，确认两种义项在同一句里真的都可能出现")


def check_x5(path: str, rep: Report, args) -> None:
    if not os.path.exists(path):
        rep.fail(f"文件不存在：{path}")
        return
    docs_dir = args.docs or ""
    doc_cache: dict[str, str] = {}
    rows = []
    bad_json = []
    for i, line in enumerate(open(path, encoding="utf-8-sig", errors="replace"), start=1):
        s = line.strip()
        if not s:
            continue
        try:
            d = json.loads(s)
            if not isinstance(d, dict):
                raise ValueError("不是对象")
            rows.append((i, d))
        except Exception as e:  # noqa: BLE001
            bad_json.append((i, str(e)[:40]))
    if bad_json:
        rep.fail(f"{len(bad_json)} 行不是合法 JSON，例：行 {bad_json[:5]}"
                 "（多半是中文引号或少了逗号）")
    if not rows:
        return
    rep.ok(f"可解析 {len(rows)} 条（要求 ≥50）") if len(rows) >= 50 else \
        rep.fail(f"只有 {len(rows)} 条，要求 ≥50")

    fields = ["qid", "question", "doc", "gold_span"]
    miss = [ln for ln, d in rows if any(f not in d for f in fields)]
    if miss:
        rep.fail(f"{len(miss)} 条缺字段（行 {miss[:6]}），要求 {fields}")
    else:
        rep.ok(f"字段齐全：{'/'.join(fields)}")

    qids = [d.get("qid") for _, d in rows]
    if len(set(qids)) != len(qids):
        rep.fail("qid 有重复")
    else:
        rep.ok("qid 唯一")

    # ★ 最值钱的一条：gold_span 必须真的在原文档里
    not_found = []
    for ln, d in rows:
        doc = (d.get("doc") or "").strip()
        span = (d.get("gold_span") or "").strip()
        if not span:
            not_found.append((ln, "gold_span 为空"))
            continue
        # 去掉末尾的（第 X 节）标注再比对
        span_clean = re.sub(r"[（(]\s*第\s*[0-9一二三四五六七八九十]+\s*节\s*[)）]\s*$", "", span).strip()
        key = doc
        if docs_dir:
            cand = os.path.join(docs_dir, doc)
            if not os.path.exists(cand):
                base = os.path.basename(doc)
                cand = os.path.join(docs_dir, base)
            if not os.path.exists(cand):
                not_found.append((ln, f"文档不存在：{doc}"))
                continue
            if key not in doc_cache:
                doc_cache[key] = norm(open(cand, encoding="utf-8", errors="replace").read())
            if norm(span_clean) not in doc_cache[key]:
                not_found.append((ln, f"原文里搜不到：{span_clean[:24]}…"))
    if not docs_dir:
        rep.info("未提供 --docs，跳过「gold_span 是否真在文档里」的交叉检查（强烈建议加）")
    elif not_found:
        rep.fail(f"{len(not_found)} 条 gold_span 在原文里找不到（AI 编造或抄错），例：")
        for ln, why in not_found[:6]:
            rep.info(f"      行 {ln}：{why}")
        rep.info("      → 这条必须让组员重抄。检索评估集一旦掺假，后面所有指标都不可信")
    else:
        rep.ok(f"全部 {len(rows)} 条 gold_span 都能在原文里找到 ★")

    from collections import Counter
    c = Counter((d.get("doc") or "?").strip() for _, d in rows)
    rep.info("文档分布：" + "、".join(f"{k} {v}" for k, v in c.most_common()))
    rep.info("人工还要看：抽 10 条读问题，像不像学生问的；有没有跨节拼答案的")


def check_x6(path: str, rep: Report, args) -> None:
    if not os.path.exists(path):
        rep.fail(f"文件不存在：{path}")
        return
    txt = open(path, encoding="utf-8", errors="replace").read()
    want = ([f"N{i}" for i in range(1, 8)] + [f"B{i}" for i in range(1, 9)]
            + [f"F{i}" for i in range(1, 6)] + ["P0", "P1", "P2"])
    missing = [w for w in want if not re.search(rf"\b{w}\b", txt)]
    if missing:
        rep.fail(f"缺 {len(missing)} 项任务：{missing}（要求覆盖全部 N1-N7/B1-B8/F1-F5/P0-P2）")
    else:
        rep.ok(f"覆盖全部 {len(want)} 项（N7 + B8 + F5 + P3）")
    n = count_md_table_rows(path)
    rep.info(f"表格数据行约 {n} 行（应 ≥ {len(want)}）")
    if n < len(want):
        rep.warn("表格行数少于任务数，可能有任务只写在正文里没进表")
    rep.info("人工还要看：每条「验收标准」是不是可核对的（写成「做好一点」就不合格）")


def check_x7(path: str, rep: Report, args) -> None:
    if not os.path.exists(path):
        rep.fail(f"文件不存在：{path}")
        return
    txt = open(path, encoding="utf-8", errors="replace").read()

    # 只数「测试项」表（有 编号 + 结果 两列），不要把汇总表、问题记录表也数进去。
    # 原来用「文件里所有表格行 - 1」，多表文档会算出一个虚高的条数。
    test_tables = []
    for header, body in parse_md_tables(path):
        if any("结果" in h for h in header) and any(h in ("编号", "ID", "项") for h in header):
            test_tables.append((header, body))
    if not test_tables:
        rep.fail("没找到「编号 + 结果」的测试表 —— 无法核对逐项结果")
        return

    res_col = next(h for h in test_tables[0][0] if "结果" in h)
    id_col = next(h for h in test_tables[0][0] if h in ("编号", "ID", "项"))
    seen: dict[str, str] = {}
    per_result: dict[str, int] = {}
    for header, body in test_tables:
        ri = header.index(res_col) if res_col in header else 0
        ii = header.index(id_col) if id_col in header else 0
        for cells in body:
            tid = cells[ii] if ii < len(cells) else ""
            res = (cells[ri] if ri < len(cells) else "").strip().upper()
            if not tid:
                continue
            seen[tid] = res
            per_result[res] = per_result.get(res, 0) + 1

    n = len(seen)
    rep.ok(f"逐项记录 {n} 条（要求 ≥10）") if n >= 10 else rep.fail(f"只有 {n} 条，要求 ≥10")

    # 编号重复 / 断号
    dups = [k for k, v in seen.items() if list(seen).count(k) > 1]
    nums = sorted(int(m.group(1)) for k in seen if (m := re.search(r"(\d+)", k)))
    gaps = [i for i in range(1, (max(nums) + 1 if nums else 1)) if i not in nums]
    if gaps:
        rep.warn(f"编号断号：T{gaps}（可能是删过项，但汇总数字要对得上）")
    else:
        rep.ok(f"编号连续 T{nums[0]}–T{nums[-1]}（共 {n} 项）")

    # ★ 汇总表自报数字 vs 逐项实计
    claimed = {}
    for header, body in parse_md_tables(path):
        if not any("数量" in h or "条数" in h for h in header):
            continue
        ci = next((i for i, h in enumerate(header) if "数量" in h or "条数" in h), 1)
        for cells in body:
            if not cells:
                continue
            key = cells[0].strip().upper()
            if key in ("PASS", "FAIL", "OBSERVE", "BLOCKED", "N/A"):
                v = re.sub(r"\D", "", cells[ci] if ci < len(cells) else "")
                if v:
                    claimed[key] = int(v)
    if claimed:
        mism = []
        for k, v in claimed.items():
            real = per_result.get(k, 0)
            if real != v:
                mism.append(f"{k} 自报 {v} 实际 {real}")
        if mism:
            rep.fail("汇总表和逐项明细对不上：" + "；".join(mism))
        else:
            rep.ok("汇总表数字与逐项明细一致：" +
                   "、".join(f"{k} {v}" for k, v in sorted(claimed.items())))
        s_claim = sum(claimed.values())
        if s_claim != n:
            rep.warn(f"汇总合计 {s_claim} 项，明细只有 {n} 项（差 {s_claim - n} 项没有列出来）")

    # 状态徽标与结论是否打架
    if per_result.get("PASS", 0) + per_result.get("FAIL", 0) + per_result.get("OBSERVE", 0) == 0:
        rep.warn("结果列没有 PASS/FAIL/OBSERVE 这类取值，无法核对")

    passed = sum(1 for v in seen.values() if v.startswith("PASS"))
    rep.ok(f"PASS {passed} 项") if passed >= 3 else \
        rep.warn(f"PASS 只有 {passed} 项 —— 全篇找茬的验收不算数，至少 3 条要说通过")

    rep.info("结果分布：" + "、".join(f"{k or '(空)'} {v}" for k, v in
                                      sorted(per_result.items(), key=lambda x: -x[1])))
    rep.info("人工还要看：每条「现象」是不是可复现（写「不好用」不算）")
    rep.info("人工还要看：FAIL 项是不是真的与规格冲突，还是组员把「有改进空间」记成了 FAIL")


def check_x8(path: str, rep: Report, args) -> None:
    rows, header = read_csv(path, rep)
    if not rows:
        return
    if not check_header(rep, header, ["pair_id", "term_a", "term_b", "relation",
                                      "why_confuse", "example_query", "risk"]):
        return
    rep.ok(f"对数 {len(rows)}（要求 ≥60）") if len(rows) >= 60 else \
        rep.fail(f"只有 {len(rows)} 对，要求 ≥60")
    allowed = {"上下位", "相邻学科", "同名不同物", "成对概念", "考试并列"}
    bad = sorted({(r.get("relation") or "").strip() for r in rows} - allowed)
    if bad:
        rep.fail(f"relation 出现自创取值：{bad}（只能 {sorted(allowed)}）")
    else:
        rep.ok("relation 取值全部合法")
    same = [r["__line__"] for r in rows if norm(r.get("term_a")) == norm(r.get("term_b"))]
    if same:
        rep.fail(f"{len(same)} 对 term_a 与 term_b 相同（行 {same[:6]}）—— 那是 X4 的活")
    high = sum(1 for r in rows if (r.get("risk") or "").strip() == "高")
    rep.ok(f"高风险 {high} 对（要求 ≥20）") if high >= 20 else \
        rep.warn(f"高风险只有 {high} 对，要求 ≥20")
    dup_report(rep, rows, "term_a")
    rep.info("人工还要看：抽 15 对判 relation 对不对；抽 10 条 example_query 是否口语")


def check_x9(path: str, rep: Report, args) -> None:
    rows, header = read_csv(path, rep)
    if not rows:
        return
    if not check_header(rep, header, ["term_id", "canonical", "domain", "en_alias",
                                      "en_full", "alias_type", "example_query"]):
        return
    rep.ok(f"词条 {len(rows)}（要求 ≥80）") if len(rows) >= 80 else \
        rep.fail(f"只有 {len(rows)} 条，要求 ≥80")
    non_cn = [r["__line__"] for r in rows if not re.search(r"[\u4e00-\u9fff]", r.get("canonical") or "")]
    if non_cn:
        rep.fail(f"{len(non_cn)} 条 canonical 不是中文（行 {non_cn[:6]}）—— 没法跟主词表对齐")
    allowed = {"缩写", "英文全称", "教材术语", "工具名"}
    bad = sorted({(r.get("alias_type") or "").strip() for r in rows} - allowed)
    if bad:
        rep.fail(f"alias_type 出现自创取值：{bad}")
    else:
        rep.ok("alias_type 取值全部合法")
    from collections import Counter
    c = Counter((r.get("domain") or "").strip() for r in rows)
    rep.info("domain 分布：" + "、".join(f"{k or '(空)'} {v}" for k, v in c.most_common()))
    amb = sum(1 for r in rows if (r.get("note") or "").strip())
    rep.ok(f"带备注（歧义缩写）{amb} 条（要求 ≥8）") if amb >= 8 else \
        rep.warn(f"带备注只有 {amb} 条，要求 ≥8")
    dup_report(rep, rows, "canonical")
    rep.info("人工还要看：★ 抽 20 个 en_full 拼写是否正确（AI 最容易在这里编）")
    rep.info("人工还要看：★ 抽 15 个缩写是否真实存在（编造的发现 2 个以上退回）")


def check_x10(path: str, rep: Report, args) -> None:
    if not os.path.exists(path):
        rep.fail(f"文件不存在：{path}")
        return
    txt = open(path, encoding="utf-8", errors="replace").read()
    miss = [f"T{i}" for i in range(1, 13) if not re.search(rf"\bT{i}\b", txt)]
    if miss:
        rep.fail(f"缺任务记录：{miss}（T1–T12 必做）")
    else:
        rep.ok("T1–T12 全部有记录")
    done = len(re.findall(r"完成", txt))
    rep.info(f"出现「完成」{done} 次（应有 ≥3 条顺利完成，不能全篇找茬）")
    if done < 3:
        rep.warn("「完成」少于 3 次 —— 可能只记了问题，要求至少 3 条通过项")
    if "总体感受" not in txt:
        rep.warn("没找到「总体感受」这一节（要求 5 行以内）")
    rep.info("人工还要看：每条「实际发生了什么」是否具体，不是「感觉有点乱」")


HANDLERS = {
    "x1": (check_x1, "X1 检索词表"),
    "x2": (check_x2, "X2 真实问法"),
    "x3": (check_x3, "X3 金标集"),
    "x4": (check_x4, "X4 歧义词"),
    "x5": (check_x5, "X5 RAG 评估集"),
    "x6": (check_x6, "X6 可执行化清单"),
    "x7": (check_x7, "X7 UI 验收"),
    "x8": (check_x8, "X8 术语边界卡"),
    "x9": (check_x9, "X9 英文别名"),
    "x10": (check_x10, "X10 线上走查"),
}


def main() -> int:
    ap = argparse.ArgumentParser(description="组员作业验收器（X1–X10）")
    ap.add_argument("task", choices=sorted(HANDLERS), help="任务编号，如 x3")
    ap.add_argument("path", help="组员交的文件路径")
    ap.add_argument("--vocab", help="X3 用：对照的词表 CSV，检查 replace_to 是否在词表里")
    ap.add_argument("--docs", help="X5 用：样本文档目录，检查 gold_span 是否真在原文里")
    args = ap.parse_args()

    fn, label = HANDLERS[args.task]
    rep = Report(f"{label.upper()} · {os.path.basename(args.path)}")
    fn(args.path, rep, args)
    return rep.show()


if __name__ == "__main__":
    sys.exit(main())
