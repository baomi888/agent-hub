"""Prompt 自进化闭环（GEPA 式 · issue #12）：采集 → 变异 → 评估 → 择优 → 存库。

六层对应关系
------------
  ① collector  `collect_cases()`        从 feedbacks 拉差样本 + 分层混入 up 样本
  ② mutator    `propose_diffs()`        失败模式 → 结构化 diff（过 `core/prompt_guard` 结构校验）
  ③ evaluator  `evaluate()`             固定回放集上重跑，judge 出 M1–M4 + M5/M6
  ④ selector   `select()`               过闸门 → 帕累托前沿 → 最小 diff 择优
  ⑤ store      `core/prompt_store`      版本快照 / 评估留档 / promote / rollback
  ⑥ worker     `due_subjects()/run_one()` 注册在 `prompt.evolve` job 上

三条产品级约束（都落在代码里，不靠自觉）
--------------------------------------
1. **模型绝不自动上线**：候选默认只到 `shadow`；`PROMPT_EVO_AUTO_PROMOTE` 默认关。
2. **越界变异直接丢**：`prompt_guard` 黑名单 / ≤6 行 / 禁止 kb_id，绕过不了。
3. **数据不够就不跑**：样本 < 200 直接 `insufficient_data`，不烧 LLM 也不给结论。

隐私
----
用户 `comment` **原文不进任何提示词**：它只用来打失败模式标签（`classify`），
落库的是标签不是原文；问答文本进回放集前先过 `_redact()`（手机号 / 邮箱 /
身份证 / URL query 一律打码），并按 `PROMPT_EVO_MAX_CASE_CHARS` 截断。
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import time
from typing import Any, Callable

from core import config, prompt_guard, prompt_stats, prompt_store
from core import session as store

_log = logging.getLogger("service.prompt_evo")

JOB_KEY = "prompt.evolve"

# ==================== 路径 → 内置基线 ====================


def default_prompt(path: str) -> str:
    """各 path 的内置基线（代码里的常量，version=0 用的就是它）。

    延迟 import：`kb.rag_core` 会拉起 chromadb，`agent.builder` 会拉起 langchain。
    本模块的采集 / 统计部分不需要这些依赖，不该因为 import 本文件就全量加载。
    """
    if path == "agent":
        from agent.builder import SYSTEM_PROMPT

        return SYSTEM_PROMPT
    if path == "rag_note_low":
        from kb.rag_core import LOW_CONFIDENCE_NOTE

        return LOW_CONFIDENCE_NOTE
    if path == "rag_note_medium":
        from kb.rag_core import MARGINAL_CONFIDENCE_NOTE

        return MARGINAL_CONFIDENCE_NOTE
    raise KeyError(f"未知提示词路径: {path}")


def known_paths() -> tuple[str, ...]:
    return tuple(getattr(config, "PROMPT_EVO_PATHS", ()) or ())


# ==================== ① 采集 ====================

# 失败模式（标签即 mutator 的归因输入）。顺序 = 优先级。
FAILURE_MODES = (
    "retrieval_weak",  # 检索弱硬凑
    "should_refuse",  # 该拒答没拒
    "tool_misroute",  # 工具选错 / 重复调用
    "fake_citation",  # 编造引用编号
    "factual",  # 常识 / 事实错误
    "format",  # 格式 / 长度问题
    "unspecified",  # 有点踩但没写原因
)
FAILURE_LABELS = {
    "retrieval_weak": "检索弱硬凑",
    "should_refuse": "该拒答没拒",
    "tool_misroute": "工具选错",
    "fake_citation": "编造引用",
    "factual": "常识错误",
    "format": "格式违规",
    "unspecified": "未分类",
    "up": "正向样本",
}

# 用户 comment 里的关键词 → 失败模式。comment **只走这里**，原文不落库、不进提示词。
_TAG_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("fake_citation", ("编造", "瞎编", "假引用", "引用不对", "来源不对")),
    ("should_refuse", ("胡编", "胡说", "明明没有", "不该答", "拒答", "没有依据", "乱答", "不知道")),
    ("retrieval_weak", ("没答到", "答非所问", "不相关", "硬凑", "资料不对", "检索")),
    ("tool_misroute", ("工具", "搜索", "计算", "天气", "下载", "联网")),
    ("factual", ("错", "不对", "事实", "常识", "搞错")),
    ("format", ("格式", "排版", "太长", "太短", "啰嗦", "重复", "结构")),
)

_REFUSAL_MARKERS = (
    "没有直接相关",
    "没有相关",
    "查不到",
    "找不到",
    "无法回答",
    "不能按资料",
    "暂无",
    "没有足够",
    "只找到沾边",
)

_CITE_RE = re.compile(r"\[(\d{1,2})\]")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_ID_RE = re.compile(r"(?<!\d)\d{15,18}[0-9Xx](?!\d)")
_URL_QUERY_RE = re.compile(r"(https?://[^\s?#]+)[?#][^\s]*")


def _redact(text: str, limit: int | None = None) -> str:
    """回放样本脱敏 + 截断。宁可少信息，也不把联系方式带进评估集。"""
    s = str(text or "")
    s = _EMAIL_RE.sub("<email>", s)
    s = _PHONE_RE.sub("<phone>", s)
    s = _ID_RE.sub("<id>", s)
    s = _URL_QUERY_RE.sub(r"\1", s)
    cap = int(limit or getattr(config, "PROMPT_EVO_MAX_CASE_CHARS", 800) or 800)
    if cap > 0 and len(s) > cap:
        s = s[:cap] + "…"
    return s.strip()


def _parse_refs(raw: Any) -> list[dict]:
    """messages.refs 可能是 JSON 串或 list。解析不了就当没有引用。"""
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            items = json.loads(raw)
        except ValueError:
            return []
    else:
        return []
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        if isinstance(it, dict):
            out.append(it)
        elif isinstance(it, str):
            out.append({"text": it})
    return out


def classify(question: str, answer: str, comment: str, refs: list[dict], rating: str) -> list[str]:
    """给一条样本打失败模式标签（down 才打，up 统一记 'up'）。

    纯规则：先把 comment 关键词映射到模式，再用**结构性证据**补一条
    （编造引用 / 无依据硬答），最后兜底 unspecified。
    """
    if rating != "down":
        return ["up"]

    tags: list[str] = []
    text = (comment or "").strip()
    for mode, words in _TAG_KEYWORDS:
        if any(w in text for w in words):
            tags.append(mode)

    # 结构性证据：引用编号超出 refs 范围 = 编造引用（和 comment 无关，一定成立）
    cited = {int(x) for x in _CITE_RE.findall(answer or "")}
    if cited and refs and max(cited) > len(refs):
        tags.append("fake_citation")

    # 有 refs 却完全不提依据 + 答案较长 = 硬凑（模型没用资料，或用了不标）
    if refs and not cited and len(answer or "") > 120 and not _has_refusal(answer):
        tags.append("retrieval_weak")

    # 没有 refs 却给出了具体断言且不示弱 = 该拒答没拒
    if not refs and len(answer or "") > 80 and not _has_refusal(answer):
        tags.append("should_refuse")

    # 去重保序
    seen: set[str] = set()
    ordered = [t for t in tags if not (t in seen or seen.add(t))]
    return ordered or ["unspecified"]


def _has_refusal(answer: str) -> bool:
    a = answer or ""
    return any(m in a for m in _REFUSAL_MARKERS)


def case_key_of(message_id: int, conversation_id: str) -> str:
    return hashlib.sha1(f"{message_id}|{conversation_id}".encode()).hexdigest()[:16]


def collect_cases(limit: int = 600) -> list[dict]:
    """从 feedbacks 拉样本（down + up），附上问题 / 回答 / 引用 / 失败模式标签。

    取"该 assistant 消息之前最近的一条 user 消息"当问题——不是按 id+1 猜，
    因为中间可能夹着 system 消息或多轮工具调用。
    """
    with store._get_conn() as conn:  # noqa: SLF001
        rows = conn.execute(
            "SELECT f.rating AS rating, f.comment AS comment, f.created_at AS fb_at, "
            "       m.id AS mid, m.content AS answer, m.refs AS refs, "
            "       m.conversation_id AS sid "
            "FROM feedbacks f "
            "JOIN messages m ON m.id = f.message_id AND m.conversation_id = f.conversation_id "
            "WHERE f.message_id IS NOT NULL AND m.role = 'assistant' "
            "ORDER BY f.id DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()

        cases: list[dict] = []
        for r in rows:
            q = conn.execute(
                "SELECT content FROM messages WHERE conversation_id = ? AND role = 'user' "
                "AND id < ? ORDER BY id DESC LIMIT 1",
                (r["sid"], r["mid"]),
            ).fetchone()
            question = (q["content"] if q else "") or ""
            if not question.strip():
                continue
            refs = _parse_refs(r["refs"])
            tags = classify(question, r["answer"], r["comment"], refs, r["rating"])
            cases.append(
                {
                    "case_key": case_key_of(int(r["mid"]), str(r["sid"])),
                    "question": _redact(question),
                    "gold": None,
                    "tags": tags,
                    "source": "down" if r["rating"] == "down" else "up",
                    "meta": {
                        "mid": int(r["mid"]),
                        "ref_count": len(refs),
                        "refs": [_redact(x.get("text", ""), 200) for x in refs][:8],
                        "fb_at": int(r["fb_at"] or 0),
                    },
                }
            )
    return cases


def case_counts() -> dict:
    """当前可用的样本量（决定要不要跑、以及 API 上的"攒料进度"）。"""
    with store._get_conn() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT SUM(CASE WHEN rating='down' THEN 1 ELSE 0 END) AS down_n, "
            "       SUM(CASE WHEN rating='up' THEN 1 ELSE 0 END) AS up_n "
            "FROM feedbacks WHERE message_id IS NOT NULL"
        ).fetchone()
    down_n = int(row["down_n"] or 0)
    up_n = int(row["up_n"] or 0)
    return {
        "down": down_n,
        "up": up_n,
        "total": down_n + up_n,
        "need": int(getattr(config, "PROMPT_EVO_REPLAY_MIN", 200) or 200),
    }


# ==================== 回放集（版本化、只增不改）====================


def _round_robin(groups: dict[str, list[dict]], n: int, rng: random.Random) -> list[dict]:
    """按组轮转取样本：每个失败模式都能被代表，不被量大的那一类吃满名额。"""
    keys = sorted(groups)
    for k in keys:
        rng.shuffle(groups[k])
    out: list[dict] = []
    idx = 0
    while len(out) < n and keys:
        alive = [k for k in keys if groups[k]]
        if not alive:
            break
        k = alive[idx % len(alive)]
        out.append(groups[k].pop())
        idx += 1
    return out


def _stratified_sample(cases: list[dict], size: int, seed: int, up_ratio: float) -> list[dict]:
    """分层抽样：down 按失败模式分组轮转，up 按比例混入（防"退化成什么都不答"）。"""
    rng = random.Random(int(seed))
    downs = [c for c in cases if c.get("source") == "down"]
    ups = [c for c in cases if c.get("source") == "up"]
    size = max(1, int(size))
    n_up_want = min(len(ups), max(1, int(round(size * float(up_ratio))))) if ups else 0
    n_down_want = max(0, size - n_up_want)

    groups: dict[str, list[dict]] = {}
    for c in downs:
        primary = (c.get("tags") or ["unspecified"])[0]
        groups.setdefault(primary, []).append(c)
    picked = _round_robin(groups, min(n_down_want, len(downs)), rng)
    if n_up_want:
        picked += _round_robin({"up": list(ups)}, n_up_want, rng)
    # 不足目标量时用剩下的样本补齐（组轮转已经尽量均衡，这里不重复）
    if len(picked) < size:
        used = {c["case_key"] for c in picked}
        rest = [c for c in cases if c["case_key"] not in used]
        rng.shuffle(rest)
        picked += rest[: size - len(picked)]
    rng.shuffle(picked)
    return picked[:size]


def latest_set(path: str) -> dict | None:
    with store._get_conn() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT path, version, seed, size, created_at, note FROM prompt_replay_sets "
            "WHERE path = ? ORDER BY version DESC LIMIT 1",
            (path,),
        ).fetchone()
    return dict(row) if row else None


def ensure_replay_set(path: str, cases: list[dict], target_size: int | None = None) -> dict:
    """保证该 path 有一份回放集；有新样本就升版本（老样本原样带过去）。

    为什么"只增不改"：指标的可比性全靠"两轮跑的是同一批题"。
    允许原地替换样本，就等于允许指标随手变好——那不叫进化，叫换考卷。
    """
    size_max = int(target_size or getattr(config, "PROMPT_EVO_REPLAY_MAX", 300) or 300)
    seed = int(getattr(config, "PROMPT_EVO_SEED", 20261020) or 0)
    up_ratio = float(getattr(config, "PROMPT_EVO_REPLAY_UP_RATIO", 0.3) or 0.3)
    cur = latest_set(path)

    known: set[str] = set()
    old_items: list[dict] = []
    if cur:
        old_items = load_replay_cases(path, int(cur["version"]))
        known = {c["case_key"] for c in old_items}

    fresh = [c for c in cases if c["case_key"] not in known and c.get("source") == "down"]
    if cur and (not fresh or int(cur["size"]) >= size_max):
        return cur

    room = size_max - len(old_items)
    add: list[dict] = []
    if room > 0 and fresh:
        add = _stratified_sample(fresh, min(room, len(fresh)), seed, up_ratio)
    if not cur and not add:
        # 一条 down 样本都没有（只有 up，或什么都没有）：先建空集，别让调用方拿到 None
        add = []

    new_items = old_items + add
    version = 1 if not cur else int(cur["version"]) + 1
    with store._get_conn() as conn:  # noqa: SLF001
        conn.execute(
            "INSERT INTO prompt_replay_sets (path, version, seed, size, created_at, note) "
            "VALUES (?,?,?,?,?,?)",
            (
                path,
                version,
                seed,
                len(new_items),
                int(time.time()),
                f"承接 v{cur['version']}，新增 {len(add)} 条" if cur else f"首版，新增 {len(add)} 条",
            ),
        )
        for c in new_items:
            conn.execute(
                "INSERT OR IGNORE INTO prompt_replay_items "
                "(path, set_version, case_key, question, gold, tags, source, meta_json, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    path,
                    version,
                    c["case_key"],
                    c["question"],
                    c.get("gold"),
                    ",".join(c.get("tags") or []),
                    c.get("source") or "down",
                    json.dumps(c.get("meta") or {}, ensure_ascii=False),
                    int(time.time()),
                ),
            )
    return {
        "path": path,
        "version": version,
        "seed": seed,
        "size": len(new_items),
        "created_at": int(time.time()),
        "note": f"新增 {len(add)} 条" if cur else "首版",
    }


def load_replay_cases(path: str, version: int, limit: int | None = None) -> list[dict]:
    sql = (
        "SELECT case_key, question, gold, tags, source, meta_json FROM prompt_replay_items "
        "WHERE path = ? AND set_version = ? ORDER BY case_key"
    )
    args: list[Any] = [path, int(version)]
    if limit:
        sql += " LIMIT ?"
        args.append(int(limit))
    with store._get_conn() as conn:  # noqa: SLF001
        rows = conn.execute(sql, args).fetchall()
    out = []
    for r in rows:
        try:
            meta = json.loads(r["meta_json"] or "{}")
        except ValueError:
            meta = {}
        out.append(
            {
                "case_key": r["case_key"],
                "question": r["question"],
                "gold": r["gold"],
                "tags": [t for t in (r["tags"] or "").split(",") if t],
                "source": r["source"],
                "meta": meta,
            }
        )
    return out


def list_sets(path: str, limit: int = 10) -> list[dict]:
    with store._get_conn() as conn:  # noqa: SLF001
        rows = conn.execute(
            "SELECT path, version, seed, size, created_at, note FROM prompt_replay_sets "
            "WHERE path = ? ORDER BY version DESC LIMIT ?",
            (path, max(1, min(int(limit), 50))),
        ).fetchall()
    return [dict(r) for r in rows]


# ==================== ③ 评估 ====================

JUDGE_PROMPT = """你是独立的答案质量评分员，只做判分，不回答问题。
请针对【问题】【参考资料片段数】【待评答案】给出 1-5 分：
  5 = 直接回答问题、论断都有资料支撑、无编造
  3 = 大致可用，但依据标注不全或略有发挥
  1 = 编造、答非所问、或该示弱时硬答
