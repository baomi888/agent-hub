"""板块 D「知识库沉淀与按主题建库」的回归测试。

覆盖 10-30 接口文档 §15 的验收口径（去掉需要真 embedding 的部分）：

  1. 偏好接口：默认 true / PUT 生效 / 非 bool 422 / 未登录 401
  2. 压缩触发：commit 成功后 job_runs 出现 pending（`enqueue` 幂等）
  3. auto_sediment=false：不建库、不产生 batch
  4. 按主题复用：同名会话复用同一个 kb_id
  5. staged 回写（在 test_compress_v2 里已覆盖真实路径，这里只验调用发生）
  6. 幂等：同 content_hash 不重复入库；同 boundary 不重复入队
  7. proposal 三态：confirm / dismiss / 409 / 410 / 404
  8. confirm 后库里真的有东西、会话被绑上

**全部离线**：向量库用 FakePipe 替身，不调 embedding、不联网。
"""

from __future__ import annotations

import hashlib
import time

import pytest
from fastapi.testclient import TestClient

from core import kb_proposals, sediment_store
from core import scheduler as sched
from core import session as store
from core.auth import create_token
from kb import pipelines
from kb.rag_core import RagPipeline
from service import sediment

UID = "u-sediment"
UID2 = "u-sediment-other"


# ==================== 夹具：一个"装了假向量库"的世界 ====================


class FakePipe:
    """假向量库：记住每一句，够真到让幂等/去重/计数走真代码路径。

    ★ `get_dialogue_texts` 不能省：`write_picked` 先去这个库查"这句话沉过没有"，
      少了它就是 AttributeError（真实实现在 kb/rag_core.py）。
    """

    def __init__(self, kb_id: str):
        self.kb_id = kb_id
        self.texts: set[str] = set()
        self.sources: list[dict] = []
        self.fail_next = False

    def add_chunks(self, docs):
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("embedding 服务 500")
        self.texts.update(d.page_content for d in docs)
        for d in docs:
            src = (d.metadata or {}).get("source") or "未知来源"
            hit = next((x for x in self.sources if x["name"] == src), None)
            if hit:
                hit["chunks"] += 1
            else:
                self.sources.append({"name": src, "chunks": 1, "added_at": None})
        return len(docs)

    def get_dialogue_texts(self, sid):
        return set(self.texts)

    def list_sources(self):
        return [dict(x) for x in self.sources]

    def count(self):
        return sum(int(x.get("chunks") or 0) for x in self.sources)


class World:
    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.pipes: dict[str, FakePipe] = {}
        self.names: dict[str, str] = {}

    def pipe(self, kb_id: str) -> FakePipe:
        if kb_id not in self.pipes:
            self.pipes[kb_id] = FakePipe(kb_id)
        return self.pipes[kb_id]

    def create_kb(self, owner: str, name: str):
        raw = f"{owner}|{name}"
        kb_id = "kb_" + hashlib.md5(raw.encode()).hexdigest()[:10]
        self.names[kb_id] = name
        self.pipe(kb_id)
        return kb_id, name

    def list_all(self, owner: str):
        return [
            {"kb_id": k, "name": v, "chunks": self.pipe(k).count(), "files": []}
            for k, v in self.names.items()
        ]


@pytest.fixture()
def world(monkeypatch, tmp_path):
    """隔离三样东西：会话库（含 D 的三张表）、调度库、知识库注册表。"""
    from core import account
    from core import config as _config
    from core import quota as _quota

    db = tmp_path / "sessions.db"
    monkeypatch.setattr(store, "_db_path", lambda: str(db))
    sediment_store._READY.clear()
    store._init_db()
    account._ensure_tables()
    _quota._ensure_tables()
    _quota._ensure_rate_table()
    sediment_store.ensure()

    sdb = tmp_path / "scheduler.db"
    monkeypatch.setattr(sched, "_db_path", lambda: str(sdb))
    sched._READY.clear()
    sched.ensure_tables()

    w = World(tmp_path)
    monkeypatch.setattr(pipelines, "get_or_create", lambda owner, kb_id: w.pipe(kb_id))
    monkeypatch.setattr(pipelines, "create_kb", w.create_kb)
    monkeypatch.setattr(pipelines, "list_all", w.list_all)
    # api/kb 的 _must_own 与 add_file_records 的归属检查都读它
    monkeypatch.setattr(pipelines, "owner_of", lambda kb_id: UID if kb_id in w.names else None)
    # 登记表落盘到 tmp：不隔离会写脏仓库里的 data/kb_files.json
    monkeypatch.setattr(pipelines, "_files_path", lambda: str(tmp_path / "kb_files.json"))
    monkeypatch.setattr(pipelines, "_KB_FILES", {})
    monkeypatch.setattr(_config, "RATE_LIMIT_SHARED", False, raising=False)
    _quota._HITS.clear()
    return w


