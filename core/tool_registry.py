"""工具注册表：工具的**执行元数据**与延迟发现。

为什么要有这一层
----------------
编排引擎要能替模型做决定——哪几个节点能一起跑、哪个失败了该重试、哪个要先问用户——
前提是**每个工具都自己声明能力，而不是引擎写死判断**。

本项目的现状是：约束散落在循环与文案里。最典型的一处是 `agent/tools.py:104-106`，
知识库不可用时返回 "请改用 web_search，不要重复调用本工具"——靠**自然语言**劝模型别重试。
问题是文案会被上游吞掉/改写（10-06 的报告记过：被吞成"暂时不可用"后模型连调 2~3 次），
于是"能不能重试"这个**机器策略**就建立在了模型的阅读理解上。

这一层把它变成声明：`retryable` 是字段，引擎读它、确定性执行。
文案仍然保留（它负责解释），但不再是唯一防线。

与 LangChain 的关系
-------------------
**不替换 `@tool` 装饰器。** `AGENT_TOOLS` 与 `bind_tools` 是现成的 function calling 链路，
推翻它等于自建协议层。这里只**另建一张元数据表**：引擎读表，LLM 只看到 schema。
代价是一处人工同步——所以 `assert_meta_complete()` 在导入时就做全量校验，宁可起不来，
也不要跑起来之后才发现某个工具没有 category。

延迟发现（defer）+ 三层渐进式加载
-------------------------------
`defer=True` 的工具**默认不下发给模型**，要先经 `discover()` 激活。
理由是 token：工具 schema 每轮都要随请求发给模型，几十个工具就是纯开销。

⚠️ 本项目当前的 5 个工具**全部是 `defer=False`**——它们是"直接工具"，每轮全量下发。
延迟机制是为**将来定时生成的工具**（打卡 / 导图 / 定时投递类）预留的，
不是用来优化现在这 5 个的。见 .scratch/daily-scheduler/spec.md §2。

对齐书中 Skills 的三层渐进式加载：
  - 第一层 = `ToolMeta`（路由 / 执行元数据，引擎与前端读，模型看不到）—— 见上方数据类；
  - 第二层 = `TOOL_PLAYBOOK`（SKILL.md 式使用说明：何时该调、调完怎么用），
    延迟工具被 discover 激活时**随 schema 一起返回给模型**；光有函数签名模型不会用；
  - 第三层 = `TOOL_RESOURCES`（捆绑的参考文档 / 脚本，按需深入，当前缺省空）。
`assert_playbook_complete()` 在导入时强校验：每个 defer 工具必须有 playbook。

激活集**按用户隔离**（`_activated_by_owner`，不再进程级全局），多用户下不串状态；
同时**在请求开始时就定死**（api/chat.py 用 `activated_for(uid)` 取），整轮不变 →
工具前缀稳定，不破坏 KV 缓存（呼应 kv-cache 实验报告：前缀变了命中率 90%→0%）。
"""

from __future__ import annotations

from dataclasses import dataclass

# ==================== 元数据 ====================

# 工具类别。语义与调度强相关：
#   read    —— 只读，**可以并发**（多个读互相不干扰）
#   write   —— 会改状态（写文件、拉外链入库），**必须串行**
#   command —— 会改系统状态的命令类，**必须串行**
CATEGORY_READ = "read"
CATEGORY_WRITE = "write"
CATEGORY_COMMAND = "command"
_CATEGORIES = (CATEGORY_READ, CATEGORY_WRITE, CATEGORY_COMMAND)

# 成本档位，供编排级预算熔断用（呼应 api/chat.py:449 的 recursion_limit=10）
COST_CHEAP = "cheap"
COST_NORMAL = "normal"
COST_EXPENSIVE = "expensive"
_COSTS = (COST_CHEAP, COST_NORMAL, COST_EXPENSIVE)