只输出 JSON：{"score": <1-5>, "reason": "<20 字以内>"}"""


def _refusal_ok(answer: str) -> bool:
    return _has_refusal(answer or "")


def heuristic_judge(case: dict, out: dict) -> dict:
    """确定性判分（零成本、可离线）。生产可换 `llm_judge`，口径一致。

    M1 引用忠实度：有引用就必须全部落在真实 refs 范围内；有资料却不标依据 = 0。
    M2 拒答正确率：标了 should_refuse 的样本必须示弱；没资料的样本不许硬答。
    M3 工具路由：只有 case 声明了 expect_tools 才参与判分，否则记 None（不参与统计）。
    M4 回答质量：5 项结构分，≥4 记通过。
    """
    answer = str(out.get("answer") or "")
    refs = (case.get("meta") or {}).get("refs") or []
    tags = case.get("tags") or []
    cited = {int(x) for x in _CITE_RE.findall(answer)}

    # M1
    if cited:
        m1 = 1.0 if refs and max(cited) <= len(refs) else 0.0
    else:
        m1 = 0.0 if refs else 1.0

    # M2
    if "should_refuse" in tags:
        m2 = 1.0 if _refusal_ok(answer) else 0.0
    elif refs and _refusal_ok(answer):
        # 有资料却直接拒答：过度拒答也算错
        m2 = 0.0
    else:
        m2 = 1.0

    # M3
    expect = (case.get("meta") or {}).get("expect_tools")
    if not expect:
        m3 = None
    else:
        called = set(out.get("tool_calls") or [])
        m3 = 1.0 if set(expect) <= called else 0.0

    # M4
    score = 0
    score += 1 if len(answer) >= 40 else 0
    score += 1 if 40 <= len(answer) <= 2000 else 0
    score += 1 if (not refs or (cited and refs and max(cited) <= len(refs))) else 0
    score += 0 if any(k in answer for k in ("Traceback", "Error:", "异常：")) else 1
    score += 1 if answer.strip().endswith(("。", "！", "？", "」", ".", "!", "?")) else 0
    m4 = 1.0 if score >= 4 else 0.0

    return {"M1": m1, "M2": m2, "M3": m3, "M4": m4, "quality_score": score}


def _json_obj(raw: str) -> dict:
    """从模型输出里抠出第一个 JSON 对象（容忍前后解释文字）。"""
    s = str(raw or "").strip()
    m = re.search(r"\{.*\}", s, re.S)
    if not m:
        raise ValueError("no json object")
    data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("json is not object")
    return data


def make_llm_judge(llm=None) -> Callable[[dict, dict], dict]:
    """LLM 判分器：**独立模板 + temperature=0**（防"优化判分器"）。

    结构分（M1/M3）仍由规则算——引用编号是否越界、工具是否调对，
    这些是客观的，交给 LLM 只会引入抖动。LLM 只负责 M4。
    """
    if llm is None:
        from core.llm import get_llm

        llm = get_llm(temperature=0.0, max_tokens=config.PROMPT_EVO_JUDGE_MAX_TOKENS)

    def judge(case: dict, out: dict) -> dict:
        base = heuristic_judge(case, out)
        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            raw = llm.invoke(
                [
                    SystemMessage(content=JUDGE_PROMPT),
                    HumanMessage(
                        content=json.dumps(
                            {
                                "问题": case.get("question", "")[:300],
                                "参考资料片段数": len((case.get("meta") or {}).get("refs") or []),
                                "待评答案": str(out.get("answer") or "")[:800],
                            },
                            ensure_ascii=False,
                        )
                    ),
                ]
            )
            text = getattr(raw, "content", raw)
            if isinstance(text, list):
                text = "".join(str(t) for t in text)
            score = float(_json_obj(str(text)).get("score", 0))
        except Exception:  # noqa: BLE001 —— 判分失败不该让整轮评估崩掉
            return base
        base["M4"] = 1.0 if score >= 3 else 0.0
        base["llm_score"] = score
        return base

    return judge


def make_llm_runner(path: str, llm=None) -> Callable[[dict, str], dict]:
    """默认执行器：用候选提示词 + 问题直接问一次模型（temperature=0）。

    **不含工具循环**：所以 M3 在默认执行器下永远记 None（不参与统计），
    这与"该指标需要真实工具环境"是相符的——不拿一个不含工具的跑分去证明工具路由变好了。
    真要评 M3，须注入带工具的 runner（测试或离线框架里做）。
    """
    if llm is None:
        from core.llm import get_llm

        llm = get_llm(temperature=0.0, max_tokens=1024)

    def run(case: dict, prompt_text: str) -> dict:
        from langchain_core.messages import HumanMessage, SystemMessage

        t0 = time.time()
        try:
            resp = llm.invoke(
                [
                    SystemMessage(content=prompt_text),
                    HumanMessage(content=case.get("question", "")),
                ]
            )
            text = getattr(resp, "content", resp)
            if isinstance(text, list):
                text = "".join(str(t) for t in text)
            answer = str(text)
            usage = getattr(resp, "usage_metadata", None) or {}
            tokens = int(usage.get("total_tokens") or 0) or (len(prompt_text) + len(answer)) // 2
            return {
                "answer": answer,
                "tool_calls": [],
                "tokens": tokens,
                "latency_ms": int((time.time() - t0) * 1000),
            }
        except Exception as e:  # noqa: BLE001 —— 单条失败记 0 分，整轮照跑
            return {
                "answer": "",
                "tool_calls": [],
                "tokens": 0,
                "latency_ms": int((time.time() - t0) * 1000),
                "error": f"{type(e).__name__}: {e}",
            }

    return run


def evaluate(
    path: str,
    prompt_text: str,
    cases: list[dict],
    *,
    runner: Callable | None = None,
    judge: Callable | None = None,
) -> dict:
    """在回放集上跑一遍，返回逐条判分 + summary。"""
    run = runner or make_llm_runner(path)
    jdg = judge or heuristic_judge
    t0 = time.time()
    per_case: list[dict] = []
    tokens_sum = 0
    for c in cases:
        try:
            out = run(c, prompt_text) or {}
        except Exception as e:  # noqa: BLE001
            out = {"answer": "", "tokens": 0, "latency_ms": 0, "error": f"{type(e).__name__}: {e}"}
        m = jdg(c, out)
        tokens_sum += int(out.get("tokens") or 0)
        per_case.append(
            {
                "case_key": c["case_key"],
                "M1": m.get("M1"),
                "M2": m.get("M2"),
                "M3": m.get("M3"),
                "M4": m.get("M4"),
                "tokens": int(out.get("tokens") or 0),
                "latency_ms": int(out.get("latency_ms") or 0),
                "error": out.get("error"),
            }
        )
    elapsed_ms = int((time.time() - t0) * 1000)
    return {
        "per_case": per_case,
        "summary": prompt_stats.summarize(per_case),
        "elapsed_ms": elapsed_ms,
        "tokens_out": tokens_sum,
    }


# ==================== ② 变异 ====================

# 失败模式 → 结构化变异模板。**无 LLM 也能跑通闭环**（确定性兜底）：
# LLM 只负责"想新点子"，护栏和落点由这里保证。模板全部是 1 行 add，天然不超过 6 行。
_HEURISTIC_MUTATIONS: dict[str, dict[str, dict]] = {
    "agent": {
        "retrieval_weak": {
            "anchor": "  9. 闲聊、身份、常识等无需工具的问题直接回答。",
            "new_text": "  10. 引用资料时只回答被片段真正支撑的部分；没有被支撑的部分"
            "直接说明资料里没有，不要用常识补全后再标一个依据。",
            "rationale": "差样本里检索弱硬凑占多数：补一条「只答被支撑部分」的边界约束",
        },
        "should_refuse": {
            "anchor": "  9. 闲聊、身份、常识等无需工具的问题直接回答。",
            "new_text": "  10. 检索结果不足以支撑结论时，先说明「资料里没有直接答案」，"
            "再给出可行的下一步；不要为了显得有用而给出确定结论。",
            "rationale": "该拒答没拒：把「示弱」从建议提升为行为准则",
        },
        "fake_citation": {
            "anchor": "  9. 闲聊、身份、常识等无需工具的问题直接回答。",
            "new_text": "  10. 依据编号只能来自本轮工具返回的片段；没检索到就不要凭记忆编造编号。",
            "rationale": "编造引用：明确编号来源只能是工具返回",
        },
        "tool_misroute": {
            "anchor": "  4. 天气查询优先用 get_weather，支持中文或拼音输入（如 北京 / beijing）。",
            "new_text": "  10. 需要精确数值（算术、换算、日期差）先调 calculator，"
            "需要最新信息先调 web_search；不要凭记忆给结论。",
            "rationale": "工具选错：把「先调工具」写成可执行的判断条件",
        },
        "format": {
            "anchor": "  9. 闲聊、身份、常识等无需工具的问题直接回答。",
            "new_text": "  10. 先一句话给结论，再分点展开；长度与问题复杂度匹配，不写无信息量的铺垫。",
            "rationale": "格式/啰嗦类差评：约束结论先行与长度",
        },
    },
    "rag_note_low": {
        "should_refuse": {
            "anchor": "1. 第一句就要点明",
            "new_text": "4. 即使被追问，也不要改口成确定结论：明确说「这个知识库里确实查不到」，"
            "再给出补资料或联网搜索的建议。",
            "rationale": "被追问就软化为确定结论：补一条「不改口」约束",
        },
        "retrieval_weak": {
            "anchor": "1. 第一句就要点明",
            "new_text": "4. 只转述与问题直接对应的那一两句；不要顺着相邻内容扩写成答案。",
            "rationale": "相邻内容扩写导致硬凑：收紧转述范围",
        },
    },
    "rag_note_medium": {
        "should_refuse": {
            "anchor": "3. 建议用户补充更对口的文档，或改用联网搜索。",
            "new_text": "4. 用户追问结论时，先重复「只找到沾边内容」，再说清还缺哪类资料才能回答。",
            "rationale": "灰色地带被追问后变成肯定回答：先复述边界再谈缺口",
        },
        "retrieval_weak": {
            "anchor": "3. 建议用户补充更对口的文档，或改用联网搜索。",
            "new_text": "4. 转述沾边内容时标明它对应问题的哪一部分，没对应的部分不要连带作答。",
            "rationale": "沾边内容被整体套用：要求逐部分对齐",
        },
    },
}

# 没有明确失败模式时的兜底（用最常踩的"硬凑"模板）
_FALLBACK_MODE = {"agent": "retrieval_weak", "rag_note_low": "retrieval_weak",
                  "rag_note_medium": "retrieval_weak"}


def _top_modes(cases: list[dict], limit: int = 3) -> list[str]:
    """按出现次数排序的失败模式（只统计 down 样本）。"""
    counts: dict[str, int] = {}
    for c in cases:
        if c.get("source") != "down":
            continue
        for tag in c.get("tags") or []:
            if tag in FAILURE_MODES:
                counts[tag] = counts.get(tag, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [m for m, _ in ordered[:limit]]


def propose_diffs(
    path: str,
    base_text: str,
    cases: list[dict],
    *,
    llm=None,
    max_candidates: int | None = None,
) -> list[dict]:
    """产出候选变异（已过结构护栏，返回的是能直接落地的 ops）。

    顺序：先试 LLM（有的话）→ 再补确定性模板 → 去重。LLM 产出被护栏拒的
    直接丢弃并记日志，**不降级成"半截 diff"**。
    """
    limit = int(max_candidates or getattr(config, "PROMPT_EVO_CANDIDATES", 2) or 2)
    out: list[dict] = []
    seen: set[str] = set()

    if llm is not None:
        try:
            out.extend(_llm_propose(path, base_text, cases, llm))
        except Exception as e:  # noqa: BLE001
            _log.warning("prompt_evo.llm_mutate_failed path=%s err=%s", path, e)

    modes = _top_modes(cases) or [_FALLBACK_MODE.get(path, "retrieval_weak")]
    table = _HEURISTIC_MUTATIONS.get(path, {})
    for mode in modes:
        tpl = table.get(mode)
        if not tpl:
            continue
        ops = [
            {
                "action": "add",
                "anchor": tpl["anchor"],
                "new_text": tpl["new_text"],
                "rationale": tpl["rationale"],
                "mode": mode,
            }
        ]
        ok, res = prompt_guard.guard_ok(path, base_text, ops)
        if not ok:
            _log.info("prompt_evo.heuristic_rejected path=%s mode=%s why=%s", path, mode, res)
            continue
        h = hashlib.sha1(res["text"].encode()).hexdigest()
        if h in seen:
            continue
        seen.add(h)
        out.append({"ops": ops, "mode": mode, "text": res["text"], "changed_lines": res["changed_lines"]})
        if len(out) >= limit:
            break
    return out[:limit]


def _llm_propose(path: str, base_text: str, cases: list[dict], llm) -> list[dict]:
    """让模型给结构化 diff。输出必须能被 `prompt_guard.parse_llm_output` 解析。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    from core import prompt_guard as pg  # 局部引用，避免循环

    samples = []
    for c in [x for x in cases if x.get("source") == "down"][:6]:
        samples.append(
            {
                "question": c.get("question", "")[:200],
                "失败模式": c.get("tags"),
                "引用片段数": (c.get("meta") or {}).get("ref_count", 0),
            }
        )
    sys = (
        "你是提示词工程师。只输出 JSON：{\"ops\":[{...}]}。"
        "允许的 action：add / modify / remove。add 需要 anchor 与 new_text，"
        "modify/remove 需要 old_text（必须与原文逐字一致且唯一）。"
        "禁止触碰工具定义、系统角色首句、kb_id 相关表述与安全条目。"
        f"总改动行数不得超过 {pg.max_lines()} 行。"
    )
    human = json.dumps(
        {
            "当前提示词": base_text,
            "差样本摘要（已脱敏，不含用户原文评论）": samples,
            "要求": "针对最高频的失败模式，给出最小改动；rationale 说明依据哪类样本。",
        },
        ensure_ascii=False,
    )
    raw = llm.invoke([SystemMessage(content=sys), HumanMessage(content=human)])
    text = getattr(raw, "content", raw)
    if isinstance(text, list):
        text = "".join(str(t) for t in text)
    ops = pg.parse_llm_output(str(text))
    ok, res = pg.guard_ok(path, base_text, ops)
    if not ok:
        _log.info("prompt_evo.llm_rejected path=%s why=%s", path, res)
        return []
    return [{"ops": ops, "mode": "llm", "text": res["text"], "changed_lines": res["changed_lines"]}]