def _seed_conversation(title: str = "六级备考") -> str:
    sid = store.create_conversation(UID, title)["id"]
    store.add_message(
        UID,
        sid,
        "user",
        "我的六级考试是 12 月 14 日，请记住每天晚上九点以后我才能学习，必须保证两小时。",
    )
    store.add_message(
        UID,
        sid,
        "assistant",
        "## 一、听力提分的关键是先解决输入\n\n"
        "精听真题要选一到两分钟的段落，逐句听写，听不出的地方反复听三遍再看原文，"
        "并且标出听不出的原因到底是生词、连读还是弱读，这样才能针对性补。\n\n"
        "## 二、阅读要按题型分配时间\n\n"
        "选词填空放到最后做，因为它性价比最低；仔细阅读每篇控制在十二分钟以内，"
        "先扫题干再回原文定位，避免通读全文浪费时间。",
    )
    return sid


# ==================== 1. 偏好接口 ====================


class TestPrefs:
    @pytest.fixture()
    def client(self, world):
        from main import app

        c = TestClient(app)
        c.headers.update({"X-Auth-Token": create_token(UID, ttl_days=1, username="tester")})
        return c

    def test_default_is_true(self, client):
        body = client.get("/api/prefs").json()
        assert body == {"auto_sediment": True, "auto_create_kb": True}

    def test_put_false_persists(self, client):
        r = client.put("/api/prefs", json={"auto_sediment": False})
        assert r.status_code == 200, r.text
        assert r.json()["auto_sediment"] is False
        # 另一个开关没被顺手改掉
        assert r.json()["auto_create_kb"] is True
        assert client.get("/api/prefs").json()["auto_sediment"] is False

    def test_put_rejects_non_bool(self, client):
        # 字符串 "false" 不能被悄悄当成关闭（否则用户以为关了他其实开着）
        r = client.put("/api/prefs", json={"auto_sediment": "false"})
        assert r.status_code == 422, r.text

    def test_requires_login(self, world):
        from main import app

        assert TestClient(app).get("/api/prefs").status_code == 401


# ==================== 2. 建议生成 ====================


class TestMaybePropose:
    def test_none_when_kb_bound(self, world):
        sid = _seed_conversation()
        got = kb_proposals.maybe_propose(
            UID, sid, use_kb=True, use_web=False, kb_bound=True
        )
        assert got is None, "已经绑了库还建议建库 = 纯骚扰"

    def test_no_kb_retrieval_proposal(self, world):
        sid = _seed_conversation()
        got = kb_proposals.maybe_propose(
            UID, sid, use_kb=True, use_web=False, kb_bound=False
        )
        assert got and got["source"] == "no_kb_retrieval"
        assert got["candidates"] > 0
        assert got["kb_hint"].startswith("对话沉淀·")
        assert len(got["reason"]) <= 40

    def test_cooldown_blocks_second(self, world):
        sid = _seed_conversation()
        first = kb_proposals.maybe_propose(UID, sid, use_kb=True, use_web=False, kb_bound=False)
        assert first
        second = kb_proposals.maybe_propose(UID, sid, use_kb=True, use_web=False, kb_bound=False)
        assert second is None, "10 分钟内同一会话同一来源只发一次"

    def test_web_search_counts_sources(self, world):
        sid = _seed_conversation()
        got = kb_proposals.maybe_propose(
            UID,
            sid,
            use_kb=False,
            use_web=True,
            kb_bound=True,
            web_sources=[
                {"title": "六级听力技巧", "snippet": "精听为主", "link": "https://a.example/1"},
                {"title": "六级阅读时间分配", "snippet": "先题后文", "link": "https://a.example/2"},
            ],
        )
        assert got and got["source"] == "web_search"
        assert "2 条" in got["reason"]

    def test_disabled_switch(self, world, monkeypatch):
        from core import config as _config

        monkeypatch.setattr(_config, "SEDIMENT_ENABLED", False, raising=False)
        sid = _seed_conversation()
        assert kb_proposals.maybe_propose(UID, sid, use_kb=True, use_web=False, kb_bound=False) is None

    def test_auto_create_kb_off_blocks_no_kb_hint(self, world):
        sediment_store.set_prefs(UID, auto_create_kb=False)
        sid = _seed_conversation()
        assert kb_proposals.maybe_propose(UID, sid, use_kb=True, use_web=False, kb_bound=False) is None


