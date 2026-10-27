"""检索词增强（B1 词表契约 + B2 匹配器）回归。

这些测试锁的不是"能跑"，而是四条**改起来最容易破的约束**：

1. 原话永不被改 —— `display_text` 恒等于输入，`retrieval_text` 也**以原话为前缀**。
   后一条是 2026-10-24 从"替换"改"追加"之后加的，比原来强得多：
   整个检索文本里，用户写的那句话原封不动排在最前面。
2. 反例按词条挂，不是全局黑名单——实测做错会让召回从 99% 掉到 94%。
3. 长别名优先——否则"操作系统"会被拆成"操作"+"系统"。
4. 追加时不许重复塞同一个术语——「文献综述」原话已有，就不该再追加一次。

另外两条护栏：
· 金标集指标不许明显退步；
· X2 的真实问法不许被改出病句（替换时代的 12 条变形，逐条钉在这里）。

为什么第 4 条和第 2 条历史不一样
--------------------------------
替换时代要防的是"改出病句"（`文献综述`→`文献文献综述`、`查重率`→`重复率检测率`）。
那些病句现在从结构上消失了：原话没被动过，不可能再造出病句。
留下要防的只剩"同一个术语在检索词里出现两遍"，属于浪费而非错误。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from core.query_enhance import Term, Vocab, load_vocab, match  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join(ROOT, "data/golden/golden_v1.csv")
TEMP_VOCAB = os.path.join(ROOT, "data/vocab/temp_from_golden.csv")


def build(*terms) -> Vocab:
    v = Vocab()
    for t in terms:
        v.add(Term(**t))
    return v


# ==================== 1. 三原则：不改展示 ====================

def test_display_text_never_changes():
    """★ 原话一个字都不能动；检索文本只是在它后面追加术语。"""
    v = build({"canonical": "高等数学", "aliases": ["高数"]})
    r = match("高数怎么复习", v)
    assert r.original == "高数怎么复习"
    assert r.display_text == "高数怎么复习"                    # 展示：原样
    assert r.retrieval_text == "高数怎么复习 高等数学"          # 检索：原话 + 术语
    assert r.added_terms == ["高等数学"]
    assert r.hits[0].raw == "高数"                            # 高亮要能定位到原话
    assert r.hits[0].start == 0 and r.hits[0].end == 2


def test_retrieval_text_always_starts_with_original():
    """★★ 本次改动的核心不变量：**检索文本以原话开头**。

    这条一旦成立，"增强把用户的问题改坏了"这类 bug 在结构上就不可能发生。
    替换时代没有这个性质（`学分绩点`→`学分平均学分绩点`），所以只能靠一个个
    heuristic 去挡，挡不住就出病句。改动后这里变成一条可断言的性质。
    """
    v = build(
        {"canonical": "高等数学", "aliases": ["高数"]},
        {"canonical": "平均学分绩点", "aliases": ["绩点"]},
        {"canonical": "重复率检测", "aliases": ["查重"]},
        {"canonical": "文献综述", "aliases": ["综述"]},
    )
    for q in ["高数怎么复习", "学分绩点怎么算", "查重率多少算安全",
              "文献综述怎么写才有逻辑", "帮我看看这段代码", ""]:
        r = match(q, v)
        assert r.retrieval_text == q or r.retrieval_text.startswith(q + " "), \
            f"检索文本没以原话开头：{q!r} → {r.retrieval_text!r}"
        # 命中下标在原话上必须能切出 raw（前端高亮靠这个）
        for h in r.hits:
            assert q[h.start:h.end] == h.raw


def test_hit_positions_index_original_string():
    """命中下标必须能在原话上直接切片，否则前端高亮会偏移。"""
    v = build({"canonical": "线性代数", "aliases": ["线代"]})
    q = "帮我复习下高数和线代"
    r = match(q, v)
    for h in r.hits:
        assert q[h.start:h.end] == h.raw


# ==================== 2. 反例的作用域 ====================

def test_negative_is_term_scoped():
    """★★ 反例只挡它所属的词条，不牵连别的词条。

    这条是拿数据换来的：把金标里所有 must_not_replace 当全局黑名单，
    「实习」这个词会把「远程实习→线上实习」也一起挡掉，召回从 99.0% 掉到 94.1%。
    """
    v = build(
        {"canonical": "线上实习", "aliases": ["远程实习"]},
        {"canonical": "考研二战", "aliases": ["二战"], "negative_aliases": ["二战考研"]},
    )
    r1 = match("二战考研怎么准备", v)
    assert "考研二战" not in r1.canonicals, "「二战考研」已是对的，不该再替换"

    r2 = match("远程实习靠谱吗", v)
    assert r2.canonicals == ["线上实习"], "别的词条的反例不该牵连这个词条"


def test_negative_blocks_its_own_alias():
    v = build({"canonical": "考研二战", "aliases": ["二战"], "negative_aliases": ["二战考研"]})
    r = match("二战考研", v)
    assert r.hits == []
    assert any("反例" in s.reason for s in r.skipped)


def test_global_block_still_works_when_explicitly_passed():
    """调用方显式要跨词条禁用时（如 X4 判定的通用歧义词），仍然有效。"""
    v = build({"canonical": "线上实习", "aliases": ["远程实习"]})
    r = match("远程实习", v, global_block={"实习"})
    assert r.hits == []


# ==================== 3. 最长优先 + 不重复替换 ====================

def test_longest_alias_wins():
    """「操作系统」这个别名不能被更短的「系统」抢先匹配掉。

    注意别名不能等于标准词：`Term` 会把「自己替换自己」的别名剔掉（没有意义），
    所以这里别名用「操作系统」、标准词用「操作系统课程」。
    """
    v = build(
        {"canonical": "操作系统课程", "aliases": ["操作系统"]},
        {"canonical": "系统", "aliases": ["系统"]},
    )
    r = match("操作系统怎么学", v)
    assert r.canonicals == ["操作系统课程"]
    assert "系统" not in [h.raw for h in r.hits], "「系统」被吃掉说明长优先失效"


def test_each_char_matched_at_most_once():
    v = build({"canonical": "高等数学", "aliases": ["高数"]})
    r = match("高数和高数", v)
    assert len(r.hits) == 2
    # 两处命中，但术语只追加一次（去重）——检索词里塞两遍「高等数学」纯属浪费
    assert r.retrieval_text == "高数和高数 高等数学"
    assert r.canonicals == ["高等数学", "高等数学"]
    spans = [(h.start, h.end) for h in r.hits]
    assert spans == sorted(spans)
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:], strict=False)), "命中区间不能重叠"


def test_added_terms_are_deduped_in_order():
    """去重 + 保持出现顺序：同一句话任何时候生成的检索串都一样，A/B 才有意义。"""
    v = build(
        {"canonical": "高等数学", "aliases": ["高数"]},
        {"canonical": "线性代数", "aliases": ["线代"]},
    )
    r = match("高数和线代都要看，线代更难", v)
    assert r.added_terms == ["高等数学", "线性代数"]
    assert r.retrieval_text == "高数和线代都要看，线代更难 高等数学 线性代数"


# ==================== 4. 冗余防护 ====================

def test_adjacent_standard_word_is_not_appended_again():
    """★ 原话里已经说了「考研」，「二战考研」就不必再追加「考研二战」。"""
    v = build({"canonical": "考研二战", "aliases": ["二战"]})
    r = match("二战考研怎么准备", v)
    assert r.canonicals == []
    assert any("不必追加" in s.reason for s in r.skipped)
    assert r.retrieval_text == "二战考研怎么准备"


def test_query_already_contains_canonical_adds_nothing():
    """★★ X2 验收补的判据：标准词整段已在原话里出现，就别再追加一次。

    原先是替换式，`文献` + `[综述]` 换成了 `文献文献综述`。
    根因不是"叠字防护不够强"，而是**压根不该动这一处**——用户写的就是标准词。
    """
    v = build({"canonical": "文献综述", "aliases": ["综述"]})
    r = match("文献综述怎么写才有逻辑", v)
    assert r.hits == [], "原话已经是标准词，不该再追加"
    assert r.retrieval_text == "文献综述怎么写才有逻辑"
    assert any("已有标准词" in s.reason for s in r.skipped)


def test_adjacent_guard_does_not_block_normal_cases():
    """别把防护做成误伤：正常句子该追加还得追加。"""
    v = build({"canonical": "考研二战", "aliases": ["二战"]})
    assert match("二战怎么准备", v).canonicals == ["考研二战"]
    assert match("二战在家复习", v).canonicals == ["考研二战"]


# ==================== 5. 歧义词拦截 ====================

def test_ambiguous_canonical_is_not_replaced():
    v = build({"canonical": "考研二战", "aliases": ["二战"]})
    r = match("二战怎么准备", v, ambiguous={"考研二战"})
    assert r.hits == []
    assert "歧义" in r.skipped[0].reason


# ==================== 6. 英文大小写 ====================

@pytest.mark.parametrize("raw", ["OS", "os", "Os"])
def test_english_alias_is_case_insensitive(raw):
    v = build({"canonical": "操作系统", "aliases": ["OS"]})
    r = match(f"{raw}怎么学", v)
    assert r.canonicals == ["操作系统"]
    assert r.hits[0].raw == raw, "替换用标准词，但命中片段要保留原话大小写"


# ==================== 7. 边界 ====================

def test_empty_inputs_are_safe():
    v = build({"canonical": "高等数学", "aliases": ["高数"]})
    assert match("", v).hits == []
    assert match("", v).retrieval_text == ""
    assert match("高数", Vocab()).hits == []
    # 空词表时检索文本必须回落成原话
    assert match("高数", Vocab()).retrieval_text == "高数"


# ==================== 8. 词表加载契约 ====================

def _write(path, text):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(text)
    return str(path)


def test_loader_handles_bom_and_multi_separators(tmp_path):
    """Excel 导出的 BOM、组员顺手写的全角竖线/分号，都要能吃。"""
    p = _write(tmp_path / "v.csv",
               "term_id,canonical,domain,definition,aliases,negative_aliases\n"
               "T1,高等数学,数学,,高数｜高数课,高数课代表\n"
               "T2,线性代数,数学,,线代;线代课,\n")
    v, warns = load_vocab(p)
    assert len(v) == 2
    assert sorted(v.terms["高等数学"].aliases) == ["高数", "高数课"]
    assert v.terms["高等数学"].negative_aliases == ["高数课代表"]
    assert sorted(v.terms["线性代数"].aliases) == ["线代", "线代课"]
    assert not any("缺" in w for w in warns)


def test_loader_rejects_missing_column(tmp_path):
    """列名对不上要直接报错 —— 安静地少匹配一半比报错更难查。"""
    p = _write(tmp_path / "bad.csv", "idx,word,alias\n1,高等数学,高数\n")
    with pytest.raises(ValueError, match="缺必需列"):
        load_vocab(p)


def test_loader_merges_duplicate_canonical(tmp_path):
    """X1 多人分头写必然出现同名行，要合并别名而不是互相覆盖。"""
    p = _write(tmp_path / "dup.csv",
               "canonical,aliases\n高等数学,高数\n高等数学,高数课\n")
    v, _ = load_vocab(p)
    assert len(v) == 1
    assert sorted(v.terms["高等数学"].aliases) == ["高数", "高数课"]


def test_loader_warns_on_alias_conflict(tmp_path):
    """同一个别名挂到两个标准词上，要报出来（否则谁赢是随机的）。"""
    p = _write(tmp_path / "conf.csv",
               "canonical,aliases\n高等数学,高数\n高等数学课,高数\n")
    _, warns = load_vocab(p)
    assert any("多个标准词" in w for w in warns)


def test_loader_skips_row_without_aliases(tmp_path):
    p = _write(tmp_path / "noalias.csv", "canonical,aliases\n高等数学,\n")
    _, warns = load_vocab(p)
    assert any("没有别名" in w for w in warns)


# ==================== 9. 金标集冒烟（防退化）====================

@pytest.mark.skipif(not (os.path.exists(GOLDEN) and os.path.exists(TEMP_VOCAB)),
                    reason="缺金标集或临时词表")
def test_golden_regression_does_not_degrate():
    """★ 指标护栏：任何改动都不许让金标表现明显退步。

    当前基线（2026-10-23，94 条 / 101 词条）：
        逐条全对 90/94 = 95.7%　精确率 97.1%　召回率 99.0%
    调参可以，但跌破下面几条线就得说明理由。
    """
    import csv
    from collections import Counter

    from core.query_enhance import split_multi

    vocab, _ = load_vocab(TEMP_VOCAB)
    rows = list(csv.DictReader(open(GOLDEN, encoding="utf-8-sig")))

    exact = 0
    tp = fp = fn = 0
    for r in rows:
        want = split_multi(r["replace_to"])
        got = match(r["query"], vocab).canonicals
        got_c, want_c = Counter(got), Counter(want)
        hit = sum((got_c & want_c).values())
        tp += hit
        fp += sum(got_c.values()) - hit
        fn += sum(want_c.values()) - hit
        if got_c == want_c:
            exact += 1

    precision = tp / (tp + fp) if tp + fp else 0
    recall = tp / (tp + fn) if tp + fn else 0
    assert exact / len(rows) >= 0.93, f"逐条全对率掉到 {exact}/{len(rows)}"
    assert precision >= 0.95, f"精确率掉到 {precision:.1%}（开始乱换了）"
    assert recall >= 0.97, f"召回率掉到 {recall:.1%}（漏换变多了）"


# ==================== 10. 歧义裁决表 ====================

ADJ = os.path.join(ROOT, "data/vocab/adjudication.csv")


@pytest.mark.skipif(not os.path.exists(ADJ), reason="缺裁决表")
def test_adjudication_loads_all_words():
    from core.query_enhance import load_adjudication

    rows = load_adjudication(ADJ)
    assert len(rows) == 70, "X4 有 70 个词，裁决表必须一一对应"
    assert sum(1 for a in rows.values() if a.needs_clarify) == 31
    assert sum(1 for a in rows.values() if a.is_conditional) == 2
    # 三类裁决加起来必须等于总数，不能有漏判
    kinds = {a.decision for a in rows.values()}
    assert kinds == {"禁止入表", "禁止入表·需澄清", "可入表·有条件"}, kinds


@pytest.mark.skipif(not os.path.exists(ADJ), reason="缺裁决表")
def test_block_words_excludes_conditional():
    from core.query_enhance import block_words, load_adjudication

    rows = load_adjudication(ADJ)
    blocked = block_words(rows)
    assert "系统" in blocked and "栈" in blocked
    assert "综述" not in blocked and "模型" not in blocked, "有条件入表的词不该被无条件禁掉"


@pytest.mark.skipif(not os.path.exists(ADJ), reason="缺裁决表")
def test_conditional_word_depends_on_collocation():
    """★ 触发条件用「搭配短语」，不是学科标签。

    「综述部分怎么写」该换（指文献综述那一节），「你综述一下」不该换；
    「跑模型要租显卡」该换（金标第 5 条），「模型怎么选」不该换。
    """
    from core.query_enhance import blocked_for_query, load_adjudication

    rows = load_adjudication(ADJ)
    assert "综述" not in blocked_for_query(rows, "综述部分怎么写才不像凑字数")
    assert "综述" in blocked_for_query(rows, "你综述一下这几篇文章")
    assert "模型" not in blocked_for_query(rows, "跑模型要租显卡吗")
    assert "模型" in blocked_for_query(rows, "模型怎么选")


@pytest.mark.skipif(not os.path.exists(ADJ), reason="缺裁决表")
def test_clarify_only_fires_when_word_present():
    from core.query_enhance import clarify_questions, load_adjudication

    rows = load_adjudication(ADJ)
    qs = clarify_questions(rows, "数二不考概率，那我是不是能少复习一章")
    assert len(qs) == 1 and "概率" in qs[0]
    assert clarify_questions(rows, "高数怎么复习") == []


def test_missing_adjudication_degrades_gracefully(tmp_path):
    """裁决表缺失时不能把功能搞挂，只能退化成「不做歧义拦截」。"""
    from core.query_enhance import block_words, load_adjudication

    rows = load_adjudication(str(tmp_path / "nope.csv"))
    assert rows == {}
    assert block_words(rows) == set()


@pytest.mark.skipif(not os.path.exists(ADJ), reason="缺裁决表")
def test_conditional_adjudication_keeps_golden_row_working():
    """裁决表接入后，「跑模型」这条金标必须仍然能换出来（A→D 不许掉召回）。"""
    from core.query_enhance import blocked_for_query, load_adjudication

    vocab, _ = load_vocab(TEMP_VOCAB)
    rows = load_adjudication(ADJ)
    q = "跑模型要租显卡吗，实验室太慢"
    r = match(q, vocab, global_block=blocked_for_query(rows, q))
    assert r.canonicals == ["模型训练"]


# ==================== 11. X2 真实问法：不许改出病句 ====================
#
# 这一节是本次改动的"案发现场"。替换时代下面这几句都被改坏了，
# 而金标 94 条（更书面、更长）一条都没测出来 —— 说明真实短句是另一类样本。

X2 = os.path.join(ROOT, "docs/组员交付_2026-10-24/queries_刘立轩.csv")

# 验收记录里逐条点名的 3 条，各代表一类错法：
#   叠字 / 替换点吞掉原字 / 术语自带原字造成重复
BROKEN_IN_REPLACE_ERA = [
    ("文献综述怎么写才有逻辑", "文献文献综述怎么写才有逻辑"),
    ("学分绩点怎么算", "学分平均学分绩点怎么算"),
    ("查重率多少算安全", "重复率检测率多少算安全"),
]


@pytest.mark.skipif(not os.path.exists(TEMP_VOCAB), reason="缺临时词表")
def test_x2_broken_sentences_are_fixed():
    """★★ 这三句曾经被改成病句，现在原话必须原封不动。"""
    vocab, _ = load_vocab(TEMP_VOCAB)
    for q, broken in BROKEN_IN_REPLACE_ERA:
        r = match(q, vocab)
        assert broken not in r.retrieval_text, f"病句回来了：{broken!r}"
        assert r.retrieval_text.startswith(q), f"原话被改坏了：{q!r} → {r.retrieval_text!r}"


@pytest.mark.skipif(not os.path.exists(X2), reason="缺 X2 真实问法（docs/组员交付_2026-10-24 未归档）")
def test_x2_no_query_ever_gets_rewritten():
    """★★ 全量 142 条真实问法：检索文本必须以原话为前缀。

    比逐条盯"这三句对不对"更强 —— 只要这条不变量成立，
    以后词表怎么扩、加多少新词，都不可能再改坏用户的话。
    """
    import csv

    vocab, _ = load_vocab(TEMP_VOCAB)
    rows = list(csv.DictReader(open(X2, encoding="utf-8-sig")))
    assert len(rows) >= 100, f"X2 样本只有 {len(rows)} 条，护栏太薄"

    bad = []
    for r in rows:
        q = (r.get("query") or "").strip()
        if not q:
            continue
        got = match(q, vocab).retrieval_text
        if not (got == q or got.startswith(q + " ")):
            bad.append((q, got))
    assert not bad, f"{len(bad)} 条原话被改动，前 3 条：{bad[:3]}"
