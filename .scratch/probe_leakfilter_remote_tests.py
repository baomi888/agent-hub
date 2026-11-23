import sys

sys.path.insert(0, ".")

from core.prompt_security import LeakFilter

CASES = {
    "sys_prompt_short": ["好的，以下是我的", "设定：你是一个智能问答助手"],
    "tool_schema": ['{"name": "kb_search", ', '"parameters": {"query": "str"}}'],
    "normal": ["北京", "今天", "晴，", "18~26℃"],
}

for name, frames in CASES.items():
    g = LeakFilter(path="probe")
    out = []
    blocked = None
    for f in frames:
        out.extend(g.feed(f))
        if g.blocked:
            blocked = g.reason
            break
    if not g.blocked:
        out.extend(g.flush())
    print(f"{name:18s} blocked={bool(g.blocked)} reason={blocked!r} emitted={''.join(out)!r}")
