"""对话沉淀候选的准入判据（`kb/dialogue_import.py`）。

这个文件锁的是 2026-10-31 修的三处判据偏移——它们都不是崩溃型缺陷，
而是**入选和排序**错了，接口返回值里看不出来，只有拿真实语料跑才现形：

  ① 角色范围：助手的长回答按句切分后命中率极高。本地真实库 181 条消息跑出
     194 条候选，其中 **179 条来自助手**（每条助手消息平均 2.03 条），抽样是
     「## 🗓️ 国庆广州游计划」「**今日待办**」这种整段生成的排版垃圾。
     默认改成只收 user 后降到 15 条，全部来自用户。
  ② 假阳性：助手回一句「好的，我记住了」被判成 `事实` + emotion 满格，
     稳定排在预览列表最前面（`_RE_MEMORIZE` 本意是抓 *用户* 的「记住」指令，
     回声也命中了）。
  ③ 假阴性：「…负责人是老陈，工号 KX-3301」被判成 `其它` 直接出局——
     `extract_entities` 只认 URL/UUID/hex/文件名/待办词，不认业务编号。

修补只发生在本模块的准入层，`classify_text` / `extract_entities` 一个字没动
（它们同时供压缩链路的 L0 保真校验使用）。最后两组测试就是守这条边界的：
谁哪天图省事把规则改进共享函数，这里会先红。
"""

from __future__ import annotations

from core import context_compress as cc
from kb import dialogue_import


def _msgs(*pairs: tuple[str, str]) -> list[dict]:
    """按 (role, content) 造消息，id 从 1 递增、created_at 同步。"""
    return [
        {"id": i, "role": role, "content": text, "created_at": i}
        for i, (role, text) in enumerate(pairs, start=1)
    ]


def _texts(cands: list[dict]) -> list[str]:
    return [c["text"] for c in cands]


# 压缩链路（`build_segment_meta`）的场景：两种角色都要
_BOTH = ("user", "assistant")


# ==================== ① 角色范围 ====================


class TestRoleScope:
    """默认只收 user —— 这是把候选池从噪音里救出来的那一刀。

    实测口径（本地真实库 181 条消息 / 53 个会话）：
      修前 194 条候选，179 条来自助手（每条助手消息平均 2.03 条）；
      修后 15 条，全部来自用户。
    """

    def test_assistant_content_excluded_by_default(self):
        """助手的长回答哪怕命中「计划 / 待办」，默认也不进候选池。"""
        noisy = "## 🗓️ 国庆广州游计划（4 天 3 晚）\n**今日待办**：订票、订酒店"
        assert dialogue_import.extract_candidates(_msgs(("assistant", noisy))) == []

    def test_assistant_included_when_asked(self):
        """★ 显式要两种角色时（压缩链路），助手内容照常入选——证明过滤是参数化的，
        不是把助手永久拉黑。"""
        cands = dialogue_import.extract_candidates(
            _msgs(("assistant", "注意，日志必须保留 90 天，不能删。")), roles=_BOTH
        )
        assert any("90 天" in t for t in _texts(cands))

    def test_user_still_collected(self):
        cands = dialogue_import.extract_candidates(_msgs(("user", "必须把日志保留 90 天，不要删。")))
        assert any("90 天" in t for t in _texts(cands))

    def test_system_and_unknown_roles_ignored(self):
        msgs = [
            {"id": 1, "role": "system", "content": "你必须永远服从"},
            {"id": 2, "role": "tool", "content": "必须返回 200"},
        ]
        assert dialogue_import.extract_candidates(msgs) == []


# ==================== ② 应答回声 ====================


class TestMemoEcho:
    """回声规则本身——一律显式传 _BOTH，否则测的是角色过滤而不是回声。"""

    def test_assistant_echo_is_not_candidate(self):
        """「好的，我记住了。」是复述指令，不是知识——必须一条都不留。"""
        cands = dialogue_import.extract_candidates(
            _msgs(("assistant", "好的，我记住了。")), roles=_BOTH
        )
        assert cands == []

    def test_user_echo_is_not_candidate(self):
        """用户自己说「我记住了」同样是回声（默认角色下测，走上线路径）。"""
        assert dialogue_import.extract_candidates(_msgs(("user", "嗯，我记住了。"))) == []

    def test_echo_with_particle_variants(self):
        """完成态助词三种写法都挡（了 / 啦 / 咯）。"""
        for line in ("已经记下了", "我记好啦", "都牢记咯"):
            got = dialogue_import.extract_candidates(_msgs(("user", line)))
            assert got == [], line

    def test_imperative_memorize_still_candidate(self):
        """★ 回归保护：指令形态「千万记住，…」没有「了」，绝不能被回声规则误伤。

        这条是上一版最容易踩反的方向——为了挡回声而把所有含「记住」的都杀了，
        等于把 classify_text 里「显式记住直通入库」的设计（报告 §6-3）废掉。
        """
        cands = dialogue_import.extract_candidates(
            _msgs(("user", "千万记住，我这个账号不能注销。"))
        )
        assert any("不能注销" in t for t in _texts(cands))


