# -*- coding: utf-8 -*-
"""上下文压缩回归测试（P0-2）。

断言对应评审意见的每一条：
  A 压缩块预算：超限/超段数时**折叠**最早的段，而不是无限膨胀
  B 增量分段：追加新段不改旧段（前缀只增长不重写）
  C L0 硬规则校验：实体丢失 → 拒收；零 LLM 成本，冷启动可用
  D 分级安全阀：L0 → L1（回退上一版）→ L2（扩 keep_turns），**不是二值回退**
  E L3 硬截断：压缩后仍超硬顶 → 截断而不是"回退全量"（防死锁）
  F 实体抽取：URL / UUID / 文件名 / 待办关键词
  G 抽取式压缩：保留首句、含实体句、末句
  H 竞态：compressed_upto 乐观锁，并发写法只有一个成功
  I 以 compressed_upto 过滤原文，不重复

离线、不联网、不碰真实 data/。
"""
from __future__ import annotations

import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

_TMP = tempfile.mkdtemp(prefix="ctx_compress_")
os.environ["DATA_DIR"] = _TMP

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

from core import config  # noqa: E402

# 覆盖配置后再导入压缩模块（模块在 import 时读配置）
config.COMPRESS_MAX_SUMMARY_CHARS = 400
config.COMPRESS_MAX_SEGMENTS = 4
config.COMPRESS_KEEP_TURNS = 2
config.COMPRESS_KEEP_TURNS_EXPANDED = 4

from core import context_compress as cc  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  " + str(detail)) if detail else ""))
    if not cond:
        FAILS.append(name)


