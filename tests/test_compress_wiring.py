"""上下文压缩"接线"回归测试（P0-2 §6.2：压缩块接入请求路径）。

`test_context_compress.py` 验的是压缩**算法**（预算/增量/安全阀/竞态），
本文件验的是**接线**——即 `assemble_history` / `maybe_compress` 接进
`api/chat.py` 之后，红线上真实发生什么：

  K 无压缩块时**原样直通**（绝不把 30 轮窗口偷偷缩成 6 轮 —— 功能倒退）
  L 有压缩块时：前置一对 human→ai + 最近 K 轮，被压缩的原文不再出现
  M 前置说明写明"不是用户的新提问"（防模型把摘要当本轮诉求执行）
  N 两次组装逐字节一致（稳定前缀才保得住 KV 缓存）
  O 未到阈值不写库 / 到阈值写库且 compressed_upto 前进
  P 二次压缩只追加不改旧段（前缀只增长）
  Q L1 回退：新压缩丢实体且有上一版 → 不写库、库里保持原样
  R 总开关 COMPRESS_ENABLED=0 → 读写两侧都退回旧行为
  S 压缩层异常 → 降级为原样返回，不抛给对话

离线、不联网、不碰真实 data/。
"""

from __future__ import annotations

import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

_TMP = tempfile.mkdtemp(prefix="ctx_wiring_")
os.environ["DATA_DIR"] = _TMP

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

from core import config  # noqa: E402

# 覆盖配置后再导入压缩模块（模块在 import 时读配置）
config.COMPRESS_ENABLED = True
config.COMPRESS_MAX_CONTEXT_CHARS = 300  # 阈值 = 300 * 0.8 = 240 字符，测试里几十字就能触发
config.COMPRESS_KEEP_TURNS = 2
config.COMPRESS_KEEP_TURNS_EXPANDED = 4
config.COMPRESS_MAX_SUMMARY_CHARS = 4000
config.COMPRESS_MAX_SEGMENTS = 12

from core import context_compress as cc  # noqa: E402
from core import session as store  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  " + str(detail)) if detail else ""))
    if not cond:
        FAILS.append(name)


def _new_conv(sid: str) -> None:
    with store._get_conn() as conn:  # noqa: SLF001
        conn.execute(
            "INSERT INTO conversations (id, owner_id, title, created_at, updated_at) "
            "VALUES (?,?,?,?,?)",
            (sid, "u1", sid, 0, 0),
        )


def _fill(sid: str, n_turns: int, tag: str) -> None:
    """灌 n 轮问答，内容足够长以越过阈值。"""
    for i in range(n_turns):
        store.add_message("u1", sid, "user", f"{tag} 第{i}问：我在准备考研数学，链接 {tag}-{i}.pdf")
        store.add_message("u1", sid, "assistant", f"{tag} 第{i}答：建议先过一遍高数基础章节。")


