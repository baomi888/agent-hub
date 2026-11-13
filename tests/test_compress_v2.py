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

# 真实形态的助手回答（取自用户线上那张截图的同一段内容）。
# 4 个 markdown 小节 + 一段不带标题的引言，用来锁住"整节进、碎片不进"。
_ASST_LISTENING = """英语六级听力提分，核心是先解决听不懂的输入问题，再练做对题的应试技巧。分几块说：

## 一、基础：把耳朵练开

- **精听真题**：选一段 1-2 分钟的真题音频，逐句听写，听不出的地方反复听 3 遍再看原文，标出听不出的原因（生词？连读？弱读？）。
- **跟读模仿**：对着原文跟读，重点模仿连读、失去爆破，比如 not at all 读成 nada-tall。

## 二、题型技巧

- **长对话 / 讲座**：先看选项，圈出关键词，预判话题和问题方向；听的时候抓转折词和因果词，答案常在这些词后面。
- **新闻听力**：重点听首句，新闻导语通常概括全文；数字、时间、地点容易设题，边听边记。
- **同义替换**：六级听力很少原词重现，选项往往是原文的改写，平时积累常见替换。

## 三、应试细节

- 利用读题时间快速扫选项，不要纠结上一题，错过就放。
- 边听边在选项旁做标记，听到什么划什么。
- 考前一个月整套限时模考，适应节奏和疲劳感。

## 四、常见误区

- 只泛听不精听，听再多也提不上去
- 背单词只记拼写不记发音，听到也反应不过来
- 追求听懂每个词，抓关键信息即可"""


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
        from core import config as _config

        # 进程内建表只跑一次：全量跑时 account/quota 的 users、usage、rate_buckets
        # 可能已经建到了其它测试的临时库（test_daily 等在模块级永久替换 _db_path）。
        # 这里对当前隔离库显式补建（都幂等），否则 auth 一查就
        # "no such table: users"、入库计额度就 "no such table: usage"。
        from core import quota as _quota
        from main import app

        account._ensure_tables()
        _quota._ensure_tables()
        _quota._ensure_rate_table()
        # 限流按 uid 分桶、窗口 60 秒：这一组用例会对同一个 uid 连着打十几次
        # import-dialogue，累计超过 RATE_PER_MINUTE 就变成 429 —— 拦的是"手速"，
        # 跟业务对不对无关。两边都要关：_HITS 是进程内窗口，RATE_LIMIT_SHARED
        # 那一层落在 SQLite 的 rate_buckets 里，清内存清不掉，跨用例照样会累加。
        _quota._HITS.clear()
        monkeypatch.setattr(_config, "RATE_LIMIT_SHARED", False, raising=False)

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
                # list_sources 的替身：真实现从 Chroma 元数据里按 source 聚合，
                # 这里按入库的 Document 累加，好让 /files 的合并逻辑跑真分支
                self.sources: list[dict] = []

            def add_chunks(self, docs):
                added.extend(docs)
                self.stored.update(d.page_content for d in docs)
                for d in docs:
                    src = (d.metadata or {}).get("source") or "未知来源"
                    hit = next((x for x in self.sources if x["name"] == src), None)
                    if hit:
                        hit["chunks"] += 1
                    else:
                        self.sources.append({"name": src, "chunks": 1, "added_at": None})
                return len(docs)

            def get_dialogue_texts(self, sid):
                return set(self.stored)

            def list_sources(self) -> list[dict]:
                return [dict(x) for x in self.sources]

            def count(self) -> int:
                return sum(int(x.get("chunks") or 0) for x in self.sources)

        # 同一个库要沿用同一个实例：每次 new 一个的话 stored 永远是空的，
        # 去重就测不出来了（真实场景里库是持久的，不是每次请求新建）
        _pipe = FakePipe()
        monkeypatch.setattr(
            kb_api.pipelines, "get_or_create", lambda owner, kb_id, **kwargs: _pipe
        )
        token = create_token(UID, ttl_days=1, username="tester")
        # 不用 with：避免触发 lifespan 的 warmup（本组测试不需要真实向量库）
        c = TestClient(app)
        c.headers.update({"X-Auth-Token": token})
        return c, added

    @pytest.fixture()
    def pipe(self, client):
        """取回 client fixture 里那个 FakePipe。

        `pipelines.get_or_create` 已被 patch 成恒定返回同一实例，
        所以再调一次拿到的就是它 —— 不必为测试改 client 的返回值（会动到既有用例）。
        """
        from api import kb as kb_api

        return kb_api.pipelines.get_or_create(UID, "kb-test")

    @pytest.fixture()
    def files_registry(self, monkeypatch, tmp_path):
        """把 kb_files.json 挪到 tmp：登记会真的落盘，不隔离就写脏仓库里的 data/。"""
        from api import kb as kb_api

        path = tmp_path / "kb_files.json"
        monkeypatch.setattr(kb_api.pipelines, "_files_path", lambda: str(path))
        monkeypatch.setattr(kb_api.pipelines, "_KB_FILES", {})
        return path

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

    def test_import_indexes_follow_preview_order(self, client, monkeypatch):
        """★ 勾「第 1 条」存进去的必须就是预览里的第 1 条。

        预览端点回的是 `rank_for_preview(extract_blocks(...))`：所有 user 候选
        排在前、assistant 结构块整体后置。入库若按 `extract_blocks` 的原序取下标，
        同一个数字指向两句话 —— 用户勾「一、基础」，库里躺的是「三、题型技巧」。
        两端单测各自都是绿的，只有"预览序 vs 入库序"对着看才抓得到。
        """
        c, added = client
        from api import kb as kb_api

        monkeypatch.setattr(kb_api.pipelines, "owner_of", lambda kb: UID)

        sid = store.create_conversation(UID, "下标错位回归")["id"]
        store.add_message(UID, sid, "user", "我今天背了三十个单词，感觉还行")
        store.add_message(UID, sid, "assistant", _ASST_LISTENING)
        store.add_message(UID, sid, "user", "我的六级考试是 12 月 14 日，请务必记住这个日期")

        preview = c.get(f"/api/kb/dialogue-candidates/{sid}").json()["candidates"]
        assert len(preview) >= 3, [x["text"][:16] for x in preview]

        # 前置断言：必须真的存在"两种序不同"，否则这条用例什么也没测
        raw = dialogue_import.extract_blocks(
            store.get_messages(UID, sid, limit_turns=None, include_system=False)
        )
        assert [x["role"] for x in raw] != [x["role"] for x in preview], (
            "前置条件变了：两种序现在一样，换一组数据再测"
        )

        target = preview[1]
        resp = c.post(
            "/api/kb/kb-test/import-dialogue", json={"sid": sid, "indexes": [1]}
        )
        assert resp.status_code == 200, resp.text
        assert len(added) == 1, f"只勾了一条却写进去 {len(added)} 条"
        assert added[0].page_content == target["text"], (
            f"勾的是《{target['text'][:20]}》，存进去的是《{added[0].page_content[:20]}》"
        )

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

    # ---------- 沉淀完必须"看得见"（2026-11-01 用户实测缺口）----------
    #
    # 用户把 AI 的整段分析存进六级资料库，收到"已存入"，回到 /knowledge 却
    # 一条新资料都没有。根因不是没写进向量库，而是资料列表读的是 kb_files.json
    # 登记表，而对话沉淀只写向量库、从不登记。

    def test_import_dialogue_shows_up_in_file_list(
        self, client, pipe, files_registry, monkeypatch
    ):
        c, added = client
        from api import kb as kb_api

        monkeypatch.setattr(kb_api.pipelines, "owner_of", lambda kb: UID)
        sid = self._seed_conversation(n_pairs=2)

        resp = c.post("/api/kb/kb-test/import-dialogue", json={"sid": sid, "indexes": []})
        assert resp.status_code == 200, resp.text
        n = resp.json()["imported"]
        assert n > 0 and len(added) == n

        body = c.get("/api/kb/kb-test/files").json()
        names = [f["name"] for f in body["files"]]
        rec = next((f for f in body["files"] if f["name"] == "对话·压缩测试会话"), None)
        assert rec is not None, f"沉淀完资料列表里没有这条：{names}"
        assert rec["origin"] == "dialogue"
        assert rec["chunks"] == n
        # 负向：登记过的 source 不能被下面的 Chroma 兜底再补一遍
        assert names.count("对话·压缩测试会话") == 1

        # 同名会话（现实里很容易建出两个「周会安排」）存进同一条资料：
        # 段数要累加，不能被同名覆盖成"只有最后一次的几条"
        sid2 = store.create_conversation(UID, "压缩测试会话")["id"]
        mid2 = store.add_message(UID, sid2, "user", "另外补充：服务器到期日是十二月三号。")
        r2 = c.post(
            "/api/kb/kb-test/import-dialogue", json={"sid": sid2, "message_ids": [mid2]}
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["imported"] == 1, r2.text
        body2 = c.get("/api/kb/kb-test/files").json()
        rec2 = next(f for f in body2["files"] if f["name"] == "对话·压缩测试会话")
        assert rec2["chunks"] == n + 1, rec2

    def test_file_list_recovers_unregistered_source(
        self, client, pipe, files_registry, monkeypatch
    ):
        """向量库里有、登记表里没有的 source 也要列出来。

        这是给**已经踩过坑的用户**兜底：他们以前存进去的对话片段没有登记记录，
        不该逼他们重新存一遍才看得见。
        """
        c, _ = client
        from api import kb as kb_api

        monkeypatch.setattr(kb_api.pipelines, "owner_of", lambda kb: UID)
        pipe.sources.append({"name": "对话·旧会话", "chunks": 3, "added_at": None})
        pipe.sources.append({"name": "C:\\tmp\\kb_up_abcd.pdf", "chunks": 2, "added_at": None})

        body = c.get("/api/kb/kb-test/files").json()
        got = {f["name"]: f for f in body["files"]}
        assert got["对话·旧会话"]["chunks"] == 3
        assert got["对话·旧会话"]["origin"] == "dialogue"
        # 不认识的 source（老库的临时绝对路径）默认按 upload，标签猜错不影响存取
        assert got["C:\\tmp\\kb_up_abcd.pdf"]["origin"] == "upload"
        assert body["chunks"] == 5

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

    def test_import_by_message_id_rejects_short_chatter(self, client, monkeypatch):
        """短而空泛的助手回答仍然存不了。

        ★ 语义已经变了：10-31 时这条测的是「助手的话一律不算资料」，
          11-01 起助手整理过的内容**是**资料库的主要来源。所以它被拒的理由
          从"角色不对"变成"没有成节的内容"—— 24 个字、没标题没列表，
          切不出小节，整条存进去又只是噪音。见下一条正向测试。
        """
        c, added = client
        from api import kb as kb_api

        monkeypatch.setattr(kb_api.pipelines, "owner_of", lambda kb: UID)
        sid = store.create_conversation(UID, "单句入库")["id"]
        store.add_message(UID, sid, "user", "你好")
        aid = store.add_message(UID, sid, "assistant", "好的，随时可以继续问我。")

        resp = c.post("/api/kb/kb-test/import-dialogue", json={"sid": sid, "message_ids": [aid]})
        assert resp.status_code == 400, resp.text
        assert added == [], "被拒的请求不许往库里写东西"

    def test_import_by_message_id_splits_assistant_answer(self, client, monkeypatch):
        """指一条助手的回答存：按小节入库，不是整条塞进去。

        ★ 为什么要拆：一段 700 字整条进向量库后，召回一条就把整段灌进上下文，
          top-k 的名额被一条占满，精度反而下降。拆成小节后每块自带标题，
          问到具体的事能精确翻到那一节。
        ★ 为什么 count 必须对上：这条路径的 `skipped = requested - len(picked)`，
          用消息条数当分母就会算出负数（一条消息切 2 段 → 2 - 2 恰好没事，
          但如果只切 1 段、用户指了 2 条消息，就会变成 2 - 1 之类）。
          所以 requested 的口径改成"单元数"，这里同时锁住 skipped 不为负。
        """
        c, added = client
        from api import kb as kb_api

        monkeypatch.setattr(kb_api.pipelines, "owner_of", lambda kb: UID)
        sid = store.create_conversation(UID, "听力提分")["id"]
        store.add_message(UID, sid, "user", "给我分析英语六级听力提分技巧")
        aid = store.add_message(UID, sid, "assistant", _ASST_LISTENING)

        resp = c.post("/api/kb/kb-test/import-dialogue", json={"sid": sid, "message_ids": [aid]})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["imported"] == 4, body
        assert body["skipped"] == 0, body
        assert len(added) == 4
        # 每节以它自己的标题开头，且都挂在这条消息的 id 上（溯源要能对回去）
        assert all(d.page_content.startswith("## ") for d in added), [d.page_content[:20] for d in added]
        assert all(d.metadata["message_id"] == aid for d in added)
        assert all(d.metadata["role"] == "assistant" for d in added)
        # 引言「分几块说：」这类碎片不该单独成条
        assert all("分几块说" not in d.page_content for d in added)

    def test_import_by_message_id_idempotent(self, client, monkeypatch):
        c, added = client
        from api import kb as kb_api

        monkeypatch.setattr(kb_api.pipelines, "owner_of", lambda kb: UID)
        sid = store.create_conversation(UID, "单句入库")["id"]
        mid = store.add_message(UID, sid, "user", "负责人是老陈，工号 KX-3301。")

        first = c.post("/api/kb/kb-test/import-dialogue", json={"sid": sid, "message_ids": [mid]})
        assert first.status_code == 200, first.text
        assert first.json()["imported"] == 1, first.text
        second = c.post("/api/kb/kb-test/import-dialogue", json={"sid": sid, "message_ids": [mid]})
        assert second.status_code == 200, second.text
        body = second.json()
        assert body["imported"] == 0, body
        assert body["skipped"] == 1, body
        assert len(added) == 1, "同一句第二次不该再写一份进库"


# ==================== 助手回答的结构分块（2026-11-01）====================


class TestAssistantBlocks:
    """锁住「整节进、碎片不进」。

    背景：10-31 把准入范围收成只收 user，理由是助手长回答占了 92% 的候选、
    抽样全是排版垃圾。方向错了一半 —— 助手整理过的内容恰恰是沉淀功能最该收的。
    实测那段 754 字的听力提分回答：只收 user 时提出 0 条；恢复双角色也只有 1 条
    （靠「不要纠结上一题」命中否定规则）。所以改成按 markdown 结构切块。
    """

    def test_splits_by_section(self):
        cs = dialogue_import.extract_blocks(
            [{"id": 5, "role": "assistant", "content": _ASST_LISTENING, "created_at": 1}]
        )
        assert len(cs) == 4, [c["text"][:20] for c in cs]
        assert [c["text"].splitlines()[0] for c in cs] == [
            "## 一、基础：把耳朵练开",
            "## 二、题型技巧",
            "## 三、应试细节",
            "## 四、常见误区",
        ]
        assert all(c["role"] == "assistant" for c in cs)
        assert all(c["refs"] == [5] for c in cs)
        # 引言碎片只有几十字、没有结构，不该单独成条
        assert all("分几块说" not in c["text"] for c in cs)

    def test_echo_and_followup_rejected(self):
        """回声 / 追问 / 寒暄都不进库 —— 它们没有内容，只会占检索名额。"""
        for txt in (
            "好的，记下了：实验室周会——每周四下午 3 点。",
            "需要我再展开讲讲吗？你说一句就行，我按你的方向细化。",
            "没问题！随时可以继续问，我在。",
        ):
            got = dialogue_import.extract_blocks(
                [{"id": 1, "role": "assistant", "content": txt, "created_at": 1}]
            )
            assert got == [], txt

    def test_code_fence_kept_whole(self):
        """代码围栏不能被切碎，也不能把代码里的 `#` 当标题。"""
        txt = (
            "可以这样写：\n\n```python\n# 加权平均\ndef gpa(scores):\n"
            "    return sum(s * c for s, c in scores) / sum(c for _, c in scores)\n```\n\n"
            "注意学分是权重，不能直接算术平均。"
        )
        cs = dialogue_import.extract_blocks(
            [{"id": 1, "role": "assistant", "content": txt, "created_at": 1}]
        )
        assert len(cs) == 1, cs
        assert "def gpa" in cs[0]["text"]
        assert "不能直接算术平均" in cs[0]["text"], "代码后面的说明不该被切掉"

    def test_long_block_is_capped(self):
        """超长整块要再切，不然召回一条就占满 top-k。"""
        body = "\n".join(f"- 第{i}条：这是一条很长的说明，用来把整块撑过上限。" for i in range(60))
        cs = dialogue_import.extract_blocks(
            [{"id": 1, "role": "assistant", "content": body, "created_at": 1}]
        )
        assert len(cs) > 1, "超过 1200 字没有切开"
        assert all(len(c["text"]) <= 1300 for c in cs)

    def test_user_side_unchanged(self):
        """user 的部分必须与 extract_candidates 完全一致 —— 那套判据是调好的。"""
        msgs = [
            {"id": 1, "role": "user", "content": "负责人是老陈，工号 KX-3301。", "created_at": 1},
            {"id": 2, "role": "user", "content": "嗯", "created_at": 2},
        ]
        got = dialogue_import.extract_blocks(msgs, roles=("user",))
        want = dialogue_import.extract_candidates(msgs, roles=("user",))
        assert got == want

    def test_extract_candidates_default_still_user_only(self):
        """压缩链路依赖的默认值不许变。

        `core/context_compress.build_segment_meta` 显式传双角色；如果这个默认值
        被顺手改宽，未来的调用方会静默拿到助手候选，kind 跟着变、折叠顺序也变 ——
        这种错不会报错，只会让压缩质量悄悄下降。
        """
        msgs = [
            {"id": 1, "role": "user", "content": "必须记住：周五交材料。", "created_at": 1},
            {"id": 2, "role": "assistant", "content": _ASST_LISTENING, "created_at": 2},
        ]
        cands = dialogue_import.extract_candidates(msgs)
        assert all(c["role"] == "user" for c in cands)

    def test_preview_keeps_section_order(self):
        """预览排序不许打乱章节顺序。

        salience 是按块长度给的，「二、题型技巧」比「一、基础」长，按它排就会
        把第二章排到第一章前面 —— 用户读到「二」才发现「一」在后面，逻辑是断的。

        ★ 第二条断言是防假绿的关键：必须**先证明样本里存在长度倒挂**，
          否则「顺序对了」可能只是因为长度恰好递增，改不改排序都能过。
        """
        cs = dialogue_import.extract_blocks(
            [{"id": 5, "role": "assistant", "content": _ASST_LISTENING, "created_at": 1}]
        )
        by_len = sorted(cs, key=lambda c: len(c["text"]), reverse=True)
        assert by_len[0] is not cs[0] or len(cs[1]["text"]) > len(cs[0]["text"]), (
            "样本退化：本例必须出现「后者比前者长」，否则测不出排序问题"
        )

        ranked = dialogue_import.rank_for_preview(cs)
        assert [c["text"].splitlines()[0] for c in ranked] == [
            "## 一、基础：把耳朵练开",
            "## 二、题型技巧",
            "## 三、应试细节",
            "## 四、常见误区",
        ]

    def test_preview_puts_user_before_assistant(self):
        """用户候选排前面，助手块随后 —— 前端按 role 分组，顺序不能交错。"""
        msgs = [
            {"id": 1, "role": "user", "content": "负责人是老陈，工号 KX-3301。", "created_at": 1},
            {"id": 2, "role": "assistant", "content": _ASST_LISTENING, "created_at": 2},
        ]
        ranked = dialogue_import.rank_for_preview(dialogue_import.extract_blocks(msgs))
        roles = [c["role"] for c in ranked]
        assert roles == ["user"] + ["assistant"] * 4, roles