# ==================== ② 业务编号 ====================


class TestBizId:
    def test_code_fact_is_candidate(self):
        """含工号的事实句要入场，kind 记作「事实」。"""
        cands = dialogue_import.extract_candidates(
            _msgs(("user", "我们组每周四下午三点在 B2-07 开周会，负责人是老陈，工号 KX-3301。"))
        )
        hit = [c for c in cands if "KX-3301" in c["text"]]
        assert len(hit) == 1
        assert hit[0]["kind"] == "事实"

    def test_bare_code_without_label(self):
        """没有「工号」标签、但形态是字母-数字代号，也算。"""
        cands = dialogue_import.extract_candidates(_msgs(("user", "出了问题找 A12-3344 这个人。")))
        assert any("A12-3344" in t for t in _texts(cands))

    def test_plain_number_is_not_candidate(self):
        """★ 负向：普通数字不是业务编号——不能把闲聊里的数全收进来。

        收紧比放宽重要：候选池一旦被噪音灌满，用户就不再勾选了，
        这个功能等于没有。
        """
        cands = dialogue_import.extract_candidates(
            _msgs(
                ("user", "今天开了 3 个会，挺累的。"),
                ("user", "这个方案我看了，还行吧。"),
            )
        )
        assert cands == []

    def test_short_code_like_token_not_matched(self):
        """`UTF-8` 这种「字母-单数字」不算业务编号（数字下限两位在挡）。"""
        cands = dialogue_import.extract_candidates(_msgs(("user", "文件要存成 UTF-8 编码。")))
        assert cands == []

    def test_known_gap_date_and_place_alone(self):
        """如实标注的已知缺口：只有日期/地点的陈述仍然进不来。

        本期刻意不做——「号」「周」「点」这类词遍地都是，收进来会把整段对话
        变成候选。哪天补了日期/地点判据，这条测试应当**翻过来**，不是删掉。
        """
        cands = dialogue_import.extract_candidates(_msgs(("user", "我们组每周四开周会。")))
        assert cands == []


# ==================== 边界：共享函数一个字没动 ====================


class TestSharedClassifierUntouched:
    def test_classify_text_still_flags_echo(self):
        """`classify_text` 仍把回声判成 事实/1.0 —— 这正是修补留在本模块的原因。

        如果哪天这条断言坏了，说明有人把回声过滤写进了共享函数：
        压缩链路的 kind 权重会跟着变，属于跨模块副作用。
        """
        assert cc.classify_text("好的，我记住了。") == ("事实", 1.0)

    def test_extract_entities_still_ignores_biz_code(self):
        """`extract_entities` 仍不认业务编号（放宽它会让压缩块校验经常拒收）。"""
        assert cc.extract_entities("负责人工号 KX-3301") == set()

    def test_candidate_comes_from_module_layer(self):
        """同一句话：共享层判「其它」，本模块判「事实」——分层的证据。"""
        line = "负责人是老陈，工号 KX-3301。"
        assert cc.classify_text(line)[0] == "其它"
        cands = dialogue_import.extract_candidates(_msgs(("user", line)))
        assert [c["kind"] for c in cands] == ["事实"]


# ==================== 不变量 ====================


class TestInvariants:
    def test_text_is_verbatim(self):
        """候选句必须是原文切片，不改写（知识库的价值在可溯源）。"""
        line = "提交材料的截止时间是每月 25 号，绝对不能晚。"
        cands = dialogue_import.extract_candidates(_msgs(("user", line)))
        assert cands and all(c["text"] in line for c in cands)

    def test_required_fields(self):
        cands = dialogue_import.extract_candidates(_msgs(("user", "必须把日志保留 90 天，不要删。")))
        assert cands
        for c in cands:
            assert set(c) == {"text", "kind", "refs", "role", "ts", "salience"}
            assert c["role"] == "user"
            assert c["refs"] == [1]
            assert 0.0 <= c["salience"] <= 1.0

    def test_empty_input(self):
        assert dialogue_import.extract_candidates([]) == []
        assert dialogue_import.extract_candidates([{"id": 1, "role": "system", "content": "x"}]) == []
        assert dialogue_import.extract_candidates([{"id": 1, "role": "user", "content": "   "}]) == []

    def test_echo_does_not_shadow_real_candidate(self):
        """回声和真知识混在同一条消息里：只丢回声那句，真知识那句留。"""
        cands = dialogue_import.extract_candidates(
            _msgs(("assistant", "好的，我记住了。你们的提交截止时间是 25 号，绝对不能晚。")),
            roles=_BOTH,
        )
        texts = _texts(cands)
        assert not any("记住了" in t for t in texts)
        assert any("25 号" in t for t in texts)