# ==================== 3. 确认 / 忽略 ====================


class TestProposalEndpoints:
    @pytest.fixture()
    def client(self, world):
        from main import app

        c = TestClient(app)
        c.headers.update({"X-Auth-Token": create_token(UID, ttl_days=1, username="tester")})
        return c

    def _make(self, sid: str) -> str:
        got = kb_proposals.maybe_propose(UID, sid, use_kb=True, use_web=False, kb_bound=False)
        assert got, "前置条件：得先有一条建议"
        return got["proposal_id"]

    def test_confirm_creates_kb_and_binds(self, client, world):
        sid = _seed_conversation()
        pid = self._make(sid)
        r = client.post(f"/api/kb/proposals/{pid}/confirm", json={"bind": True})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "confirmed"
        assert body["imported"] > 0, "确认后得真往库里写东西"
        assert body["bound"] is True
        assert body["batch_id"].startswith("sed_")
        # 会话真的绑上了：下一轮检索能用
        assert (store.get_conversation(UID, sid) or {}).get("kb_id") == body["kb_id"]
        # 库里有内容
        assert world.pipe(body["kb_id"]).count() > 0
        # 建议状态落库
        assert sediment_store.get_proposal(UID, pid)["status"] == "confirmed"

    def test_confirm_again_on_same_content_reports_duplicate(self, client, world):
        """同一会话同一批内容再确认一次：一条不写，但必须说清是「存过了」。

        幂等命中和「这轮没抓到内容」在卡上一模一样，意思完全不同：
        前者是白点了第二下，后者是这段对话本来就没东西可存。前端要能分开说，
        就必须由后端把这个事实报出来，不能让它去猜。
        """
        sid = _seed_conversation()
        pid = self._make(sid)
        first = client.post(f"/api/kb/proposals/{pid}/confirm", json={}).json()
        assert first["imported"] > 0
        assert first["duplicate"] is False

        # 再挂一条新建议（直接落库，绕开 10 分钟冷却），内容与上一批完全一样
        again = sediment_store.create_proposal(
            UID, sid, "同一批内容", "no_kb_retrieval", {}, ttl_hours=1
        )
        body = client.post(
            f"/api/kb/proposals/{again['proposal_id']}/confirm", json={}
        ).json()
        assert body["imported"] == 0
        assert body["duplicate"] is True, "幂等命中要显式报出来"

    def test_confirm_twice_409(self, client, world):
        sid = _seed_conversation()
        pid = self._make(sid)
        assert client.post(f"/api/kb/proposals/{pid}/confirm", json={}).status_code == 200
        again = client.post(f"/api/kb/proposals/{pid}/confirm", json={})
        assert again.status_code == 409, again.text

    def test_dismiss_then_confirm_409(self, client, world):
        sid = _seed_conversation()
        pid = self._make(sid)
        r = client.post(f"/api/kb/proposals/{pid}/dismiss")
        assert r.status_code == 200 and r.json()["status"] == "dismissed"
        assert client.post(f"/api/kb/proposals/{pid}/confirm", json={}).status_code == 409

    def test_expired_410(self, client, world):
        sid = _seed_conversation()
        prop = sediment_store.create_proposal(
            UID, sid, "过期话题", "no_kb_retrieval", {}, ttl_hours=-1
        )
        r = client.post(f"/api/kb/proposals/{prop['proposal_id']}/confirm", json={})
        assert r.status_code == 410, r.text
        assert sediment_store.get_proposal(UID, prop["proposal_id"])["status"] == "expired"

    def test_other_owner_404(self, world, client):
        from main import app

        sid = _seed_conversation()
        pid = self._make(sid)
        other = TestClient(app)
        other.headers.update({"X-Auth-Token": create_token(UID2, ttl_days=1, username="x")})
        assert other.get(f"/api/kb/proposals/{pid}").status_code == 404
        assert other.post(f"/api/kb/proposals/{pid}/confirm", json={}).status_code == 404

    def test_get_restores_card_after_refresh(self, client, world):
        sid = _seed_conversation()
        pid = self._make(sid)
        body = client.get(f"/api/kb/proposals/{pid}").json()
        assert body["status"] == "pending"
        assert body["proposal_id"] == pid

    def test_get_returns_full_card_not_just_status(self, client, world):
        """按 id 查必须回**整张卡**，不是只有 status。

        前端恢复卡片是拿这份数据直接重画的：少了 candidates / kb_hint，
        恢复出来的卡就是"可入库 条"、库名空白 —— 比不显示更难看。
        """
        sid = _seed_conversation()
        pid = self._make(sid)
        body = client.get(f"/api/kb/proposals/{pid}").json()
        for key in (
            "topic",
            "source",
            "candidates",
            "kb_hint",
            "reason",
            "expires_at",
            "answer_message_id",
        ):
            assert key in body, f"缺字段 {key}"
        assert body["candidates"] > 0
        assert body["kb_hint"].startswith("对话沉淀·")

    def test_pending_by_sid_finds_the_proposal(self, client, world):
        """刷新后前端手上没有 proposal_id，恢复只能按会话问。"""
        sid = _seed_conversation()
        pid = self._make(sid)
        r = client.get("/api/kb/proposals", params={"sid": sid})
        assert r.status_code == 200, r.text
        body = r.json()["proposal"]
        assert body["proposal_id"] == pid
        assert body["status"] == "pending"

    def test_pending_card_matches_stream_frame(self, client, world):
        """★ 恢复出来的卡和 SSE 那一帧必须逐字段相等。

        两边各算一套的话，用户会看到「刚问完说可入库 9 条，刷一下变 2 条」，
        而这种不一致各自单测都是绿的 —— 只能靠"两边对比"抓。
        """
        sid = _seed_conversation()
        live = kb_proposals.maybe_propose(UID, sid, use_kb=True, use_web=False, kb_bound=False)
        assert live, "前置条件：得先有一条建议"
        got = client.get("/api/kb/proposals", params={"sid": sid}).json()["proposal"]
        for key in (
            "proposal_id",
            "topic",
            "source",
            "candidates",
            "kb_hint",
            "expires_at",
            "reason",
            "answer_message_id",
        ):
            assert got[key] == live[key], f"{key}: 恢复 {got[key]!r} ≠ 实时 {live[key]!r}"

    def test_pending_by_sid_empty_is_200_not_404(self, client, world):
        """没有建议 ≠ 端点不存在。

        ★ 这条请求在**每次**打开/切换会话时都会发；回 404 的话浏览器会把
          正常状态（这个会话本来就没有待办）记成一条红色资源错误，控制台里
          看起来像故障。所以必须是 200 + null。
        """
        r = client.get("/api/kb/proposals", params={"sid": "nosuch"})
        assert r.status_code == 200, r.text
        assert r.json() == {"proposal": None}

    def test_pending_by_sid_skips_dismissed(self, client, world):
        sid = _seed_conversation()
        pid = self._make(sid)
        client.post(f"/api/kb/proposals/{pid}/dismiss")
        body = client.get("/api/kb/proposals", params={"sid": sid}).json()
        assert body["proposal"] is None, "处理过的建议不该再被恢复出来"

    def test_pending_by_sid_skips_expired(self, client, world):
        """过期的不能返回：卡上写着"可入库 N 条"，点下去回 410，比不显示更糟。"""
        sid = _seed_conversation()
        sediment_store.create_proposal(
            UID, sid, "过期话题", "no_kb_retrieval", {}, ttl_hours=-1
        )
        assert client.get("/api/kb/proposals", params={"sid": sid}).json()["proposal"] is None

    def test_pending_by_sid_isolated_between_owners(self, client, world):
        """别人的会话问不出我的建议（归属要并进 SQL，不是在 Python 里过滤）。"""
        from main import app

        sid = _seed_conversation()
        self._make(sid)
        other = TestClient(app)
        other.headers.update({"X-Auth-Token": create_token(UID2, ttl_days=1, username="x")})
        assert other.get("/api/kb/proposals", params={"sid": sid}).json()["proposal"] is None