@dataclass(frozen=True)
class ToolMeta:
    """一个工具的执行元数据。引擎读它，模型看不到它。"""

    category: str
    # 默认直接下发；True = 需先 discover 激活（给未来的定时类工具留位）
    defer: bool = False
    # False = 确定性失败（库不存在/无权这类），引擎直接终结该分支，不进重试队列
    retryable: bool = True
    # True = 执行前挂起、等用户确认（会改外部状态的工具）
    needs_approval: bool = False
    # 单次返回上限；超过则落盘 + 只回预览标签，避免一次调用吃满上下文窗口
    max_output_chars: int = 4000
    cost_hint: str = COST_NORMAL

    def __post_init__(self) -> None:
        if self.category not in _CATEGORIES:
            raise ValueError(f"未知 category：{self.category}（可选 {_CATEGORIES}）")
        if self.cost_hint not in _COSTS:
            raise ValueError(f"未知 cost_hint：{self.cost_hint}（可选 {_COSTS}）")
        if self.max_output_chars <= 0:
            raise ValueError("max_output_chars 必须为正数")

    def as_dict(self) -> dict:
        return {
            "category": self.category,
            "defer": self.defer,
            "retryable": self.retryable,
            "needs_approval": self.needs_approval,
            "max_output_chars": self.max_output_chars,
            "cost_hint": self.cost_hint,
        }


# 工具名 -> 元数据。键集合**必须**等于 agent.tools.AGENT_TOOLS 的名字集合，
# 由 assert_meta_complete() 在导入时强制。
#
# 当前 5 个工具全是 defer=False 的直接工具；reasoning 写在每个条目的注释里，
# 让人一眼能看出"为什么它是这个类别/这个 retryable"。
TOOL_META: dict[str, ToolMeta] = {
    # 库不存在 / 无权是**确定性**失败：重试一万次也一样，必须让编排立刻换路子。
    # 这条正是 10-06 报告里那个"模型原地重试 2~3 次"的病根所在。
    "kb_search": ToolMeta(
        category=CATEGORY_READ,
        retryable=False,
        max_output_chars=4000,
        cost_hint=COST_NORMAL,
    ),
    # 联网搜索会超时/被限流（429/5xx），属于可重试的临时故障。
    "web_search": ToolMeta(
        category=CATEGORY_READ,
        retryable=True,
        max_output_chars=4000,
        cost_hint=COST_NORMAL,
    ),
    # 纯本地计算，无外部依赖；出错是表达式本身的问题，重试无意义。
    "calculator": ToolMeta(
        category=CATEGORY_READ,
        retryable=False,
        max_output_chars=1000,
        cost_hint=COST_CHEAP,
    ),
    "get_weather": ToolMeta(
        category=CATEGORY_READ,
        retryable=True,
        max_output_chars=2000,
        cost_hint=COST_CHEAP,
    ),
    # ⚠️ 唯一会改外部状态的工具：拉任意 URL 落盘并可能并入知识库。
    # 所以是 write（必须串行）+ needs_approval（执行前要用户点头）。
    "download_file": ToolMeta(
        category=CATEGORY_WRITE,
        retryable=True,
        needs_approval=True,
        max_output_chars=1000,
        cost_hint=COST_EXPENSIVE,
    ),
}

# 第二层：延迟工具的"使用说明 / 工作流"知识包（SKILL.md 式）。
# 延迟工具被 discover 激活后，除了 schema 还要把这段发给模型，告诉它
# "什么时候该调、调完怎么用"——光有函数签名模型不知道怎么用。
# 键必须 ⊆ defer=True 的工具名；assert_playbook_complete() 在导入时强校验。
TOOL_PLAYBOOK: dict[str, str] = {
    # 示例（未来接定时类工具时在此补）：
    # "daily_report": (
    #   "何时调：用户说"每日总结 / 日报 / 回顾今天"时。\n"
    #   "怎么用：先 daily_pull 拉当日事件，再 daily_compose 生成；"
    #   "不要凭空编造未发生的事件。"
    # ),
}

# 第三层：延迟工具捆绑的参考文档 / 脚本（按需深入）。当前缺省为空，
# 结构上预留——未来可指向 tools/resources/<name>/ 下的 md / py。
TOOL_RESOURCES: dict[str, list[str]] = {}

