# -*- coding: utf-8 -*-
"""P1 轻量版：场景预设的纯逻辑单测（不依赖 DB / LLM）。

验证点：
- get_scenario_prompt 对 None / 未知值安全降级为空串
- 三个已知场景返回非空且带场景标记行
- 场景标记行能被四分支拼装逻辑共用（这里只验证函数本身，拼装是字符串拼接已编译校验）
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.scenarios import get_scenario_prompt, SCENARIOS, SCENARIO_IDS


class TestScenarios(unittest.TestCase):
    def test_none_is_empty(self):
        self.assertEqual(get_scenario_prompt(None), "")

    def test_empty_string_is_empty(self):
        self.assertEqual(get_scenario_prompt(""), "")

    def test_unknown_is_empty(self):
        self.assertEqual(get_scenario_prompt("not_a_real_scenario"), "")

    def test_known_ids_nonempty_and_marker(self):
        for sid in SCENARIO_IDS:
            p = get_scenario_prompt(sid)
            self.assertTrue(p, f"{sid} 应返回非空 prompt")
            self.assertIn("【当前对话场景", p, f"{sid} 应包含场景标记行")

    def test_all_ids_present(self):
        self.assertEqual(set(SCENARIOS.keys()), set(SCENARIO_IDS))

    def test_prompts_mention_no_fabrication(self):
        # 每个场景都应重申"没依据不硬编"，与 P0 诚实兜底协同
        markers = ("不要", "绝不", "明说", "不编造")
        for sid in SCENARIO_IDS:
            p = get_scenario_prompt(sid)
            self.assertTrue(
                any(m in p for m in markers),
                f"{sid} 应重申'没依据不硬编'原则（命中任一关键词 {markers}）",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