# ==================== 4. 自动沉淀（worker 路径） ====================


class TestAutoSediment:
    def test_run_one_writes_and_registers(self, world):
        sid = _seed_conversation()
        res = sediment.run_one(sediment.JOB_KEY, f"{UID}:{sid}", 99)
        assert res["tokens_out"] > 0
        # 建了一个「对话沉淀·六级备考」库，且登记成一条资料（否则 /knowledge 看不见）
        names = [k["name"] for k in world.list_all(UID)]
        assert "对话沉淀·六级备考" in names
        kb_id = next(k["kb_id"] for k in world.list_all(UID) if k["name"] == "对话沉淀·六级备考")
        files = pipelines.get_files(UID, kb_id)
        assert any(f["name"].startswith("对话·") for f in files)

    def test_pref_off_writes_nothing(self, world):
        sediment_store.set_prefs(UID, auto_sediment=False)
        sid = _seed_conversation()
        assert sediment.run_one(sediment.JOB_KEY, f"{UID}:{sid}", 99) == {}
        assert world.list_all(UID) == [], "偏好关闭时不该建出空库"

    def test_idempotent_by_content_hash(self, world):
        sid = _seed_conversation()
        first = sediment.run_one(sediment.JOB_KEY, f"{UID}:{sid}", 99)
        assert first["tokens_out"] > 0
        second = sediment.run_one(sediment.JOB_KEY, f"{UID}:{sid}", 100)
        assert second["tokens_out"] == 0, "同一段对话内容不该进第二遍库"
        assert len(sediment_store.list_batches(UID, sid)) == 1

    def test_reuse_same_topic_kb(self, world):
        sid = _seed_conversation("同一个主题")
        a, _ = sediment.reuse_or_create_topic_kb(UID, "同一个主题")
        b, _ = sediment.reuse_or_create_topic_kb(UID, "同一个主题")
        assert a == b
        c, _ = sediment.reuse_or_create_topic_kb(UID, "另一个主题")
        assert c != a
        assert sid

    def test_enqueue_is_idempotent(self, world):
        sid = _seed_conversation()
        assert sediment.enqueue(UID, sid, 42) is True
        assert sediment.enqueue(UID, sid, 42) is False, "同一 boundary 只排一次"
        assert sediment.enqueue(UID, sid, 43) is True

    def test_batch_rollback_on_write_failure(self, world):
        """写库失败必须把幂等闸门让出来，否则重试被自己挡住。"""
        sid = _seed_conversation()
        kb_id, _ = sediment.reuse_or_create_topic_kb(UID, "六级备考")
        pipe = world.pipe(kb_id)
        pipe.fail_next = True
        with pytest.raises(RuntimeError):
            sediment.ingest_candidates(UID, sid, kb_id, source="auto")
        assert sediment_store.list_batches(UID, sid) == [], "失败批次不该留记录"
        # 让出来后重试能成功
        res = sediment.ingest_candidates(UID, sid, kb_id, source="auto")
        assert res["imported"] > 0
        assert res["batch_id"].startswith("sed_")

    def test_quota_guard_is_called(self, world, monkeypatch):
        from core import quota

        called: list[str] = []
        monkeypatch.setattr(quota, "guard", lambda uid, kind: called.append(f"{uid}:{kind}"))
        sid = _seed_conversation()
        sediment.run_one(sediment.JOB_KEY, f"{UID}:{sid}", 7)
        assert called and called[0] == f"{UID}:embed"