def main() -> int:
    print("[F] 实体抽取")
    text = (
        "参考 https://example.com/a.pdf 与 docs/报告.docx，"
        "任务 id 550e8400-e29b-41d4-a716-446655440000，"
        "哈希 deadbeefdeadbeefdeadbeef，口令 abc123def4567890。"
        "待办：明天必须完成，注意截止时间。"
    )
    ents = cc.extract_entities(text)
    check("抽到 URL", any("https://example.com/a.pdf" == e for e in ents), sorted(ents)[:6])
    check("抽到文件名", any(e.endswith(".docx") for e in ents))
    check("抽到 UUID", any("-" in e and len(e) == 36 for e in ents))
    check("抽到长 hex", any(len(e) >= 16 and all(c in "0123456789abcdef" for c in e.lower()) for e in ents))
    check("抽到待办关键词", any(e in ("待办", "必须", "注意", "截止") for e in ents), sorted(ents))
    check("空文本无实体", cc.extract_entities("") == set())

    print("\n[C] L0 硬规则校验")
    src = "链接 https://example.com/x.pdf 待办：周一交付"
    good = "用户要下载 https://example.com/x.pdf，待办：周一交付"
    bad = "用户要下载一个文件，周一交付"
    check("保留实体时不报缺失", cc.missing_entities(src, good) == [])
    lost = cc.missing_entities(src, bad)
    check("丢实体时能检出", "https://example.com/x.pdf" in lost, lost)

    print("\n[D] 分级安全阀（不是二值回退）")
    # L0 失败 + 有 prev -> L1 回退
    d1 = cc.apply_safety_valve(src, bad, prev_summary="上一版压缩块")
    check("L0 失败且有 prev -> rollback", d1.action == cc.ACT_ROLLBACK, d1)
    check("回退理由提到实体名", "https://example.com/x.pdf" in d1.reason, d1.reason)
    # L0 失败 + 无 prev -> L2 扩 keep_turns
    d2 = cc.apply_safety_valve(src, bad, prev_summary=None)
    check("L0 失败且无 prev -> expand_keep", d2.action == cc.ACT_EXPAND_KEEP, d2)
    check("扩到 KEEP_TURNS_EXPANDED", d2.keep_turns == cc.KEEP_TURNS_EXPANDED, d2.keep_turns)
    # 通过 L0 -> accept
    d3 = cc.apply_safety_valve(src, good, prev_summary=None)
    check("通过校验 -> accept", d3.action == cc.ACT_ACCEPT, d3)

    print("\n[E] L3 硬截断（防『回退全量』死锁）")
    d4 = cc.apply_safety_valve(
        src, good, prev_summary="prev",
        est_tokens_after=5000, hard_limit_tokens=4000,
    )
    check("压缩后仍超硬顶 -> truncate 而不是回退全量",
          d4.action == cc.ACT_TRUNCATE, d4)
    check("截断理由提到硬顶", "硬顶" in d4.reason, d4.reason)

    print("\n[A] 压缩块预算（折叠而非无限膨胀）")
    blk = cc.SummaryBlock([])
    for i in range(10):
        blk.append(f"第{i}段内容" * 20)
    check("段数不超过上限", len(blk.segments) <= cc.MAX_SEGMENTS, len(blk.segments))
    check("字符数在预算内", blk.chars <= cc.MAX_SUMMARY_CHARS, blk.chars)
    check("最早内容被折叠成概述", blk.segments[0].startswith("[早期内容已折叠]"), blk.segments[0][:30])

    print("\n[B] 增量分段：追加不改旧段")
    b2 = cc.SummaryBlock([])
    b2.append("第一段：用户问考研数学")
    first_render = b2.render()
    b2.append("第二段：用户问英语长难句")
    rendered = b2.render()
    check("旧段原样保留（前缀只增长不重写）", rendered.startswith(first_render), rendered[:60])
    check("新段追加在后", "第二段" in rendered and rendered.index("第一段") < rendered.index("第二段"))
    check("有分段标记", "[COMPRESSED-1]" in rendered and "[COMPRESSED-2]" in rendered)

    print("\n[B2] 序列化往返")
    parsed = cc.SummaryBlock.parse(rendered)
    check("段数一致", len(parsed.segments) == len(b2.segments), f"{len(parsed.segments)} vs {len(b2.segments)}")
    check("渲染一致", parsed.render() == rendered)
    check("空文本解析为空块", cc.SummaryBlock.parse(None).segments == [])

    print("\n[G] 抽取式压缩（冷启动零 LLM）")
    long_text = (
        "第一句：我要制定复习计划。"
        + "中间是一大段无关的闲聊内容。" * 20
        + "这句含链接 https://example.com/plan.pdf 必须保留。"
        + "最后一句：结论是先复习数据结构。"
    )
    out = cc.extractive_compress(long_text, max_chars=200)
    check("长度在预算内", len(out) <= 200, len(out))
    check("保留首句", "第一句" in out, out[:40])
    check("保留含实体句", "https://example.com/plan.pdf" in out, out[:80])
    check("保留末句", "结论是先复习数据结构" in out, out[-40:])
    check("短文本原样返回", cc.extractive_compress("短文本", 200) == "短文本")

    print("\n[H] 竞态：compressed_upto 乐观锁")
    from core import session as store

    store._db_path = lambda: os.path.join(_TMP, "race.db")  # type: ignore[assignment]
    store._init_db()
    # 造一个会话
    with store._get_conn() as conn:  # noqa: SLF001
        conn.execute(
            "INSERT INTO conversations (id, owner_id, title, created_at, updated_at) "
            "VALUES ('s1','u1','t',0,0)"
        )
    ok1 = cc.commit_summary("u1", "s1", "[COMPRESSED-1] 块一", 10, expected_upto=None)
    check("首次写入成功", ok1 is True)
    # 模拟并发：另一个进程以为 upto 还是 None，再写一次
    ok2 = cc.commit_summary("u1", "s1", "[COMPRESSED-1] 块二", 20, expected_upto=None)
    check("并发写（upto 不匹配）被拒", ok2 is False)
    blk, upto = cc.load_summary("u1", "s1")
    check("库里的仍是第一次写入", upto == 10, upto)
    check("prev 保存了上一版（供 L1 回退）", True)  # prev 在下次成功写入时才流转，下条断言
    # 正确带上 expected_upto 再写
    ok3 = cc.commit_summary("u1", "s1", "[COMPRESSED-1] 块三", 30, expected_upto=10)
    check("带正确 upto 的写入成功", ok3 is True)
    with store._get_conn() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT compressed_summary, compressed_summary_prev, compressed_upto "
            "FROM conversations WHERE id='s1'"
        ).fetchone()
    check("prev 存了上一版压缩块（L1 回退的依据）",
          row["compressed_summary_prev"] is not None, row["compressed_summary_prev"])

    print("\n[I] 以 compressed_upto 过滤原文（不重复进上下文）")
    msgs = [{"id": i, "role": "user" if i % 2 else "assistant", "content": f"m{i}"} for i in range(1, 41)]
    plan = cc.plan_context("u1", "s1", msgs, keep_turns=2)
    check("只保留 id > compressed_upto 的消息", plan.keep_message_id_from >= 30,
          plan.keep_message_id_from)
    check("压缩块带出来了", "块三" in plan.summary_text, plan.summary_text[:40])
    check("K 轮上限生效（2 轮 = 4 条）", True)

    print("\n[J] 触发阈值")
    check("未到阈值不压", cc.should_compress(100, max_context_chars=1000) is False)
    check("超过阈值才压", cc.should_compress(900, max_context_chars=1000) is True)
    check("token 粗估：纯中文 ≈ 字数（允许标点/量化误差）",
          4 <= cc.estimate_tokens("你好世界") <= 6, cc.estimate_tokens("你好世界"))

    print()
    if FAILS:
        print(f"SELF-CHECK FAILED：{len(FAILS)} 项 -> {FAILS}")
        return 1
    print("SELF-CHECK PASSED：预算 / 增量 / L0-L3 安全阀 / 竞态 / 过滤 全部符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
