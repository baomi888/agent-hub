"""一句话 → 待办/提醒 proposal（Phase B）。

## 这个模块只做三件事

1. 拼 prompt（当前时间、星期、时区给死，不指望模型自己算「周五是哪天」）；
2. 调模型，把返回的 JSON 抠出来（带一次轻量修复）；
3. 用 Pydantic 把模型的自由发挥收紧成前端能直接渲染的形状。

**它一个字节都不写库。** tasks / reminders / reminder_fires 全都不碰 ——
用户勾完点「加入 N 条」之后，由前端调现有 `POST /api/tasks` / `POST /api/reminders` 写。

## 三条与"模型一定会犯错"直接相关的设计

1. **时间不收 Unix 秒，收本地时刻串。** 规格里响应字段是 Unix 秒，但让模型做
   epoch 换算十次里错三次（时区、夏令时、`明天` 的边界），而错的时间用户还得逐条改，
   等于白整理。所以 prompt 里要 `YYYY-MM-DD HH:MM`，服务端按 `timeutil.TZ` 换算成秒。
   响应形状不变，还是 Unix 秒。
2. **单条坏掉不拖垮整批。** 逐条校验，坏的进 `unresolved` 并附一句原因，
   其余照常返回 —— 用户看到 5 条对的 + 1 句「这条没看懂」，比看到 502 好得多。
3. **offset 由服务端算。** 不让模型给 `source_start/end`：它给的下标经常差一两个字符，
   前端拿去做高亮就会切错。这里用 `text.find(source_text, cursor)` 算，
   cursor 只往后走，保证多条 `source_text` 相同时也不出现倒序区间。
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ValidationError, field_validator

from core import llm_gate, timeutil
from core.llm import get_llm

# ---------------------------------------------------------------- 硬边界
MAX_ITEMS = 12
MAX_TEXT = 2000
TITLE_MAX = 60
LIST_KEYS = ("inbox", "course", "homework", "club", "life", "exam")
FREQS = ("once", "daily", "weekdays", "weekly", "interval")

# 合理时间窗：一年前到五年后。超出这个范围的多半是模型把 2027 写成 2017。
_TS_PAST = 366 * 86400
_TS_FUTURE = 5 * 366 * 86400

_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


class ParseFailed(Exception):
    """模型没给出能用的 JSON。路由把它转成 502，不要静默返回空数组。"""


# ---------------------------------------------------------------- 模型的原始输出
class RawRecurrence(BaseModel):
    freq: str = "daily"
    at: str | None = None
    weekday: int | None = None
    every_minutes: int | None = None
    fire_at: str | None = None

    @field_validator("freq")
    @classmethod
    def _freq_ok(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in FREQS:
            raise ValueError(f"freq 只能是 {'/'.join(FREQS)}")
        return v

    @field_validator("at")
    @classmethod
    def _at_ok(cls, v: str | None) -> str | None:
        if v is None:
            return None
        m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", v)
        if not m:
            raise ValueError("at 要写成 HH:MM")
        h, mi = int(m.group(1)), int(m.group(2))
        if not (0 <= h <= 23 and 0 <= mi <= 59):
            raise ValueError("at 不是合法时刻")
        return f"{h:02d}:{mi:02d}"

    @field_validator("weekday")
    @classmethod
    def _wd_ok(cls, v: int | None) -> int | None:
        if v is None:
            return None
        if not (0 <= int(v) <= 6):
            raise ValueError("weekday 只能是 0–6")
        return int(v)

    @field_validator("every_minutes")
    @classmethod
    def _every_ok(cls, v: int | None) -> int | None:
        if v is None:
            return None
        if not (60 <= int(v) <= 1440):
            raise ValueError("every_minutes 只能是 60–1440")
        return int(v)


class RawItem(BaseModel):
    kind: Literal["task", "reminder"]
    title: str
    note: str | None = None
    list_key: str | None = None
    priority: int = 0
    due_at: Any = None
    remind_at: Any = None
    recurrence: RawRecurrence | None = None
    source_text: str
    needs_confirm: bool = False
    # 故意不写死形状：真模型会把选项摊平，硬校验会把整条待办判死（见 _questions）
    questions: list[Any] = []

    @field_validator("title")
    @classmethod
    def _title_ok(cls, v: str) -> str:
        v = " ".join((v or "").split())
        if not v:
            raise ValueError("标题为空")
        # 截断而不是丢弃：条目还在，用户下一步就能改标题；丢掉就变成"我明明说了却没有"
        return v[:TITLE_MAX]

    @field_validator("source_text")
    @classmethod
    def _src_ok(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("没引用原话")
        return v

    @field_validator("list_key")
    @classmethod
    def _list_ok(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().lower()
        if v not in LIST_KEYS:
            # 归错清单是可改的小错，不值得为此丢一条
            return "inbox"
        return v

    @field_validator("priority", mode="before")
    @classmethod
    def _prio_ok(cls, v: Any) -> int:
        try:
            return max(0, min(2, int(v)))
        except (TypeError, ValueError):
            return 0


class RawOut(BaseModel):
    items: list[RawItem] = []
    unresolved: list[str] = []


# ---------------------------------------------------------------- 时间换算
def _to_ts(value: Any, now: int) -> int | None:
    """把模型给的时间换成 Unix 秒。给了但换算不出来 → None（宁可让用户补，也别瞎猜）。"""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        ts = int(value)
        if ts > 10**11:  # 毫秒
            ts //= 1000
    elif isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if re.fullmatch(r"\d{9,13}", s):
            ts = int(s)
            if ts > 10**11:
                ts //= 1000
        else:
            today = datetime.fromtimestamp(now, timeutil.TZ)
            ts = -1
            for fmt, fill in (("%Y-%m-%d %H:%M", None), ("%Y-%m-%d %H:%M:%S", None),
                              ("%Y-%m-%d", (0, 0)), ("%m-%d %H:%M", None), ("%H:%M", None)):
                try:
                    d = datetime.strptime(s, fmt)
                except ValueError:
                    continue
                if fill is not None:
                    d = d.replace(hour=fill[0], minute=fill[1])
                if "%Y" not in fmt:
                    d = d.replace(year=today.year)
                if "%m" not in fmt:
                    d = d.replace(month=today.month, day=today.day)
                ts = int(d.replace(tzinfo=timeutil.TZ).timestamp())
                # 只给了时刻、那个点今天已经过了 → 用户说的多半是明天
                if "%Y" not in fmt and "%m" not in fmt and ts <= now:
                    ts += 86400
                break
            if ts < 0:
                return None
    else:
        return None

    if not (now - _TS_PAST <= ts <= now + _TS_FUTURE):
        return None
    return ts


def _now_human(now: int) -> str:
    d = datetime.fromtimestamp(now, timeutil.TZ)
    return (
        f"{d.strftime('%Y-%m-%d %H:%M')}（{_WEEKDAYS[d.weekday()]}），"
        f"时区 {timeutil.TZ_NAME}"
    )


SYSTEM_PROMPT = """你在把用户随口说的一段话整理成待办和提醒。只输出 JSON，不解释，不加 markdown 代码块。