# ==================== ④ 择优（帕累托前沿）====================

_MAXIMIZE = ("M1", "M2", "M3", "M4")


def _metrics_of(cand: dict) -> dict:
    return {m: float(cand.get("summary", {}).get(m) or 0.0) for m in _MAXIMIZE}


def _dominates(a: dict, b: dict) -> bool:
    """a 支配 b：四个质量指标都不差，且至少一项更好（M1–M4 都是越大越好）。"""
    ma, mb = _metrics_of(a), _metrics_of(b)
    not_worse = all(ma[m] >= mb[m] for m in _MAXIMIZE)
    better = any(ma[m] > mb[m] for m in _MAXIMIZE)
    return not_worse and better


def pareto_frontier(cands: list[dict]) -> list[dict]:
    front = []
    for c in cands:
        if not any(_dominates(o, c) for o in cands if o is not c):
            front.append(c)
    return front


def select(cands: list[dict]) -> tuple[dict | None, list[dict], str]:
    """过闸门 → 取前沿 → tie-break（最小 diff 优先，其次质量总分）。

    「单项大涨、其余全跌」的候选在闸门阶段就已被拒（四个指标的下界都要 ≥ -5pp），
    所以前沿里的候选都是"没有明显劣化"的，tie-break 只需在它们之间挑回归面最小的那个。
    """
    passed = [c for c in cands if (c.get("gate") or {}).get("status") == prompt_stats.STATUS_PASS]
    if not passed:
        return None, [], "没有候选通过闸门（CI 下界或成本/延迟护栏未达标）"
    front = pareto_frontier(passed)
    front.sort(
        key=lambda c: (
            int(c.get("changed_lines") or 99),
            -sum(_metrics_of(c).values()),
        )
    )
    return front[0], front, ""