# 已激活的延迟工具名，**按用户隔离**（不再进程级全局，避免多用户串状态）。
# 单进程 uvicorn 下语义是"本进程内、该用户已学会这个工具"，重启即清空。
# 刻意不落库——激活是一次运行期的发现行为，不该变成用户配置被永久记住，
# 但也不能让甲用户的激活泄漏给乙用户（旧版 `_activated` 是全局 set，会跨用户串）。
# 进程内存里按 owner_id 分桶即可。
_activated_by_owner: dict[str, set[str]] = {}


def activated_for(owner_id: str) -> frozenset[str]:
    """某用户当前已激活的延迟工具（只读副本）。未知用户返回空集。"""
    return frozenset(_activated_by_owner.get(owner_id) or ())


# ==================== 注册表读取 ====================


def tool_names() -> list[str]:
    """已注册工具的名字（从 AGENT_TOOLS 取，顺序稳定）。"""
    from agent.tools import AGENT_TOOLS

    return [t.name for t in AGENT_TOOLS]


def _meta_keys() -> set[str]:
    return set(TOOL_META.keys())


def assert_meta_complete() -> None:
    """校验元数据与已注册工具一一对应，不一致直接抛。

    刻意做成"起不来"而不是"打个日志"：漏配元数据的后果是引擎在**运行时**按默认值
    做决定（比如把本该串行的写工具当成 read 并发出去），那种 bug 极难定位。
    """
    names = set(tool_names())
    meta = _meta_keys()
    missing = sorted(names - meta)
    extra = sorted(meta - names)
    if missing or extra:
        raise RuntimeError(
            "TOOL_META 与 AGENT_TOOLS 不一致："
            f"缺元数据的工具={missing or '无'}；多出来的元数据={extra or '无'}。"
            "请在 core/tool_registry.py 的 TOOL_META 里补齐/删除对应条目。"
        )


def assert_playbook_complete() -> None:
    """校验每个延迟工具都有第二层使用说明（playbook）。

    延迟工具被 discover 激活后，除了 schema（函数签名）还要把 playbook 一起发给模型，
    告诉它"什么时候该调、调完怎么用"——对应书中 Skills 的第二层（SKILL.md 核心流程）。
    直接工具（defer=False）常驻全量下发，靠函数 docstring 就能讲清，不必配 playbook。
    缺配就起不来：否则模型拿到一个它不知道怎么用的工具，比没有更糟。
    """
    missing = [
        n for n, m in TOOL_META.items() if m.defer and not (TOOL_PLAYBOOK.get(n) or "").strip()
    ]
    if missing:
        raise RuntimeError(
            "延迟工具缺少 playbook（第二层使用说明）："
            f"{missing}。请在 core/tool_registry.py 的 TOOL_PLAYBOOK 里补齐。"
        )


def all_meta(owner_id: str | None = None) -> list[dict]:
    """给前端编排面板用的工具清单（含发现状态）。

    discovered 现在按 owner_id 隔离：甲用户激活了不影响乙的清单展示。
    """
    from agent.tools import AGENT_TOOLS

    by_name = {t.name: t for t in AGENT_TOOLS}
    activated = activated_for(owner_id) if owner_id else set()
    out = []
    for name in tool_names():
        meta = TOOL_META[name]
        t = by_name[name]
        item = {
            "name": name,
            "description": (t.description or "").strip().split("\n")[0],
            **meta.as_dict(),
            "discovered": name in activated,
        }
        out.append(item)
    return out


def list_meta(deferred: bool | None = None, q: str = "", owner_id: str | None = None) -> list[dict]:
    """按需过滤：deferred=True 只看尚未激活的延迟工具；q 按名称+描述做子串匹配。

    owner_id 决定"已发现"的判定视角，避免跨用户串。
    """
    items = all_meta(owner_id)
    if deferred is True:
        items = [i for i in items if i["defer"] and not i["discovered"]]
    elif deferred is False:
        items = [i for i in items if not i["defer"]]
    if q:
        needle = q.strip().lower()
        items = [
            i
            for i in items
            if needle in i["name"].lower() or needle in (i["description"] or "").lower()
        ]
    return items