硬规则：
1. 每条必须引用原文里一段**连续原文**，放在 source_text 里。一个字都不许改写原话。
2. 时间写成 "YYYY-MM-DD HH:MM"（本地时间）。只有确实没提时间、或没法确定时才写 null。
   绝不要自己编一个时间凑数。
3. 用户说"明天/周五/下周/下午三点"时，按我给的出现时间和星期几推算。
4. 只有明确说了"提醒我 / 叫我 / 记得"才填 remind_at。不要把 due_at 抄成 remind_at。
5. "每天/每周/工作日/每小时"这类周期放进 recurrence，不要塞进 due_at。
6. **"task" 是默认值**：凡是"做完能打勾"的事一律用它 —— 交作业、写论文、报名、取快递、
   起床、吃早饭、买菜、收拾房间、给谁回电话。
7. 只有"到点被通知"本身就是全部意义的，才用 "reminder" —— 生日、吃药、开会开始、
   班车发车、成绩公布、截止时刻到了提醒一声。
   判据只有一句：**说完之后用户还需要"去做"一件具体的事吗？**
   需要 → task；只是"知道一下、别错过" → reminder。
   特别注意："早上八点起床""十点吃早饭"这类**看起来是时间点、其实是待办**的，
   必须给 task。上一版的规则把它们全判成了 reminder，用户看到满屏"只提醒"，没法打勾。
8. 带"每天/每周/工作日/每小时"这类周期的**必须**用 "reminder"（待办暂不支持重复）。
9. 一句话不要拆成几个同义条目，也不要把两件事合成一条。
10. 时间不确定时：把对应字段写 null，needs_confirm 为 true，并在 questions 里问一个问题，
   每个问题给两个选项（label 是给用户看的中文，value 是 once/weekly 这类机器值）。
11. 最多 12 条。多出来的一律不要，放进 unresolved 用一句话说明。
12. 什么都不像的句子（口头语、废话）不要变成条目，放进 unresolved。

输出结构：
{"items": [{"kind": "task|reminder", "title": "...", "note": null,
  "list_key": "inbox|course|homework|club|life|exam 或 null",
  "priority": 0, "due_at": "YYYY-MM-DD HH:MM 或 null",
  "remind_at": "YYYY-MM-DD HH:MM 或 null",
  "recurrence": {"freq": "once|daily|weekdays|weekly|interval", "at": "HH:MM 或 null",
                 "weekday": 0-6 或 null, "every_minutes": 60-1440 或 null,
                 "fire_at": "YYYY-MM-DD HH:MM 或 null"} 或 null,
  "source_text": "原文连续片段", "needs_confirm": false,
  "questions": [{"text": "要问用户的那句话", "options": [
      {"label": "给用户看的中文", "value": "once"},
      {"label": "给用户看的中文", "value": "weekly"}]}]}],
 "unresolved": ["..."]}

