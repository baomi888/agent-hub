# -*- coding: utf-8 -*-
"""Agent 构建（LangChain v1 create_agent）。

核心：create_agent(model, tools, system_prompt, checkpointer)
  - 底层 LangGraph，自动处理 ReAct 循环 / 工具路由 / 流式输出
  - checkpointer 注入 MemorySaver，天然支持多轮对话记忆
  - 比旧版 AgentExecutor 更稳定，流式事件更丰富

注意：SummarizationMiddleware 在 langgraph 1.2.x 不存在。
当前用 MemorySaver + 窗口截断（api/chat.py 层）组合处理长会话。
"""

import os

from langchain.agents import create_agent
from langgraph.checkpoint.memory import MemorySaver

from agent.tools import AGENT_TOOLS
from core import config
from core.llm import get_llm

SYSTEM_PROMPT = """你是一个智能问答助手，拥有以下能力：
  - 从知识库中检索资料（kb_search）
  - 联网搜索（web_search）
  - 数学计算（calculator）
  - 查询中国城市天气（get_weather）
  - 下载文档类文件，可选并入知识库（download_file）

行为准则：
  1. 根据用户问题自主选择是否调用工具、调用哪个工具。
  2. 需要知识库资料时用 kb_search；需要最新互联网信息时用 web_search。
  3. 数学计算必须用 calculator 工具，禁止口算。
  4. 天气查询优先用 get_weather，支持中文或拼音输入（如 北京 / beijing）。
  5. 当用户要求"搜索并下载论文/文档/报告"时，先用 web_search 找到文件直链，再用 download_file 下载；若用户说明要入库，传入对应的 kb_id。
  6. 若某工具明确返回失败（如提示"暂时不可用"），不要用相同参数重复调用该工具，直接基于已有信息回答或如实告知用户。
  7. 工具返回错误时如实向用户解释，绝不编造。
  8. 参考知识库资料回答时，末尾注明来源编号（依据：[1][2]）。
  9. 闲聊、身份、常识等无需工具的问题直接回答。"""


def location_note(location: dict | None) -> str:
    """把用户地理定位信息整理成 system prompt 片段（无则空串）。

    location 形如 {"city": "上海", "lat": 31.23, "lon": 121.47}。
    城市名优先用后端逆地理反查的结果；若只有坐标也行，提示模型用坐标查天气。
    """
    if not location:
        return ""
    city = location.get("city")
    lat = location.get("lat")
    lon = location.get("lon")
    if not (lat is not None and lon is not None):
        return ""
    if city:
        return (
            f"\n\n【用户当前位置】{city}（经纬度 {lat}, {lon}）。"
            "当用户询问天气、气温、空气质量、穿衣指数、本地生活等且未明确指定城市时，"
            f"优先调用 get_weather(city='{city}')，无需追问用户所在城市。"
        )
    return (
        f"\n\n【用户当前位置】经纬度 {lat}, {lon}（前端地理定位得到，未反查出城市名）。"
        "当用户询问天气且未指定城市时，调用 get_weather(lat=" + str(lat) +
        f", lon={lon}) 用坐标直接查询。"
    )


def _make_checkpointer():
    """优先用 SQLite 落盘（重启不丢、可控），装了依赖才生效，否则退回内存版。

    MemorySaver 只在进程内有效：服务一重启 Agent 的多轮记忆就全没了，
    而且会话越攒越多没有上限。
    """
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver

        path = os.path.join(config.DATA_DIR, "agent_memory.sqlite")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return SqliteSaver.from_conn_string(path)
    except Exception:
        return MemorySaver()


# 模块级单例：checkpointer 全局共享，进程内记忆
_CHECKPOINTER = _make_checkpointer()


def build_agent(kb_id: str | None = None, location: dict | None = None):
    """构建集合式 Agent（带 5 个工具 + 多轮记忆）。

    kb_id 会被写进系统提示词：否则模型调用 kb_search 时只能凭空猜库名，
    绑定了知识库却在 Agent 模式下用不上。
    location 是用户地理定位（前端传来的经纬度 + 逆地理城市名），让用户问天气时免手输城市。
    """
    llm = get_llm(temperature=0.1)
    system_prompt = SYSTEM_PROMPT
    if kb_id:
        system_prompt += (
            f"\n\n【当前会话绑定的知识库】\n"
            f"kb_id = {kb_id}\n"
            f"需要查资料时直接用这个 id 调用 kb_search，不要猜测或编造其他 id。"
        )
    else:
        system_prompt += (
            "\n\n【当前会话没有绑定知识库】\n"
            "不要凭空猜测 kb_id 调用 kb_search；需要外部信息时用 web_search。"
        )
    system_prompt += location_note(location)

    agent = create_agent(
        model=llm,
        tools=AGENT_TOOLS,
        system_prompt=system_prompt,
        checkpointer=_CHECKPOINTER,
    )
    return agent


def get_checkpointer() -> MemorySaver:
    """暴露 checkpointer 供 API 层调用（流式对话用 thread_id 隔离会话）。"""
    return _CHECKPOINTER
