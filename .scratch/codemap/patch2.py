# -*- coding: utf-8 -*-
import io

P = r"C:\biancheng\agent-hub\.scratch\codemap\data_block.txt"
t = io.open(P, encoding="utf-8-sig").read()
pairs = [
    ("{t:'class', n:'core/router.py', doc:'route', kw:['core/router.py']",
     "{t:'class', n:'core/router.py', doc:'route', kw:['分诊器']"),
    ("{t:'class', n:'hybrid/workflow.py', doc:'workflow', kw:['hybrid/workflow.py']",
     "{t:'class', n:'hybrid/workflow.py', doc:'workflow', kw:['节点 DSL']"),
]
for o, n in pairs:
    if t.count(o) != 1:
        raise SystemExit("count=%d for %s" % (t.count(o), o))
    t = t.replace(o, n)
io.open(P, "w", encoding="utf-8", newline="\n").write(t)
print("ok")