注意 questions 的形状：每个问题是一个对象，**问题本身在 text 里**，两个选项放在
options 数组里。不要把选项直接摊在问题对象上。

task 的 recurrence 必须是 null。list_key 只有 task 有值。"""


def _extract_json(raw: str) -> dict[str, Any]:
    """从模型输出里抠出 JSON。先轻修一次，修不出来就 ParseFailed。

    只做两件事：去 ```json 围栏、取第一个 `{` 到最后一个 `}`。
    不做"猜测式补括号"——补出来的 JSON 往往字段名也是错的，那比重试更危险。
    """
    s = (raw or "").strip()
    if not s:
        raise ParseFailed("模型返回了空内容")
    s = re.sub(r"^```(?:json|JSON)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        raise ParseFailed("模型返回的不是 JSON")
    try:
        data = json.loads(s[i : j + 1])
    except json.JSONDecodeError as exc:
        raise ParseFailed(f"模型返回的 JSON 解析不了：{exc.msg}") from exc
    if not isinstance(data, dict):
        raise ParseFailed("模型返回的 JSON 顶层不是对象")
    return data


def _questions(raw: Any) -> list[dict]:
    """把模型给的 questions 归一化。问题不像话就整条丢掉，**绝不编**。

    真模型实测（2026-11-10）会把选项摊平在问题对象上：
        [{"label": "按当天结束算？", "value": "end_of_day"}, ...]
    而不是 {"text": ..., "options": [...]}。这是 prompt 没说清造成的，
    但**容错不能靠"猜回问题原文"** —— 编一句话出来让用户点头，比不问更糟。
    所以：认得出 text 的才留，认不出就丢；`needs_confirm` 随之落回 false，
    用户照常能加入、加入后再改。
    """
    out: list[dict] = []
    for q in raw or []:
        if isinstance(q, str):
            continue  # 只有一句问话、没有选项 = 用户答不了
        if not isinstance(q, dict):
            continue
        text = str(q.get("text") or q.get("question") or q.get("q") or "").strip()
        opts_raw = q.get("options") or q.get("choices") or []
        opts = []
        for o in opts_raw if isinstance(opts_raw, list) else []:
            if not isinstance(o, dict):
                continue
            label = str(o.get("label") or o.get("text") or "").strip()
            value = str(o.get("value") or "").strip()
            if label and value:
                opts.append({"label": label, "value": value})
        if not text or len(opts) < 2:
            continue
        out.append({"id": str(q.get("id") or f"q{len(out) + 1}"), "text": text[:80], "options": opts[:4]})
    return out


def _to_proposal(item: RawItem, text: str, now: int, cursor: int) -> tuple[dict, int]:
    """把一条原始条目收紧成前端契约。返回 (proposal, 新的 cursor)。"""
    kind = item.kind
    if kind == "task" and item.recurrence is not None:
        # 规格：task 的 recurrence 必须为 null（库里的 Task.recurrence 只允许 "none"，
        # 见 `task_core._recurrence`）。但直接丢掉会让「每天背单词」变成一条没有
        # 时间的待办 —— 用户原话里的"每天"凭空消失，卡片上还会写「缺少时间」，
        # 等于怪用户没说清。这套类型里只有 reminder 能承载"每天"，所以改判成 reminder。
        #
        # 提示词第 8 条已经要求周期类直接给 reminder，这里是**兜底**：
        # 模型偶尔仍会输出 task + recurrence，两种处理方式都不能丢信息。
        kind = "reminder"
    due_at = _to_ts(item.due_at, now)
    remind_at = _to_ts(item.remind_at, now)

    recurrence: dict | None = None
    if kind == "reminder" and item.recurrence is not None:
        rec = item.recurrence
        fire_at = _to_ts(rec.fire_at, now)
        # fire_at 只对 once 有意义；别的 freq 留着它，worker 会当成一次性
        if rec.freq != "once":
            fire_at = None
        recurrence = {
            "freq": rec.freq,
            "at": rec.at,
            "weekday": rec.weekday,
            "every_minutes": rec.every_minutes,
            "fire_at": fire_at,
        }
        if rec.freq == "once" and fire_at is None:
            # once 却没给出具体时刻 → 落回 remind_at，别让条目变成哑弹
            fire_at = remind_at
            recurrence["fire_at"] = fire_at
    questions = _questions(item.questions)
    # 说"要确认"却一个能答的问题都没给 = 把用户卡在原地，那就别装
    needs_confirm = bool(item.needs_confirm) and bool(questions)

    # reminder 两样时间都没有，等于一条不会响的提醒 —— 必须问
    if kind == "reminder" and recurrence is None and remind_at is None and due_at is None:
        needs_confirm = True
        if not questions:
            questions = [
                {
                    "id": "q1",
                    "text": "这条什么时候提醒你？",
                    "options": [
                        {"label": "今天晚些时候", "value": "today"},
                        {"label": "先不设时间", "value": "__later"},
                    ],
                }
            ]

    src = item.source_text
    start = text.find(src, cursor)
    if start < 0:
        # 给同一段原话算过的 cursor 之后找不到，再从头找一次
        start = text.find(src)
    if start >= 0:
        end = start + len(src)
        if start >= cursor:
            cursor = end
    else:
        start, end = None, None

    proposal = {
        "kind": kind,
        "title": item.title,
        "note": (item.note or None) and item.note[:500].strip(),
        "list_key": (item.list_key or "inbox") if kind == "task" else None,
        # 图标不由模型猜：它没有"这条该长什么样"的可靠依据，猜错比留空更糟
        # （留空时列表会按清单/标题兜底，效果更稳）。这一格留给用户在确认屏上选。
        "icon": None,
        "priority": item.priority,
        "due_at": due_at,
        "remind_at": remind_at,
        "recurrence": recurrence,
        "source_text": src,
        "source_start": start,
        "source_end": end,
        "needs_confirm": needs_confirm,
        "questions": questions,
    }
    return proposal, cursor


