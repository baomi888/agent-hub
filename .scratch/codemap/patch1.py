# -*- coding: utf-8 -*-
"""一次性补丁：把 OVER 换成角色短标签版，并同步调整 TREE 的 kw 锚点。"""
import io
import re

P = r"C:\biancheng\agent-hub\.scratch\codemap\data_block.txt"
t = io.open(P, encoding="utf-8-sig").read()

# ---------- 1. 替换 OVER_SRC ----------
start = t.index("/* 全景图：模块地图")
end = t.index("/* ══════════════ 左侧树数据")
NEW_OVER = '''/* 全景图：模块地图（角色短标签，13 个节点；具体路径见右侧说明卡） */
const OVER_SRC = `flowchart TB
    classDef modc fill:#f4f8ff,stroke:#8fb0e8,stroke-width:1.6px,color:#1c3d6e
    classDef newc fill:#e6f6ec,stroke:#17a45f,stroke-width:2px,color:#0b5c3a
    classDef oldc fill:#f1f3f6,stroke:#9aa3af,stroke-width:1.2px,stroke-dasharray:5 3,color:#5a6068
    classDef warnc fill:#fff4e0,stroke:#e6a23c,stroke-width:1.6px,color:#7a4101
    classDef errc fill:#fdecec,stroke:#e5484d,stroke-width:1.8px,color:#b3232a
    classDef extc fill:#eaf1fe,stroke:#3574f0,stroke-width:1.6px,color:#1c3257

    WEB["🖥️ 前端<br/>Next.js 15 · 27 组件"]

    subgraph API["🚪 服务端 · api + core（单进程 FastAPI）"]
        direction TB
        ENTRY["入口装配<br/>lifespan · normalize_api_slash"]
        CHATAPI["SSE 主链路<br/>五条分支"]
        OTHERAPI["知识库 · 认证<br/>混合执行 · 看板"]
        GUARDS["鉴权 · 额度<br/>并发闸"]
        CROUTER["分诊器<br/>workflow / agent"]
        CTXSESSION["安全 · 压缩<br/>会话存储"]
    end
    subgraph BRANCH["🧠 执行分支 · 五选一"]
        direction TB
        AGENTB["智能体装配<br/>4 个工具"]
        RAGC["混合检索<br/>向量 + BM25 + RRF"]
        HYBO["混合引擎<br/>节点 DSL · 双引擎"]
    end
    WORKER["定时任务<br/>APScheduler 独立进程"]
    OPS["运维资产<br/>deploy · systemd · https"]
    EXT[("☁️ DeepSeek / Serper / 天气")]

    WEB --> CHATAPI
    ENTRY --> CHATAPI
    CHATAPI --> GUARDS
    CHATAPI --> CROUTER
    CHATAPI --> CTXSESSION
    CHATAPI --> AGENTB
    CHATAPI --> RAGC
    CHATAPI --> HYBO
    CROUTER --> HYBO
    HYBO --> AGENTB
    AGENTB --> RAGC
    AGENTB --> EXT
    OTHERAPI --> CTXSESSION
    GUARDS --> CTXSESSION
    WORKER --> CTXSESSION
    OPS -.-> WORKER

    class CHATAPI,GUARDS,CROUTER,CTXSESSION,AGENTB,RAGC,HYBO newc
    class WEB,ENTRY,OTHERAPI,WORKER oldc
    class OPS warnc
    class EXT extc`;

'''
t = t[:start] + NEW_OVER + t[end:]

# ---------- 2. TREE 的 kw 锚点（指向图里真实存在的短文本） ----------
REPL = [
    ("{t:'class', n:'main.py', doc:'entry', kw:['main.py']",
     "{t:'class', n:'main.py', doc:'entry', kw:['入口装配']"),
    ("{t:'class', n:'api/chat.py', doc:'chatapi', kw:['api/chat.py']",
     "{t:'class', n:'api/chat.py', doc:'chatapi', kw:['api/chat/stream']"),
    ("{t:'class', n:'api/kb.py · api/auth.py', doc:'apilayer', kw:['api/kb.py']",
     "{t:'class', n:'api/kb.py · api/auth.py', doc:'apilayer', kw:['知识库']"),
    ("doc:'apilayer', kw:['hybrid · delegate · report']",
     "doc:'apilayer', kw:['混合执行']"),
    ("{t:'class', n:'core/auth.py', doc:'auth', kw:['core/auth.py']",
     "{t:'class', n:'core/auth.py', doc:'auth', kw:['get_current_user']"),
    ("{t:'class', n:'core/quota.py + core/llm_gate.py', doc:'quota', kw:['core/llm_gate.py']",
     "{t:'class', n:'core/quota.py + core/llm_gate.py', doc:'quota', kw:['llm_gate.acquire()']"),
    ("{t:'class', n:'core/context_compress.py', doc:'compress', kw:['core/context_compress.py']",
     "{t:'class', n:'core/context_compress.py', doc:'compress', kw:['context_compress.maybe_compress']"),
    ("{t:'class', n:'core/prompt_security.py', doc:'sec', kw:['core/prompt_security.py']",
     "{t:'class', n:'core/prompt_security.py', doc:'sec', kw:['detect_prompt_extraction']"),
    ("{t:'class', n:'core/session.py', doc:'session', kw:['core/session.py']",
     "{t:'class', n:'core/session.py', doc:'session', kw:['session.py']"),
    ("{t:'class', n:'agent/builder.py', doc:'agentbuild', kw:['agent/builder.py']",
     "{t:'class', n:'agent/builder.py', doc:'agentbuild', kw:['build_agent']"),
    ("{t:'class', n:'agent/tools.py', doc:'tools', kw:['agent/tools.py']",
     "{t:'class', n:'agent/tools.py', doc:'tools', kw:['kb_search']"),
    ("{t:'class', n:'kb/rag_core.py', doc:'rag', kw:['kb/rag_core.py']",
     "{t:'class', n:'kb/rag_core.py', doc:'rag', kw:['retrieve_with_score']"),
    ("{t:'class', n:'hybrid/orchestrator.py', doc:'hybrid', kw:['hybrid/orchestrator.py']",
     "{t:'class', n:'hybrid/orchestrator.py', doc:'hybrid', kw:['混合引擎']"),
    ("{t:'class', n:'worker/scheduler.py', doc:'worker', kw:['worker/scheduler.py']",
     "{t:'class', n:'worker/scheduler.py', doc:'worker', kw:['APScheduler']"),
    ("{t:'class', n:'frontend（Next.js 15 App Router）', doc:'frontend', kw:['Next.js rewrite']",
     "{t:'class', n:'frontend（Next.js 15 App Router）', doc:'frontend', kw:['Next.js 15']"),
    ("{t:'class', n:'data/*.db（SQLite WAL）', doc:'session', kw:['SQLite WAL']",
     "{t:'class', n:'data/*.db（SQLite WAL）', doc:'session', kw:['SQLite']"),
    ("{t:'class', n:'chroma_db（每个知识库一个集合）', doc:'rag', kw:['chroma_db']",
     "{t:'class', n:'chroma_db（每个知识库一个集合）', doc:'rag', kw:['RRF']"),
    ("doc:'ops', kw:['ops 运维资产']", "doc:'ops', kw:['运维资产']"),
]
for old, new in REPL:
    c = t.count(old)
    if c != 1:
        raise SystemExit("替换次数 %d：%s" % (c, old[:50]))
    t = t.replace(old, new)

io.open(P, "w", encoding="utf-8", newline="\n").write(t)
print("patched")
