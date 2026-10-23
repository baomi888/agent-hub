# -*- coding: utf-8 -*-
"""B3 检索词增强接口（/api/query）的 HTTP 层回归。

为什么单独一份 HTTP 测试，不并进 test_query_enhance.py
------------------------------------------------------
那一份测的是匹配器（纯函数，喂字符串进、取结果出）。
这一份测的是"从 HTTP 进来之后，出去的东西对不对"——三件事只有走完整的
FastAPI 栈才看得见：

1. `hits[].start/end` 是**在 original 上**的下标。前端拿它切片做高亮，
   一旦 service 层或 pydantic 层顺手 strip 了文本，下标就会整体偏移，
   而单元测试完全察觉不到（它不经过 HTTP）。
2. `text` 会被 `.strip()` 再处理，但返回的 `original` 必须是 strip 之后的原话。
3. 限流、超长、未登录这些**只有这一层才有**。

测试策略：词表与裁决表都换成临时文件
------------------------------------
真实词表（data/vocab/*.csv）是组员每天在改的。拿它写死断言（"应该有 101 个词条"）
的结果是第二天词表一更新测试就红，然后大家学会无视红灯。
所以这里一律用临时词表，只断言**契约**；真实词表能不能加载，单独一条冒烟测。

不联网、不依赖真实 API Key、不写真实 data/。
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.account  # noqa: E402
import core.config  # noqa: E402
import core.quota  # noqa: E402
import core.session  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from service import enhance  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 临时词表：四行覆盖四种分支（普通替换 / 长词 / 条件入表 / 反例）
VOCAB_TEXT = (
    "canonical,domain,aliases,negative_aliases\n"
    "高等数学,数学,高数,\n"
    "线性代数,数学,线代,\n"
    "文献综述,科研写作,综述,\n"
    "考研二战,考研,二战,二战考研\n"
)

# 临时裁决表：一个无条件禁用的词 + 一个有条件的词（带触发词与澄清问法）
ADJ_TEXT = (
    "词,裁决,需澄清,触发词,澄清问法,依据,风险等级,歧义类型\n"
    "系统,禁止入表,否,,,本身就是标准术语,高,同形异义\n"
    "综述,可入表·有条件,是,部分|章节|怎么写,你说的「综述」是指文献综述这一节吗？,"
    "动词义与名词义并存,中,词性歧义\n"
)


def _prep(monkeypatch, tag: str):
    """独立临时库 + 临时词表 + 临时裁决表。返回 (client 工厂, 词表路径)。"""
    tmp = tempfile.mkdtemp(prefix=f"qapi_{tag}_")
    db = os.path.join(tmp, "sessions.db")
    monkeypatch.setattr(core.session, "_db_path", lambda: db)
    core.session._init_db()
    core.account._ensure_tables()   # 登录鉴权要 users 表
    core.quota._ensure_tables()
    # 限流是进程内滑动窗口，跨用例会累积；不清零会在第 11 次请求上莫名 429
    core.quota._HITS.clear()

    vocab_path = os.path.join(tmp, "vocab.csv")
    adj_path = os.path.join(tmp, "adjudication.csv")
    with open(vocab_path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(VOCAB_TEXT)
    with open(adj_path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(ADJ_TEXT)

    monkeypatch.setattr(core.config, "ENHANCE_VOCAB_PATH", vocab_path)
    monkeypatch.setattr(core.config, "ENHANCE_ADJUDICATION_PATH", adj_path)
    enhance.reset_cache()  # 缓存 key 是 "vocab"/"adj"，换路径不一定触发 stamp 变化
    return tmp, vocab_path


def _client(username: str) -> TestClient:
    """登录态 client。不用 with：跳过 lifespan，省掉 Chroma 预热。"""
    c = TestClient(main.app)
    c.post("/api/auth/register/", json={"username": username, "password": "pw123456"})
    r = c.post("/api/auth/login/", json={"username": username, "password": "pw123456"})
    assert r.status_code == 200, (r.status_code, r.text)
    return c


def _enhance(c: TestClient, text: str, **kw) -> dict:
    r = c.post("/api/query/enhance", json={"text": text, **kw})
    assert r.status_code == 200, (r.status_code, r.text)
    return r.json()


# ==================== 1. 鉴权 ====================

def test_endpoints_require_login(monkeypatch):
    """两个端点都要登录。它们能读到用户正在打的字，不能匿名开放。"""
    _prep(monkeypatch, "auth")
    anon = TestClient(main.app)
    assert anon.post("/api/query/enhance", json={"text": "高数"}).status_code == 401
    assert anon.get("/api/query/vocab").status_code == 401


# ==================== 2. 核心契约：不改展示 ====================

def test_preview_never_touches_display_text(monkeypatch):
    """★ original 必须与用户输入逐字相同，检索文本才是被替换的那份。"""
    _prep(monkeypatch, "display")
    body = _enhance(_client("qapi_display"), "高数怎么复习")
    assert body["original"] == "高数怎么复习"
    assert body["retrieval"] == "高等数学怎么复习"
    assert body["changed"] is True


def test_hit_offsets_index_original_string(monkeypatch):
    """★ hits 的 start/end 必须能在 original 上直接切片。

    同一个词出现两次时，用 raw 去 indexOf 会两次都定位到第一个位置——
    这就是为什么接口必须下发下标而不是让前端自己找。
    """
    _prep(monkeypatch, "offset")
    original = "高数和高数，哪个难"
    body = _enhance(_client("qapi_offset"), original)
    hits = body["hits"]
    assert len(hits) == 2
    for h in hits:
        assert original[h["start"]:h["end"]] == h["raw"] == "高数"
    assert hits[0]["start"] != hits[1]["start"]
    assert [h["start"] for h in hits] == sorted(h["start"] for h in hits)


def test_leading_whitespace_is_stripped_consistently(monkeypatch):
    """端点会 strip 输入；strip 之后的下标必须与 strip 之后的 original 对齐。

    如果只 strip 检索用文本、不 strip original，前端高亮会整体右移两格。
    """
    _prep(monkeypatch, "strip")
    body = _enhance(_client("qapi_strip"), "  高数怎么复习  ")
    assert body["original"] == "高数怎么复习"
    h = body["hits"][0]
    assert body["original"][h["start"]:h["end"]] == "高数"


# ==================== 3. 反例与裁决表 ====================

def test_negative_alias_reports_skip_reason(monkeypatch):
    """「二战考研」本来就是对的说法，不该再换；但要给出没换的原因（排障用）。"""
    _prep(monkeypatch, "neg")
    body = _enhance(_client("qapi_neg"), "二战考研怎么准备")
    assert body["changed"] is False
    assert any("反例" in s["reason"] for s in body["skipped"])


def test_conditional_word_follows_collocation(monkeypatch):
    """★ 条件入表的词按「这句话」判，不是一刀切。

    「综述部分怎么写」该换成「文献综述」；「你综述一下」是动词义，不该换。
    """
    _prep(monkeypatch, "cond")
    c = _client("qapi_cond")

    good = _enhance(c, "综述部分怎么写才不像凑字数")
    assert good["changed"] is True
    assert good["retrieval"].startswith("文献综述")

    bad = _enhance(c, "你综述一下这几篇文章")
    assert bad["changed"] is False, "动词义被换成名词义了"


def test_clarify_can_be_switched_off(monkeypatch):
    """with_clarify=False 时前端只想静默换词，不要弹问题。"""
    _prep(monkeypatch, "clarify")
    c = _client("qapi_clarify")

    on = _enhance(c, "综述部分怎么写")
    assert len(on["clarify"]) == 1 and "文献综述" in on["clarify"][0]

    off = _enhance(c, "综述部分怎么写", with_clarify=False)
    assert off["clarify"] == []
    assert off["retrieval"] == on["retrieval"], "关掉追问不该影响换词结果"


# ==================== 4. 入参校验 ====================

def test_blank_text_is_rejected(monkeypatch):
    """纯空白走到业务层后必须 400，且给出能看懂的话。"""
    _prep(monkeypatch, "blank")
    c = _client("qapi_blank")
    r = c.post("/api/query/enhance", json={"text": "   "})
    assert r.status_code == 400
    assert "空" in r.json()["detail"]


def test_empty_string_is_rejected_by_schema(monkeypatch):
    """空字符串在 pydantic 层就被拦住（422），根本到不了业务层。

    这里如实锁住"两种空输入、两种状态码"的现状：422 是字段约束，
    400 是业务判断。前端要按 4xx 一类处理，别只判 400。
    """
    _prep(monkeypatch, "empty")
    c = _client("qapi_empty")
    assert c.post("/api/query/enhance", json={"text": ""}).status_code == 422
    assert c.post("/api/query/enhance", json={}).status_code == 422


def test_over_length_is_rejected(monkeypatch):
    """超长文本回 400，并在文案里给出实际长度与上限（用户知道该删多少）。"""
    _prep(monkeypatch, "long")
    monkeypatch.setattr(core.config, "ENHANCE_MAX_CHARS", 5)
    r = _client("qapi_long").post("/api/query/enhance", json={"text": "高数怎么复习"})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "太长" in detail and "5" in detail


# ==================== 5. 词表版本端点 ====================

def test_vocab_endpoint_reports_effective_file(monkeypatch):
    """GET /vocab 要能回答"现在用的是哪版词表"——换词表出问题时第一个看它。"""
    _, vocab_path = _prep(monkeypatch, "stats")
    body = _client("qapi_stats").get("/api/query/vocab").json()
    assert body["path"] == vocab_path
    assert body["terms"] == 4
    assert body["adjudication"]["words"] == 2
    assert body["adjudication"]["blocked"] == 1      # 系统
    assert body["adjudication"]["conditional"] == 1  # 综述
    assert body["adjudication"]["clarify"] == 1


def test_preview_reports_which_vocab_it_used(monkeypatch):
    """预览结果里也带词表文件与词条数：截图报 bug 时能直接看出用的哪版。"""
    _prep(monkeypatch, "vp")
    body = _enhance(_client("qapi_vp"), "高数")
    assert body["vocab"] == {"file": "vocab.csv", "terms": 4}
    assert body["warnings"] == []


# ==================== 6. 路由形态 ====================

def test_no_trailing_slash_redirect(monkeypatch):
    """带尾斜杠的写法不许 3xx。

    线上 Next 代理把 Location 写成 localhost:8000，浏览器照做就连自己那台机器，
    于是"页面能开、请求全挂"。所以这两种写法必须都直接命中。
    """
    _prep(monkeypatch, "slash")
    c = _client("qapi_slash")
    for path in ("/api/query/enhance", "/api/query/enhance/"):
        r = c.post(path, json={"text": "高数"}, follow_redirects=False)
        assert r.status_code not in (301, 302, 303, 307, 308), path
        assert r.status_code == 200, (path, r.status_code)
    for path in ("/api/query/vocab", "/api/query/vocab/"):
        r = c.get(path, follow_redirects=False)
        assert r.status_code == 200, (path, r.status_code)


# ==================== 7. 真实词表冒烟 ====================

def test_shipped_vocab_still_loads():
    """仓库里随包发布的词表必须能被解析器读（临时词表测不出格式漂移）。

    路径从**仓库根**直接算，不走 `enhance.resolve_vocab_path()`：
    那个函数依赖 config.DATA_DIR，而别的测试模块会把 DATA_DIR 改到临时目录，
    全量跑时这条就变成"读一个不存在的文件"（第一次跑全量就是这么红的）。
    不断言词条数：词表是组员每天在改的，写死数字只会让人学会无视红灯。
    """
    from core.query_enhance import load_vocab

    cands = [os.path.join(ROOT, "data", "vocab", n) for n in ("vocab_v1.csv", "temp_from_golden.csv")]
    path = next((p for p in cands if os.path.exists(p)), None)
    assert path, "data/vocab 下既没有 vocab_v1.csv 也没有 temp_from_golden.csv，B4 会静默不生效"
    vocab, warns = load_vocab(path)
    assert len(vocab) > 0, f"{os.path.basename(path)} 是空的，B4 接进聊天链路会静默不生效"
    assert not any("缺必需列" in w for w in warns)