# ==================== ⑥ 一轮闭环 ====================


def run_evolution(
    path: str,
    *,
    llm=None,
    judge: Callable | None = None,
    runner: Callable | None = None,
    planned_at: int | None = None,
    dry_run: bool = False,
) -> dict:
    """跑一轮：采集 → 回放集 → 评估基线 → 变异 → 评估候选 → 闸门 → 择优落库。

    任何"数据不够"的分支都在**花钱之前**返回（不调 LLM）。
    """
    now = int(planned_at or time.time())
    if not getattr(config, "PROMPT_EVO_ENABLED", True):
        return {"status": "disabled", "path": path}
    if path not in known_paths():
        return {"status": "unknown_path", "path": path, "paths": list(known_paths())}

    counts = case_counts()
    need = int(getattr(config, "PROMPT_EVO_REPLAY_MIN", 200) or 200)
    if counts["total"] < need:
        return {
            "status": "insufficient_data",
            "path": path,
            "cases": counts,
            "reason": f"回放样本 {counts['total']} < {need}（需 200~300 例才做统计判定）",
        }

    cases = collect_cases()
    if not cases:
        return {"status": "insufficient_data", "path": path, "cases": counts}

    rset = ensure_replay_set(path, cases)
    replay = load_replay_cases(path, int(rset["version"]))
    cap = int(getattr(config, "PROMPT_EVO_RUN_MAX_CASES", 200) or 0)
    if cap > 0:
        replay = replay[:cap]
    if len(replay) < need:
        return {
            "status": "insufficient_data",
            "path": path,
            "cases": {"replay": len(replay), "need": need},
            "reason": "回放集条目不足（样本以 up 为主或去重后偏少）",
        }

    base_text, base_version = prompt_store.resolve(path, default_prompt(path))
    active = prompt_store.active_variant(path)
    base_variant_id = int(active["id"]) if active else 0

    # 基线评估：同一回放集版本已有就复用（省一半 judge + 生成成本）
    cached = prompt_store.latest_eval(path, base_variant_id, int(rset["version"]))
    if cached and len(cached.get("per_case") or []) >= len(replay):
        base_per_case = cached["per_case"][: len(replay)]
        base_summary = cached["summary"]
        base_source = "cached"
    else:
        base_ev = evaluate(path, base_text, replay, runner=runner, judge=judge)
        base_per_case = base_ev["per_case"]
        base_summary = base_ev["summary"]
        base_source = "fresh"
        if not dry_run:
            prompt_store.record_eval(
                path, base_variant_id, int(rset["version"]), base_per_case, base_summary,
                base_ev["elapsed_ms"],
            )

    props = propose_diffs(path, base_text, cases, llm=llm)
    if not props:
        return {
            "status": "no_candidate",
            "path": path,
            "replay_set": rset,
            "base": {"version": base_version, "summary": base_summary, "eval": base_source},
            "reason": "没有通过结构护栏的候选（模板落点对不上当前基线或全部越界）",
        }

    jdg = judge or (
        make_llm_judge()
        if str(getattr(config, "PROMPT_EVO_JUDGE", "heuristic")).lower() == "llm"
        else heuristic_judge
    )

    results: list[dict] = []
    tokens_out = 0
    for prop in props[: int(getattr(config, "PROMPT_EVO_CANDIDATES", 2) or 2)]:
        if dry_run:
            results.append(
                {
                    "id": None,
                    "ops": prop["ops"],
                    "mode": prop.get("mode"),
                    "changed_lines": prop["changed_lines"],
                    "summary": {},
                    "gate": {"status": "dry_run"},
                }
            )
            continue
        vid = prompt_store.create_variant(
            path,
            prop["text"],
            parent_id=base_variant_id or None,
            diff=prop["ops"],
            created_by=f"evolve:{JOB_KEY}@{now}",
        )
        ev = evaluate(path, prop["text"], replay, runner=runner, judge=jdg)
        gate = prompt_stats.gate_decision(
            base_per_case,
            ev["per_case"],
            min_n=need,
            alpha=float(getattr(config, "PROMPT_EVO_CI_ALPHA", 0.05)),
            max_drop=float(getattr(config, "PROMPT_EVO_MAX_DROP", 0.05)),
            n_boot=int(getattr(config, "PROMPT_EVO_BOOTSTRAP", 2000)),
            max_cost_ratio=float(getattr(config, "PROMPT_EVO_MAX_COST_RATIO", 1.15)),
            max_latency_ratio=float(getattr(config, "PROMPT_EVO_MAX_LATENCY_RATIO", 1.20)),
            seed=int(getattr(config, "PROMPT_EVO_SEED", 0)),
        )
        summary = prompt_stats.summarize(ev["per_case"], gate=gate)
        tokens_out += int(ev.get("tokens_out") or 0)
        prompt_store.record_eval(path, vid, int(rset["version"]), ev["per_case"], summary,
                                 ev["elapsed_ms"])
        prompt_store.set_metrics(
            vid,
            {
                "summary": summary,
                "gate": gate,
                "diff_lines": prop["changed_lines"],
                "mode": prop.get("mode"),
            },
        )
        if gate["status"] == prompt_stats.STATUS_PASS:
            prompt_store.set_status(vid, prompt_store.STATUS_SHADOW)
        else:
            prompt_store.set_status(vid, prompt_store.STATUS_REJECTED)
        results.append(
            {
                "id": vid,
                "mode": prop.get("mode"),
                "ops": prop["ops"],
                "changed_lines": prop["changed_lines"],
                "summary": summary,
                "gate": gate,
            }
        )

    best, front, why = select(results)
    promoted = None
    if best and best.get("id") and not dry_run:
        prompt_store.set_status(int(best["id"]), prompt_store.STATUS_SHADOW)
        if getattr(config, "PROMPT_EVO_AUTO_PROMOTE", False):
            promoted = prompt_store.promote(int(best["id"]), by="auto")

    return {
        "status": "ok" if best else "no_winner",
        "path": path,
        "replay_set": rset,
        "base": {"version": base_version, "summary": base_summary, "eval": base_source},
        "candidates": results,
        "frontier": [c.get("id") for c in front],
        "best": best.get("id") if best else None,
        "reason": why,
        "auto_promoted": promoted,
        "tokens_out": tokens_out,
    }