def tighten(
    data: dict[str, Any], text: str, now: int
) -> tuple[list[dict], list[str]]:
    """把模型 JSON 收紧成 (items, unresolved)。逐条校验，坏的进 unresolved。

    单独拆出来是为了能脱模型测试：给一段假 JSON 就能验全部校验规则，
    不用 mock 网络。
    """
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        raise ParseFailed("模型返回的 JSON 里没有 items 数组")

    unresolved: list[str] = []
    for extra in data.get("unresolved") or []:
        if isinstance(extra, str) and extra.strip():
            unresolved.append(extra.strip()[:80])

    items: list[dict] = []
    cursor = 0
    for idx, raw in enumerate(raw_items):
        if len(items) >= MAX_ITEMS:
            unresolved.append(f"超过 {MAX_ITEMS} 条，后面的没采纳")
            break
        if not isinstance(raw, dict):
            unresolved.append("有一条格式不对，跳过了")
            continue
        try:
            item = RawItem.model_validate(raw)
        except ValidationError as exc:
            # 只把第一条原因写出来：给用户看的东西不该是一屏技术栈
            first = exc.errors()[0]
            field = ".".join(str(p) for p in first.get("loc", ())) or "字段"
            hint = str(first.get("msg", "")).replace("Value error, ", "")
            label = str(raw.get("title") or raw.get("source_text") or "其中一条")[:20]
            unresolved.append(f"「{label}」没整理出来（{field}：{hint}）")
            continue
        proposal, cursor = _to_proposal(item, text, now, cursor)
        items.append({"id": f"p{idx + 1}", **proposal})
    return items, unresolved


async def parse(text: str, now: int) -> dict[str, Any]:
    """调模型 + 收紧。失败抛 ParseFailed；闸满了抛 llm_gate.Busy（路由转 429）。"""
    raw_text = (text or "").strip()[:MAX_TEXT]
    if not raw_text:
        raise ParseFailed("没有可整理的内容")

    messages = [
        ("system", SYSTEM_PROMPT),
        ("human", f"现在是 {_now_human(now)}。\n\n用户说：{raw_text}"),
    ]

    started = time.monotonic()
    async with llm_gate.slot():
        llm = get_llm(temperature=0, max_tokens=1200)
        resp = await llm.ainvoke(messages)
    content = resp.content if isinstance(resp.content, str) else str(resp.content)
    latency_ms = int((time.monotonic() - started) * 1000)

    items, unresolved = tighten(_extract_json(content), raw_text, now)
    return {
        "items": items,
        "unresolved": unresolved,
        "model": getattr(llm, "model_name", None) or "deepseek-chat",
        "latency_ms": latency_ms,
    }


def local_datetime(ts: int) -> str:
    """给测试和日志用：把秒换成本地时刻串（和 prompt 里同一个口径）。"""
    return datetime.fromtimestamp(ts, timeutil.TZ).strftime("%Y-%m-%d %H:%M")


def tomorrow_midnight(now: int) -> int:
    """`明天` 的收尾时刻（23:59:59）。有测试拿它当"明天交作业"的期望值。"""
    d = datetime.fromtimestamp(now, timeutil.TZ) + timedelta(days=1)
    d = d.replace(hour=23, minute=59, second=59, microsecond=0)
    return int(d.timestamp())