def main() -> int:
    store._db_path = lambda: os.path.join(_TMP, "wiring.db")  # type: ignore[assignment]
    store._init_db()

    # ---------- K：无压缩块时原样直通 ----------
    print("[K] 没有压缩块时：原样直通，不缩窗口")
    _new_conv("s_none")
    _fill("s_none", 5, "NONE")  # 10 条消息，远超 KEEP_TURNS=2 的 4 条
    raw = store.get_messages("u1", "s_none", include_system=False)
    out = cc.assemble_history("u1", "s_none", raw)
    check("不注入压缩前置", not any(cc.PRELUDE_HEAD[:8] in m["content"] for m in out), out[:1])
    check("消息条数与原文一致（没套 K 轮窗口）", len(out) == len(raw), f"{len(out)} vs {len(raw)}")
    check("首条就是第一条原文", out[0]["content"].startswith("NONE 第0问"), out[0]["content"][:20])

    # ---------- O：未到阈值不写库 ----------
    print("\n[O1] 未到阈值不写库")
    before = cc.load_summary("u1", "s_none")[1]
    r = cc.maybe_compress("u1", "s_none", max_context_chars=10**9)
    check("返回 below_threshold", r["reason"] == "below_threshold", r)
    check("compressed_upto 未变", cc.load_summary("u1", "s_none")[1] == before)

    # ---------- O：达阈值写库 ----------
    print("\n[O2] 达到阈值写库，compressed_upto 前进")
    r = cc.maybe_compress("u1", "s_none", force=True)
    check("写入成功", r["ok"] is True, r)
    block, upto = cc.load_summary("u1", "s_none")
    total = len(raw)
    check("compressed_upto 停在倒数第 K 轮之前", upto == total - cc.KEEP_TURNS * 2, f"{upto}/{total}")
    check("压缩块有分段标记", block.render().startswith("[COMPRESSED-1]"), block.render()[:30])
    check(
        "压缩块保留了硬实体（L0 通过）",
        "NONE-0.pdf" in block.render() or "NONE-1.pdf" in block.render(),
        block.render()[:80],
    )

    # ---------- L：有压缩块时前置 + 最近 K 轮 ----------
    print("\n[L] 有压缩块时：前置一对 human→ai + 最近 K 轮")
    out2 = cc.assemble_history("u1", "s_none", store.get_messages("u1", "s_none"))
    check("第 1 条是 user 且带压缩块", out2[0]["role"] == "user" and "[COMPRESSED-1]" in out2[0]["content"])
    check("第 2 条是 assistant 的确认", out2[1]["role"] == "assistant", out2[1]["content"][:20])
    tail = out2[2:]
    check("尾巴正好是 K 轮（K=2 → 4 条）", len(tail) == cc.KEEP_TURNS * 2, len(tail))
    check("尾巴是最近的原文", tail[-1]["content"].startswith("NONE 第4答"), tail[-1]["content"][:20])
    joined = "\n".join(m["content"] for m in tail)
    check("被压缩的旧原文不再出现（不重复进上下文）", "NONE 第0问" not in joined, joined[:60])
    check("角色序列合法（无连续两条 human）", out2[0]["role"] == "user" and out2[1]["role"] == "assistant")

    # ---------- M：前置说明 ----------
    print("\n[M] 前置说明写明『不是用户的新提问』")
    check("含『不是用户的新提问』", "不是用户的新提问" in cc.PRELUDE_HEAD, cc.PRELUDE_HEAD[:40])
    check("含『不要编造』约束", "不要编造" in cc.PRELUDE_HEAD)
    check("确认语不是空串", bool(cc.PRELUDE_ACK.strip()))

    # ---------- N：稳定前缀 ----------
    print("\n[N] 两次组装逐字节一致（保 KV 缓存前缀）")
    a = cc.assemble_history("u1", "s_none", store.get_messages("u1", "s_none"))
    b = cc.assemble_history("u1", "s_none", store.get_messages("u1", "s_none"))
    check("连续两次组装完全相同", a == b)

    # ---------- P：二次压缩只追加不改旧段 ----------
    print("\n[P] 二次压缩：只追加不改旧段")
    old_render = cc.load_summary("u1", "s_none")[0].render()
    store.add_message("u1", "s_none", "user", "追问：线性代数怎么安排？")
    store.add_message("u1", "s_none", "assistant", "答：线性代数放在高数之后。")
    r2 = cc.maybe_compress("u1", "s_none", force=True)
    check("第二次写入成功", r2["ok"] is True, r2)
    new_render = cc.load_summary("u1", "s_none")[0].render()
    check("旧段原样保留（前缀只增长不重写）", new_render.startswith(old_render), new_render[:60])
    check("多了第 2 段", "[COMPRESSED-2]" in new_render, new_render[-60:])

    # ---------- Q：L1 回退 ----------
    print("\n[Q] L1 回退：新压缩丢实体且有上一版 → 不写库")
    # 再灌 2 轮，让"最近 K 轮之前"重新有内容可压（否则 nothing_to_compress）
    _fill("s_none", 2, "TAIL")
    with store._get_conn() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT compressed_summary, compressed_upto FROM conversations WHERE id='s_none'"
        ).fetchone()
    upto_before = int(row["compressed_upto"])
    # 注入一个"会把实体丢光"的压缩器，模拟规则抽取失手
    r3 = cc.maybe_compress(
        "u1", "s_none", force=True, compressor=lambda _raw: "用户聊过考研的事，没有别的细节。"
    )
    check("被安全阀拦下（未写库）", r3["ok"] is False, r3)
    check("理由是实体丢失回退", "实体丢失" in r3["reason"], r3["reason"])
    check("库里 compressed_upto 原地不动", cc.load_summary("u1", "s_none")[1] == upto_before)
    check(
        "库里压缩块未被污染",
        cc.load_summary("u1", "s_none")[0].render() == new_render,
    )

    # ---------- S：异常降级 ----------
    print("\n[S] 压缩层异常 → 降级为原样返回")
    orig = cc.plan_context

    def _boom(*_a, **_kw):
        raise RuntimeError("模拟 DB 抖动")

    cc.plan_context = _boom  # type: ignore[assignment]
    try:
        deg = cc.assemble_history("u1", "s_none", store.get_messages("u1", "s_none"))
        check("异常时退回原文而不是抛错", deg and "role" in deg[0], str(deg[:1]))
        check("降级结果不含压缩前置", "[COMPRESSED" not in deg[0]["content"], deg[0]["content"][:30])
    finally:
        cc.plan_context = orig  # type: ignore[assignment]

    # ---------- R：总开关 ----------
    print("\n[R] COMPRESS_ENABLED=0 → 读写两侧退回旧行为")
    config.COMPRESS_ENABLED = False
    try:
        off = cc.assemble_history("u1", "s_none", store.get_messages("u1", "s_none"))
        check("关闭后不注入压缩块", "[COMPRESSED" not in off[0]["content"], off[0]["content"][:30])
        check("关闭后条数等于原文条数", len(off) == len(store.get_messages("u1", "s_none")), len(off))
        roff = cc.maybe_compress("u1", "s_none", force=True)
        check("关闭后不写库", roff["reason"] == "disabled", roff)
    finally:
        config.COMPRESS_ENABLED = True

    print()
    if FAILS:
        print(f"SELF-CHECK FAILED：{len(FAILS)} 项 -> {FAILS}")
        return 1
    print("SELF-CHECK PASSED：接线 / 直通 / 前置 / 稳定前缀 / 增量 / L1 回退 / 降级 / 开关 全部符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