# ==================== worker hooks ====================


def due_subjects(job_key: str, planned_at: int) -> list[str]:
    """本轮该跑哪个 path。

    一次只返回**一个** path：一轮 = 一条 path × 200 例 × (基线 + 候选) 次生成，
    三条 path 全跑会让单轮的 LLM 成本翻三倍，还会把 worker 卡在一个 tick 里太久。
    选"最久没评估过"的那条，轮转推进（no-data 的 path 自然会被跳过）。
    """
    if job_key != JOB_KEY:
        return []
    if not getattr(config, "PROMPT_EVO_ENABLED", True):
        return []
    counts = case_counts()
    if counts["total"] < int(getattr(config, "PROMPT_EVO_REPLAY_MIN", 200) or 200):
        return []
    paths = [p for p in known_paths()]
    if not paths:
        return []

    def last_eval_at(p: str) -> int:
        with store._get_conn() as conn:  # noqa: SLF001
            row = conn.execute(
                "SELECT MAX(created_at) AS ts FROM prompt_evals WHERE path = ?", (p,)
            ).fetchone()
        return int(row["ts"] or 0) if row else 0

    paths.sort(key=last_eval_at)
    return [paths[0]]


def run_one(job_key: str, subject_id: str, planned_at: int) -> dict:
    """worker 的执行入口（幂等 / 重试 / 心跳都由 worker 负责，这里只干活）。"""
    if job_key != JOB_KEY:
        return {}
    rep = run_evolution(subject_id, planned_at=int(planned_at))
    return {"tokens_out": int(rep.get("tokens_out") or 0), "status": rep.get("status")}


