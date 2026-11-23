"""回答正文剥离资料的守卫（2026-11-23）。

用户反馈：智能体把注入给它的资料连【资料开始·X】标签一起复述进回答正文，
聊天里出现一整屏内部原文（含 `score=0.721` 这种检索打分），真正的答案
被挤到几屏之外。

本文件钉四件事：
1. 成对标签能被完整剥掉，正文和块都拿到
2. 只有开标签（模型被截断）也能剥
3. **没有标签的普通回答逐字符不变**（负向：不能误伤正常内容）
4. 各种分隔符/括号变体都认（模型不会逐字节照抄标签）

★ 第 3 条是这次最该守的：剥多了会吃掉用户真正的回答句子，那比不剥更糟。
"""

import unittest

from core import data_boundary as db


class TestStripBlocks(unittest.TestCase):
    # 截图里那段真实文本：两种来源 + 内部打分 + 前后都有正文
    SHOT = (
        "我先查一下库里现在到底有什么，再决定能不能出题。"
        "【资料开始·用户上传】\n**** 日志采集系统\n"
        "Flume_2611101458114844.docx | score=0.721 | 日志采集系统Flume总共25题\n"
        "【资料结束·用户上传】\n\n"
        "【资料开始·知识库】\n** 对话·数据资料入库与复习准备 | score=0.666 | 1. 网络数据采集\n"
        "【资料结束·知识库】\n"
        "下面开始出题：1. 如果数据只是面向 Hadoop 的，推荐使用 Flume。"
    )

    def test_strips_both_blocks(self):
        clean, blocks = db.strip_blocks(self.SHOT)
        self.assertEqual(len(blocks), 2, "两种来源的资料块都该被剥出来")
        self.assertEqual({b["source"] for b in blocks}, {"upload", "kb"})

    def test_no_marker_left_in_body(self):
        """正文里不能残留任何标签 —— 否则用户还是看到「【资料开始…」。"""
        clean, _ = db.strip_blocks(self.SHOT)
        self.assertNotIn("资料开始", clean)
        self.assertNotIn("资料结束", clean)

    def test_no_internal_score_leaked(self):
        """检索打分是实现细节，不能出现在用户正文里。"""
        clean, _ = db.strip_blocks(self.SHOT)
        self.assertNotIn("score=", clean)

    def test_keeps_real_answer(self):
        """剥的只是资料，答案一个字都不能少。"""
        clean, _ = db.strip_blocks(self.SHOT)
        self.assertTrue(clean.startswith("我先查一下库里"), clean[:30])
        self.assertIn("推荐使用 Flume", clean)

    def test_plain_answer_untouched(self):
        """★ 负向守卫：没有标签的回答必须逐字符原样返回。"""
        plain = "线性代数里矩阵的秩等于行秩等于列秩。\n\n第二段：普通回答。"
        clean, blocks = db.strip_blocks(plain)
        self.assertEqual(clean, plain)
        self.assertEqual(blocks, [])

    def test_normal_sentence_with_words_not_stripped(self):
        """★ 负向守卫：正文里出现「资料开始」四个字也不能被误剥。"""
        for bad in (
            "资料开始解释了这个问题。",
            "我们看看【资料开始】是什么。",
            "【资料开始·用户上传了文件】这句是正常正文。",
        ):
            clean, blocks = db.strip_blocks(bad)
            self.assertEqual(clean, bad, f"误剥了正常正文：{bad}")
            self.assertEqual(blocks, [])

    def test_open_without_close(self):
        """模型被截断：只有开标签也要剥掉（否则那半截会更难看）。"""
        clean, blocks = db.strip_blocks("先做题：【资料开始·知识库】1. 网络数据采集（33题）")
        self.assertEqual(len(blocks), 1)
        self.assertNotIn("资料开始", clean)
        self.assertIn("先做题", clean)

    def test_separator_variants(self):
        """分隔符/括号变体都要认 —— 模型复述时不逐字节照抄。"""
        variants = [
            "【资料开始·用户上传】X【资料结束·用户上传】",
            "【资料开始・用户上传】X【资料结束・用户上传】",
            "【资料开始.用户上传】X【资料结束.用户上传】",
            "[资料开始·用户上传]X[资料结束·用户上传]",
            "【资料开始：用户上传】X【资料结束：用户上传】",
        ]
        for v in variants:
            clean, blocks = db.strip_blocks("前" + v + "后")
            self.assertEqual(len(blocks), 1, f"没认出变体：{v}")
            self.assertNotIn("X", clean)
            self.assertIn("前", clean)
            self.assertIn("后", clean)

    def test_empty_block_not_recorded(self):
        clean, blocks = db.strip_blocks("前文【资料开始·用户上传】【资料结束·用户上传】后文")
        self.assertEqual(blocks, [])
        self.assertIn("前文", clean)
        self.assertIn("后文", clean)

    def test_long_block_truncated(self):
        clean, blocks = db.strip_blocks(
            "【资料开始·用户上传】" + "字" * 6000 + "【资料结束·用户上传】答案在这"
        )
        self.assertTrue(blocks[0]["truncated"])
        self.assertLessEqual(len(blocks[0]["text"]), 4000)
        self.assertIn("答案在这", clean)

    def test_idempotent(self):
        clean, _ = db.strip_blocks(self.SHOT)
        again, blocks2 = db.strip_blocks(clean)
        self.assertEqual(again, clean)
        self.assertEqual(blocks2, [])


if __name__ == "__main__":
    unittest.main()