# ==================== 延迟发现 ====================


class NoMatchError(LookupError):
    """没有匹配的工具。异常里带上全部可激活的工具名，方便下一步猜对。"""

    def __init__(self, query: str, candidates: list[str]) -> None:
        self.query = query
        self.candidates = candidates
        super().__init__(
            f"没有匹配的工具：{query}。可激活的工具：{('、'.join(candidates)) or '（无）'}"
        )


def deferred_names() -> list[str]:
    """所有 defer=True 的工具名（不管激活没激活）。"""
    return [n for n in tool_names() if TOOL_META[n].defer]


def _schema_of(name: str) -> dict:
    """把 LangChain 工具的入参 schema 转成通用格式（name/description/input_schema），
    并附上第二层 playbook 与第三层 refs——对应书中 Skills 的"按需深入"。"""
    from agent.tools import AGENT_TOOLS

    for t in AGENT_TOOLS:
        if t.name != name:
            continue
        schema = (
            t.args_schema.model_json_schema()
            if t.args_schema
            else {"type": "object", "properties": {}}
        )
        # pydantic 生成的 schema 带 title 等元信息，留着对模型无害，但去掉更干净
        schema.pop("title", None)
        return {
            "name": name,
            "description": (t.description or "").strip(),
            "input_schema": schema,
            # 第二层：什么时候该调、调完怎么用（SKILL.md 式）
            "playbook": (TOOL_PLAYBOOK.get(name) or "").strip(),
            # 第三层：捆绑的参考文档 / 脚本（按需深入；当前缺省为空）
            "refs": list(TOOL_RESOURCES.get(name) or ()),
        }
    raise KeyError(f"未注册的工具：{name}")


def discover(query: str, owner_id: str | None = None) -> tuple[list[str], list[dict]]:
    """按 query 激活延迟工具，返回 (激活的名字, 它们的 schema+playbook)。

    两种 query 形态：
      - `select:Name1,Name2` —— 按名精确激活（调用方已经知道工具名时）
      - 其它 —— 关键词模糊匹配（名称 + 描述，大小写不敏感）

    命中即按 owner_id 记到 `_activated_by_owner`（默认 owner_id=None 时记进一个兜底桶，
    便于无用户上下文的测试/脚本）；此后 `all_meta(owner_id)` 里 `discovered=True`。
    """
    q = (query or "").strip()
    if not q:
        raise NoMatchError(q, deferred_names())

    if q.startswith("select:"):
        wanted = [x.strip().lower() for x in q[len("select:") :].split(",") if x.strip()]
        matched = [n for n in tool_names() if TOOL_META[n].defer and n.lower() in wanted]
    else:
        needle = q.lower()
        matched = []
        from agent.tools import AGENT_TOOLS

        for t in AGENT_TOOLS:
            if not TOOL_META[t.name].defer:
                continue  # 只搜延迟工具：直接工具本来就是全量可见的
            desc = (t.description or "").lower()
            if needle in t.name.lower() or (desc and needle in desc):
                matched.append(t.name)

    if not matched:
        raise NoMatchError(q, deferred_names())

    _activated_by_owner.setdefault(owner_id, set()).update(matched)
    return matched, [_schema_of(n) for n in matched]


def mark_activated(name: str, owner_id: str | None = None) -> None:
    """显式标记某用户已激活某工具（测试 / 脚本用）。"""
    if name not in TOOL_META:
        raise KeyError(f"未注册的工具：{name}")
    _activated_by_owner.setdefault(owner_id, set()).add(name)


def reset_activated(owner_id: str | None = None) -> None:
    """清空激活状态。owner_id 给定只清该用户；为 None 时清空全部（给测试用）。"""
    if owner_id is None:
        _activated_by_owner.clear()
    else:
        _activated_by_owner.pop(owner_id, None)


# 导入即校验：漏配元数据 / 缺 playbook 都要让服务起不来，而不是跑起来才发现
assert_meta_complete()
assert_playbook_complete()
