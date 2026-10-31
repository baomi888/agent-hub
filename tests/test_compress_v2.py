"""压缩 v2 落地回归（报告《对话提取与记忆压缩》§6 验收口径 1-6）。

覆盖：
  1. Segment 结构化 + 旧格式兼容（§6-1）
  2. 分流规则：约束句归候选、寒暄句不归；含"记住"的句子直通（§6-3）
  3. salience 四信号：约束句 > 闲聊句；singleton 句 > 重复句（§6-4）
  4. 折叠只折已 staged 的最低 salience 段；早期约束 20 轮后仍在（§6-5）
  5. 纠正句候选 + 被纠正的前句不入（§6-6）
  6. 入库回写 staged（§4.2 staged 字段）
"""

from __future__ import annotations

import os
import tempfile

# 隔离库：必须在任何 store 使用之前设置（_db_path 每次读 env，连接按路径重建）
os.environ["AGENT_HUB_DB"] = os.path.join(
    tempfile.gettempdir(), "agent_hub_compress_v2_test.db"
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core import context_compress as cc  # noqa: E402
from core import session as store  # noqa: E402
from core.auth import create_token  # noqa: E402
from kb import dialogue_import  # noqa: E402

UID = "u-compress-v2"


# ==================== §6-1 结构化 + 旧格式兼容 ====================


class TestParseCompat:
    def test_old_format_no_meta(self):
        """老压缩块 parse 后段数不变、内容不丢、meta 补默认。"""
        old = "[COMPRESSED-1] 用户问了 nginx 反代配置\n[COMPRESSED-2] 用户问了证书续期"
        block = cc.SummaryBlock.parse(old)
        assert len(block.segments) == 2
        assert "nginx 反代配置" in block.segments[0]
        assert all(m.kind == "其它" for m in block.metas)
        assert all(m.salience == 0.5 for m in block.metas)
        assert all(m.role == "user" for m in block.metas)
        assert all(not m.staged for m in block.metas)

    def test_meta_roundtrip(self):
        """新格式：append 带 meta → render → parse(metas) 完整还原。"""
        block = cc.SummaryBlock()
        block.append("第一段", cc.SegmentMeta(kind="约束", salience=0.9, refs=[11]))
        block.append("第二段", cc.SegmentMeta(kind="其它", salience=0.2, refs=[12]))
        again = cc.SummaryBlock.parse(block.render(), block.meta_json())
        assert [m.kind for m in again.metas] == ["约束", "其它"]
        assert again.metas[0].refs == [11]
        assert again.metas[1].salience == 0.2

    def test_meta_length_mismatch_pads_default(self):
        """meta 数量对不齐时补默认，绝不因结构化改造丢内容。"""
        block = cc.SummaryBlock.parse(
            "[COMPRESSED-1] a\n[COMPRESSED-2] b", '[{"kind": "约束"}]'
        )
        assert len(block.metas) == 2
        assert block.metas[0].kind == "约束"
        assert block.metas[1].kind == "其它"

    def test_render_text_clean(self):
        """元数据不进 prompt 文本：render 只输出 [COMPRESSED-n] 文本。"""
        block = cc.SummaryBlock()
        block.append("内容", cc.SegmentMeta(kind="约束", salience=0.99))
        assert block.render() == "[COMPRESSED-1] 内容"
        assert "约束" not in block.render()


# ==================== §6-3 分流规则 ====================


class TestClassify:
    def test_constraint_sentence(self):
        kind, _ = cc.classify_text("必须每天备份数据库")
        assert kind == "约束"

    def test_smalltalk_not_candidate(self):
        assert cc.classify_text("今天天气不错啊，出去玩吧")[0] == "其它"

    def test_explicit_memorize_is_candidate(self):
        """含"记住"的句子直通入库（§6-3 验收）。"""
        cands = dialogue_import.extract_candidates(
            [
                {"id": 1, "role": "user", "content": "千万记住，我这个账号不能注销", "created_at": 1},
            ]
        )
        assert any("不能注销" in c["text"] for c in cands)

    def test_candidates_split(self):
        """约束句归"入库"，寒暄句归"抽象"（§6-3 验收）。"""
        msgs = [
            {"id": 1, "role": "user", "content": "必须把日志保留 90 天，不要删", "created_at": 1},
            {"id": 2, "role": "assistant", "content": "好呀，今天天气真不错呢", "created_at": 2},
        ]
        cands = dialogue_import.extract_candidates(msgs)
        texts = [c["text"] for c in cands]
        assert any("90 天" in t for t in texts)
        assert not any("天气" in t for t in texts)


# ==================== §6-4 salience ====================


class TestSalience:
    def test_constraint_beats_smalltalk(self):
        k1, e1 = cc.classify_text("必须在周五之前交作业")
        k2, e2 = cc.classify_text("随便聊聊最近的电影")
        s1 = cc.compute_salience(k1, e1, 0.5, 0.5)
        s2 = cc.compute_salience(k2, e2, 0.5, 0.5)
        assert s1 > s2

    def test_singleton_beats_repeated(self):
        """singleton 句 > 重复句（§6-4 验收）。"""
        ent = "https://example.com/a-very-unique-path"
        once = cc.singleton_ratio(f"配置在 {ent}", f"配置在 {ent}")
        corpus = f"配置在 {ent}\n又说了一遍 {ent}"
        repeated = cc.singleton_ratio(f"配置在 {ent}", corpus)
        assert once > repeated

    def test_outlier_range(self):
        assert cc.outlier_score("完全不同的话题", ["完全不同的话题"]) == 0.0
        assert cc.outlier_score("甲乙丙丁", ["戊己庚辛"]) == 1.0

    def test_full_pipeline_ranking(self):
        """端到端：候选列表里约束句排在闲聊性质的硬实体句前面。"""
        msgs = [
            {
                "id": 1,
                "role": "user",
                "content": "千万不要把秘钥提交到 git。另外 readme.md 已经更新。",
                "created_at": 1,
            },
        ]
        cands = dialogue_import.extract_candidates(msgs)
        assert cands, "至少应有一条候选"
        kinds = {c["kind"] for c in cands}
        assert "约束" in kinds


# ==================== §6-5 折叠 ====================


def _mk_block(rows: list[tuple[str, float, bool]]) -> cc.SummaryBlock:
    block = cc.SummaryBlock()
    for text, sal, staged in rows:
        block.append(text, cc.SegmentMeta(kind="事实", salience=sal, staged=staged))
    return block


class TestFold:
    def test_fold_lowest_salience_staged(self):
        """有 staged 段时：折 salience 最低的 staged 段，而不是最早的。"""
        block = _mk_block(
            [
                ("低价值已入库", 0.2, True),  # salience 最低且 staged → 应被折
                ("中价值未入库", 0.6, False),
                ("高价值已入库", 0.9, True),
            ]
        )
        block._fold(1)
        joined = "\n".join(block.segments)
        assert "低价值已入库" in block.segments[0]  # 被折成概述
        assert "中价值未入库" in joined  # 未入库的段不受影响
        assert "高价值已入库" in joined

    def test_no_staged_falls_back_to_v1(self):
        """全部未 staged：退回 v1 行为（折最早），且不谎报 staged。"""
        block = _mk_block([("第一段", 0.9, False), ("第二段", 0.1, False)])
        block._fold(1)
        assert block.segments[0].startswith("[早期内容已折叠]")
        assert not block.metas[0].staged

    def test_early_constraint_survives_20_turns(self):
        """§6-5 核心验收：第 1 轮说过的关键约束（未入库），20 轮后仍在压缩块里。"""
        block = cc.SummaryBlock()
        block.append(
            "用户必须使用 3000 端口，不要改",  # 第 1 轮：高 salience 约束，未入库
            cc.SegmentMeta(kind="约束", salience=0.95, refs=[1], staged=False),
        )
        for i in range(20):  # 之后 20 个低价值话题，全部已入库
            block.append(
                f"闲聊话题 {i} 的细节",
                cc.SegmentMeta(kind="其它", salience=0.15, refs=[10 + i], staged=True),
            )
        joined = "\n".join(block.segments)
        assert "3000 端口" in joined, "未入库的高价值约束不允许被折叠"
        assert any("早期内容已折叠" in s for s in block.segments), "低价值段应已被折叠"

    def test_budget_loop_uses_v2_when_staged_exists(self):
        """字符超限循环：存在 staged 段时走 v2（折 staged 低分段）。"""
        # 直接构造（绕过 append 的自动预算）：append 每次都会顺手折叠，拼不出超预算块
        block = cc.SummaryBlock(
            ["约束内容" * 400, "闲聊A" * 900, "闲聊B" * 900],
            [
                cc.SegmentMeta(kind="约束", salience=0.9, staged=False),
                cc.SegmentMeta(kind="其它", salience=0.3, staged=True),
                cc.SegmentMeta(kind="其它", salience=0.2, staged=True),
            ],
        )
        assert block.chars > cc.MAX_SUMMARY_CHARS
        block._enforce_budget()
        joined = "\n".join(block.segments)
        assert "约束内容" in joined
        assert block.chars <= cc.MAX_SUMMARY_CHARS

    def test_budget_loop_v1_without_meta(self):
        """无 meta（旧数据直接构造）：字符超限走 v1 行为。"""
        block = cc.SummaryBlock(["第一段内容填充" * 400, "第二段内容填充" * 400])
        block.metas = []
        block._enforce_budget()
        assert block.chars <= cc.MAX_SUMMARY_CHARS


# ==================== §6-6 纠正信号 ====================


class TestCorrection:
    def test_correction_is_top_candidate(self):
        msgs = [
            {"id": 1, "role": "user", "content": "我要上传的是 data.csv 文件", "created_at": 1},
            {
                "id": 2,
                "role": "user",
                "content": "不对，我说的是 data.csv.bak 文件",
                "created_at": 2,
            },
        ]
        cands = dialogue_import.extract_candidates(msgs)
        kinds = [c["kind"] for c in cands]
        assert "纠正" in kinds
        # 被纠正的相似前句不应再出现在候选里（§5.2）
        assert not any("data.csv 文件" == c["text"] for c in cands)

    def test_correction_survives_fold(self):
        """纠正句永不折叠（§6-6 验收：含纠正的轮次不折）。"""
        block = cc.SummaryBlock()
        block.append(
            "不对，我说的是 3000 不是 8080",
            cc.SegmentMeta(kind="纠正", salience=1.0, refs=[2], staged=False),
        )
        for i in range(15):
            block.append(
                f"普通话题 {i}",
                cc.SegmentMeta(kind="其它", salience=0.2, refs=[100 + i], staged=True),
            )
        assert any("3000" in s for s in block.segments)


# ==================== 开通路：入库 + staged 回写 ====================


class TestDialogueImport:
    @pytest.fixture()
    def client(self, monkeypatch):
        from api import kb as kb_api
        from core import account

        # 进程内建表只跑一次：全量跑时 account/quota 的 users、usage、rate_buckets
        # 可能已经建到了其它测试的临时库（test_daily 等在模块级永久替换 _db_path）。
        # 这里对当前隔离库显式补建（都幂等），否则 auth 一查就
        # "no such table: users"、入库计额度就 "no such table: usage"。
        from core import quota as _quota
        from main import app

        account._ensure_tables()
        _quota._ensure_tables()
        _quota._ensure_rate_table()

        # 只隔离"真正往 Chroma 写"的那一步：其余（提取/回写/配额）全部走真实现
        added: list[object] = []

        class FakePipe:
            """假管道：够 true 到让 import_dialogue 跑通，不多做。

            ★ `get_dialogue_texts` 不能省：入库前的去重要读它（真实的
              `RAGPipeline.get_dialogue_texts` 按 dialogue_sid 从向量库捞已存原句，
              见 kb/rag_core.py）。少了这个方法，端点在测试里直接 AttributeError。
              这里用内存集合模拟同一个库的"已存原句"。
            """

            def __init__(self):
                self.stored: set[str] = set()

            def add_chunks(self, docs):
                added.extend(docs)
                self.stored.update(d.page_content for d in docs)
                return len(docs)

            def get_dialogue_texts(self, sid):
                return set(self.stored)

        # 同一个库要沿用同一个实例：每次 new 一个的话 stored 永远是空的，
        # 去重就测不出来了（真实场景里库是持久的，不是每次请求新建）
        _pipe = FakePipe()
        monkeypatch.setattr(
            kb_api.pipelines, "get_or_create", lambda owner, kb_id: _pipe
        )
        token = create_token(UID, ttl_days=1, username="tester")
        # 不用 with：避免触发 lifespan 的 warmup（本组测试不需要真实向量库）
        c = TestClient(app)
        c.headers.update({"X-Auth-Token": token})
        return c, added

    def _seed_conversation(self, n_pairs: int = 12):
        sid = store.create_conversation(UID, "压缩测试会话")["id"]
        for i in range(n_pairs):
            store.add_message(
                UID, sid, "user", f"第{i}问：必须记住项目{i}的截止日期是周五" if i == 0 else f"第{i}问：随便聊聊话题{i}"
            )
            store.add_message(UID, sid, "assistant", f"第{i}答：好的，这是关于话题{i}的回答。")
        return sid

    def test_candidates_endpoint(self, client):
        c, _ = client
        sid = self._seed_conversation()
        resp = c.get(f"/api/kb/dialogue-candidates/{sid}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["conv_title"] == "压缩测试会话"
        assert any("截止日期" in x["text"] for x in body["candidates"])

    def test_candidates_wrong_owner_404(self, client):
        c, _ = client
        resp = c.get("/api/kb/dialogue-candidates/not-exist")
        assert resp.status_code == 404

    def test_import_dialogue_marks_staged(self, client, monkeypatch):
        """入库成功后：压缩段被标 staged → 之后允许被折叠（§4.2）。"""
        c, added = client
        from api import kb as kb_api

        # _must_own 走内存版归属（不碰真实 kb_owners.json）
        monkeypatch.setattr(kb_api.pipelines, "owner_of", lambda kb: UID)

        sid = self._seed_conversation(n_pairs=14)
        # 先制造一个带 meta 的压缩块（force 压缩前面轮次）
        result = cc.maybe_compress(UID, sid, force=True)
        assert result["ok"], result
        block, _upto, _prev = cc.load_summary_state(UID, sid)
        assert block.segments and block.metas

        resp = c.post(
            "/api/kb/kb-test/import-dialogue",
            json={"sid": sid, "indexes": []},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["imported"] == len(added) > 0

        # staged 已回写：段内知识消息全部入库的段应被标记
        block2, _u2, _p2 = cc.load_summary_state(UID, sid)
        assert any(m.staged for m in block2.metas), "入库后至少应有一个段被标 staged"

    def test_import_dialogue_twice_skips_second(self, client, monkeypatch):
        """同一段对话勾同样的候选再存一次：一条都不再写进去。

        ★ 为什么这条必须在 HTTP 层测，而不是只测纯函数 `drop_existing`：
        向量库给每条都生成 `uuid4()` 当 id，去重没生效时**不会报错**，
        只是库里悄悄多出几份一模一样的片段 —— 它们会重复占用检索 top-k 的
        名额，表现为"我明明存了很多，问它却只答得出那一条"。
        纯函数测试证明不了"真的没写"，只有数 `add_chunks` 收了几条才能。
        """
        c, added = client
        from api import kb as kb_api

        monkeypatch.setattr(kb_api.pipelines, "owner_of", lambda kb: UID)
        sid = self._seed_conversation(n_pairs=2)

        first = c.post("/api/kb/kb-test/import-dialogue", json={"sid": sid, "indexes": []})
        assert first.status_code == 200, first.text
        n1 = first.json()["imported"]
        assert n1 > 0

        second = c.post("/api/kb/kb-test/import-dialogue", json={"sid": sid, "indexes": []})
        assert second.status_code == 200, second.text
        body = second.json()
        assert body["imported"] == 0, body
        assert body["skipped"] == n1, body
        assert len(added) == n1, "第二次不该再往库里写任何片段"

    def test_import_dialogue_bad_kb_404(self, client):
        c, _ = client
        sid = self._seed_conversation(n_pairs=2)
        resp = c.post(
            "/api/kb/no-such-kb/import-dialogue",
            json={"sid": sid, "indexes": []},
        )
        assert resp.status_code == 404

    def test_import_dialogue_index_out_of_range(self, client, monkeypatch):
        c, _ = client
        from api import kb as kb_api

        monkeypatch.setattr(kb_api.pipelines, "owner_of", lambda kb: UID)
        sid = self._seed_conversation(n_pairs=2)
        resp = c.post(
            "/api/kb/kb-x/import-dialogue",
            json={"sid": sid, "indexes": [9999]},
        )
        assert resp.status_code == 400

    # ---------- 指着某一句存（message_ids）----------
    #
    # 这条路刻意**不走候选判据**：判据是零 LLM 正则，认不出「周会固定在
    # 每周四下午三点」这种没有编号的事实。用户已经指着那句话点了保存，
    # 再拿正则否掉他，界面上就变成「我点了保存，它说没挑出来」。

    def test_import_by_message_id_stores_that_sentence(self, client, monkeypatch):
        c, added = client
        from api import kb as kb_api

        monkeypatch.setattr(kb_api.pipelines, "owner_of", lambda kb: UID)
        sid = store.create_conversation(UID, "单句入库")["id"]
        # 这句不含编号、不是待办，正规判据根本不会把它当候选
        mid = store.add_message(UID, sid, "user", "我们实验室的周会固定在每周四下午三点，会议室是 B2-07。")
        store.add_message(UID, sid, "assistant", "好的。")

        cands = c.get(f"/api/kb/dialogue-candidates/{sid}").json()["candidates"]
        assert all("每周四" not in x["text"] for x in cands), "前提失效：这句被判据收进来了"

        resp = c.post("/api/kb/kb-test/import-dialogue", json={"sid": sid, "message_ids": [mid]})
        assert resp.status_code == 200, resp.text
        assert resp.json()["imported"] == 1, resp.text
        assert len(added) == 1
        assert added[0].page_content == "我们实验室的周会固定在每周四下午三点，会议室是 B2-07。"
        assert added[0].metadata["message_id"] == mid

    def test_import_by_message_id_rejects_assistant(self, client, monkeypatch):
        """助手的话不算资料 —— 只挂用户消息是界面的事，后端也得拦一道。"""
        c, added = client
        from api import kb as kb_api

        monkeypatch.setattr(kb_api.pipelines, "owner_of", lambda kb: UID)
        sid = store.create_conversation(UID, "单句入库")["id"]
        store.add_message(UID, sid, "user", "你好")
        aid = store.add_message(UID, sid, "assistant", "助手生成的一大段内容，不该被当成用户的资料。")

        resp = c.post("/api/kb/kb-test/import-dialogue", json={"sid": sid, "message_ids": [aid]})
        assert resp.status_code == 400, resp.text
        assert added == [], "被拒的请求不许往库里写东西"

    def test_import_by_message_id_idempotent(self, client, monkeypatch):
        c, added = client
        from api import kb as kb_api

        monkeypatch.setattr(kb_api.pipelines, "owner_of", lambda kb: UID)
        sid = store.create_conversation(UID, "单句入库")["id"]
        mid = store.add_message(UID, sid, "user", "负责人是老陈，工号 KX-3301。")

        first = c.post("/api/kb/kb-test/import-dialogue", json={"sid": sid, "message_ids": [mid]})
        assert first.json()["imported"] == 1, first.text
        second = c.post("/api/kb/kb-test/import-dialogue", json={"sid": sid, "message_ids": [mid]})
        body = second.json()
        assert body["imported"] == 0, body
        assert body["skipped"] == 1, body
        assert len(added) == 1, "同一句第二次不该再写一份进库"