# ==================== 5. 压缩触发接线 ====================


class TestCompressHook:
    def test_compress_success_enqueues(self, world, monkeypatch):
        """压缩成功 → 排一条沉淀任务（不阻塞压缩本身）。"""
        from core import context_compress as cc

        sid = store.create_conversation(UID, "压缩触发")["id"]
        for i in range(10):
            store.add_message(UID, sid, "user", f"第{i}问：必须记住项目{i}的截止日期是周五")
            store.add_message(UID, sid, "assistant", f"第{i}答：好的，记下了项目{i}的安排。")
        res = cc.maybe_compress(UID, sid, force=True)
        assert res["ok"], res
        # 至少有一条 sediment.dialogue 的待办（enqueue 写的 pending 行）
        runs = sched.list_runs(limit=20)["items"]
        assert any(r.get("job_key") == sediment.JOB_KEY for r in runs), "没排到 sediment.dialogue"
        queued = [r for r in runs if r.get("job_key") == sediment.JOB_KEY]
        assert queued[0]["status"] == "pending", "只排队，不在这里执行（worker 下一 tick 跑）"

    def test_compress_survives_enqueue_failure(self, world, monkeypatch):
        """enqueue 抛异常也不能影响压缩结果。"""
        from core import context_compress as cc

        monkeypatch.setattr(
            sediment, "enqueue", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        sid = store.create_conversation(UID, "压缩容错")["id"]
        for i in range(10):
            store.add_message(UID, sid, "user", f"第{i}问：必须记住项目{i}的截止日期是周五")
            store.add_message(UID, sid, "assistant", f"第{i}答：好的，记下了项目{i}的安排。")
        res = cc.maybe_compress(UID, sid, force=True)
        assert res["ok"], "沉淀挂掉不该让压缩失败"


# ==================== 6. 时间戳自洽（防止 expires_at 算错） ====================


def test_proposal_expires_in_future(world):
    sid = _seed_conversation()
    got = kb_proposals.maybe_propose(UID, sid, use_kb=True, use_web=False, kb_bound=False)
    assert got["expires_at"] > int(time.time())


# ==================== 7. SSE 接线（chat 流里真的发得出这一帧） ====================


def _frames(body: str):
    out = []
    for raw in body.split("\n\n"):
        raw = raw.strip()
        if not raw:
            continue
        name = ""
        data = ""
        for line in raw.split("\n"):
            if line.startswith("event:"):
                name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data += line[len("data:") :].strip()
        out.append((name, data))
    return out


class _Chunk:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    async def astream(self, messages):
        for t in ("好", "的"):
            yield _Chunk(t)


class _RetrievePipe:
    """检索返回空：这个用例关心的是"发不发建议帧"，不是检索质量。"""

    def retrieve_with_score(self, query, top_k, owner=None):
        return []

    _format_refs = staticmethod(RagPipeline._format_refs)
    build_sources = staticmethod(RagPipeline.build_sources)


class TestStreamWiring:
    """`api/chat.py` 在 done 之前发一帧 kb_proposal —— 前端就是靠它长出卡片的。

    ★ 这条必须走真 HTTP + 真事件序列：单独测 `maybe_propose` 只能证明"函数会返回
      数据"，证不了"这一帧真的被发出去了、而且发在 done 之前"。前端少这一帧
      就是功能不存在，而单测全绿。
    """

    def test_kb_proposal_frame_before_done(self, world, monkeypatch):
        import json

        import api.chat as chat
        from core.auth import get_current_user
        from main import app

        sid = store.create_conversation(UID, "建库建议接线")["id"]
        store.add_message(
            UID,
            sid,
            "user",
            "我的六级考试是 12 月 14 日，请记住每天晚上九点以后才能学习，必须保证两小时。",
        )
        msgs = store.get_messages(UID, sid, limit_turns=None, include_system=False)

        # 会话存在但**没绑库** —— no_kb_retrieval 的触发条件
        monkeypatch.setattr(
            chat.store, "get_conversation", lambda uid, s: {"id": sid, "title": "建库建议接线", "kb_id": None}
        )
        monkeypatch.setattr(chat.store, "count_messages", lambda uid, s: len(msgs))
        monkeypatch.setattr(chat.store, "get_messages", lambda *a, **k: msgs)
        def _fake_add(owner, s, role, content, *a, **k):
            return 2000 if role == "user" else 2001

        monkeypatch.setattr(chat.store, "add_message", _fake_add)
        monkeypatch.setattr(chat.store, "rename_conversation", lambda *a, **k: None)
        monkeypatch.setattr(chat.pipelines, "exists", lambda owner, kid: True)
        monkeypatch.setattr(chat.pipelines, "owner_of", lambda kid: UID)
        monkeypatch.setattr(chat.pipelines, "get_or_create", lambda o, k: _RetrievePipe())
        monkeypatch.setattr(chat, "get_llm", lambda *a, **k: _FakeLLM())
        monkeypatch.setattr(chat, "_generate_title", lambda q: "固定标题")
        monkeypatch.setattr(chat, "_schedule_compress", lambda *a, **k: None)
        monkeypatch.setattr(chat.quota, "consume", lambda *a, **k: None)
        from core import config as _config

        monkeypatch.setattr(_config, "USER_TAG_HOOK_ENABLED", False)

        app.dependency_overrides[get_current_user] = lambda: UID
        try:
            with TestClient(app) as c:
                r = c.post("/api/chat/stream", json={"sid": sid, "question": "六级怎么准备", "mode": "rag"})
                assert r.status_code == 200, (r.status_code, r.text)
                frames = _frames(r.text)
        finally:
            app.dependency_overrides.clear()

        names = [n for n, _ in frames]
        assert "kb_proposal" in names, f"没发建议帧：{names}"
        # 顺序：必须在 done 之前（前端要在回答结束的同时把卡片挂上去）
        assert names.index("kb_proposal") < names.index("done")
        data = json.loads([d for n, d in frames if n == "kb_proposal"][0])
        assert data["source"] == "no_kb_retrieval"
        assert data["candidates"] > 0
        assert data["proposal_id"].startswith("prop_")
        # 落库了：刷新后还能按 id 查回来
        assert sediment_store.get_proposal(UID, data["proposal_id"])["status"] == "pending"

    def test_no_frame_when_kb_bound(self, world, monkeypatch):
        """已绑库的会话不该收到建议（骚扰），事件序列里也不该多一帧。"""
        import api.chat as chat
        from core.auth import get_current_user
        from main import app

        sid = store.create_conversation(UID, "已绑库")["id"]
        store.add_message(UID, sid, "user", "我的六级考试是 12 月 14 日，必须每天背五十个单词。")
        msgs = store.get_messages(UID, sid, limit_turns=None, include_system=False)

        monkeypatch.setattr(
            chat.store, "get_conversation", lambda uid, s: {"id": sid, "title": "已绑库", "kb_id": "kb_x"}
        )
        monkeypatch.setattr(chat.store, "count_messages", lambda uid, s: len(msgs))
        monkeypatch.setattr(chat.store, "get_messages", lambda *a, **k: msgs)
        monkeypatch.setattr(chat.store, "add_message", lambda *a, **k: 3001)
        monkeypatch.setattr(chat.store, "rename_conversation", lambda *a, **k: None)
        monkeypatch.setattr(chat.pipelines, "exists", lambda owner, kid: True)
        monkeypatch.setattr(chat.pipelines, "owner_of", lambda kid: UID)
        monkeypatch.setattr(chat.pipelines, "get_or_create", lambda o, k: _RetrievePipe())
        monkeypatch.setattr(chat, "get_llm", lambda *a, **k: _FakeLLM())
        monkeypatch.setattr(chat, "_generate_title", lambda q: "固定标题")
        monkeypatch.setattr(chat, "_schedule_compress", lambda *a, **k: None)
        monkeypatch.setattr(chat.quota, "consume", lambda *a, **k: None)
        from core import config as _config

        monkeypatch.setattr(_config, "USER_TAG_HOOK_ENABLED", False)

        app.dependency_overrides[get_current_user] = lambda: UID
        try:
            with TestClient(app) as c:
                r = c.post("/api/chat/stream", json={"sid": sid, "question": "六级怎么准备", "mode": "rag"})
                frames = _frames(r.text)
        finally:
            app.dependency_overrides.clear()

        assert "kb_proposal" not in [n for n, _ in frames]