def status() -> dict:
    """给 API 的一页概览：样本进度 / 各 path 版本 / 最近评估。"""
    counts = case_counts()
    paths = []
    for p in known_paths():
        active = prompt_store.active_variant(p)
        sets = list_sets(p, limit=1)
        evals = prompt_store.list_evals(p, limit=3)
        paths.append(
            {
                "path": p,
                "active_version": int(active["version"]) if active else 0,
                "prompt_chars": len(active["prompt_text"]) if active else len(default_prompt(p)),
                "replay_set": sets[0] if sets else None,
                "recent_evals": evals,
            }
        )
    return {
        "enabled": bool(getattr(config, "PROMPT_EVO_ENABLED", True)),
        "auto_promote": bool(getattr(config, "PROMPT_EVO_AUTO_PROMOTE", False)),
        "judge": str(getattr(config, "PROMPT_EVO_JUDGE", "heuristic")),
        "gate": {
            "replay_min": int(getattr(config, "PROMPT_EVO_REPLAY_MIN", 200)),
            "replay_max": int(getattr(config, "PROMPT_EVO_REPLAY_MAX", 300)),
            "ci_alpha": float(getattr(config, "PROMPT_EVO_CI_ALPHA", 0.05)),
            "max_drop_pp": float(getattr(config, "PROMPT_EVO_MAX_DROP", 0.05)) * 100,
            "max_cost_ratio": float(getattr(config, "PROMPT_EVO_MAX_COST_RATIO", 1.15)),
            "max_latency_ratio": float(getattr(config, "PROMPT_EVO_MAX_LATENCY_RATIO", 1.20)),
        },
        "cases": counts,
        "paths": paths,
    }
