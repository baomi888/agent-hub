# -*- coding: utf-8 -*-
"""补丁 3：时序图收敛到 7 个参与者，并同步 3 处 kw。"""
import io

P = r"C:\biancheng\agent-hub\.scratch\codemap\data_block.txt"
t = io.open(P, encoding="utf-8-sig").read()

start = t.index("const SEQ_SRC = `sequenceDiagram")
end = t.index("const FLOW_SRC = `flowchart TB")

NEW = '''const SEQ_SRC = `sequenceDiagram
    autonumber
    participant BR as 浏览器
    participant CH as ChatAPI
    participant GUARD as 准入闸<br/>闸 · JWT · 额度
    participant RT as 分诊器
    participant RAG as 检索
    participant AG as ReAct 智能体
    participant SESS as SQLite 会话

    BR->>CH: POST /api/chat/stream（SSE）
    CH->>GUARD: 抢闸 → JWT → 输入拦截 → 扣额度
    GUARD->>SESS: 会话 / 黑名单 / 今日用量
    GUARD-->>CH: uid + ticket
    CH->>RT: decide(question)
    RT->>RT: 轻量 LLM 分诊（ROUTER_TIMEOUT=2s）
    RT-->>CH: RouteDecision(agent)
    Note over CH,RT: Next.js rewrite 进入；use_kb / use_web 只带开关
    CH->>CH: build_agent(kb_state, use_web)<br/>同键复用已编译图
    CH->>RAG: retrieve_with_score()
    RAG->>RAG: 向量 + BM25 → RRF → MMR
    RAG-->>CH: docs_scores
    CH-->>BR: sources / grounding（先于正文）
    CH->>AG: astream(recursion_limit=10)
    loop 至多 10 步
        AG->>AG: kb_search / web_search / get_weather
        AG-->>CH: token 增量
        CH->>GUARD: 输出侧 LeakFilter
        CH-->>BR: event: token
    end
    CH->>SESS: add_message(assistant)
    CH-->>BR: done {answer, refs}`;

'''
t = t[:start] + NEW + t[end:]

pairs = [
    ("doc:'quota', kw:['LlmGate']", "doc:'quota', kw:['抢闸']"),
    ("{t:'class', n:'core/session.py', doc:'session', kw:['session.py']",
     "{t:'class', n:'core/session.py', doc:'session', kw:['SQLite 会话']"),
]
for o, n in pairs:
    if t.count(o) != 1:
        raise SystemExit("count=%d %s" % (t.count(o), o))
    t = t.replace(o, n)

io.open(P, "w", encoding="utf-8", newline="\n").write(t)
print("seq patched")
