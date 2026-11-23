# -*- coding: utf-8 -*-
"""变异测试：证明快照真能抓到「字段改名」和「少一帧」，并检查报错信息可读。

实验期间临时改写 tests/golden/rag.json，finally 恢复原文。
"""
import json
import subprocess
import sys

P = r"C:\biancheng\agent-hub\tests\golden\rag.json"
TEST = "tests/test_stream_golden.py::test_rag_frame_sequence_matches_golden"

orig = open(P, encoding="utf-8").read()
base = json.loads(orig)


def run(label):
    r = subprocess.run(
        [sys.executable, "-m", "pytest", TEST, "-q", "--basetemp=.pytest_tmp_mut"],
        capture_output=True,
        cwd=r"C:\biancheng\agent-hub",
    )
    out = (r.stdout + r.stderr).decode("utf-8", "replace")
    lines = [ln for ln in out.splitlines() if ln.strip()]
    print("=" * 60)
    print(label, "→ 退出码", r.returncode, "(1 = 抓到了)")
    print("-" * 60)
    for ln in lines:
        if any(k in ln for k in ("不一致", "帧数", "帧名", "字段", "快照=", "实际=", "缺少", "多出")):
            print("  " + ln.strip()[:150])
    print()


try:
    # 实验 1：把 done 里的 plan_topic 改名成 planTopic（模拟拼错字段名）
    d1 = json.loads(orig)
    d1[-1]["data"]["planTopic"] = d1[-1]["data"].pop("plan_topic")
    open(P, "w", encoding="utf-8").write(json.dumps(d1, ensure_ascii=False, indent=2))
    run("实验1 字段改名 plan_topic→planTopic")

    # 实验 2：删掉第 4 帧（"检索到 2 条片段"这条 status），模拟少发一帧
    d2 = json.loads(orig)
    del d2[3]
    open(P, "w", encoding="utf-8").write(json.dumps(d2, ensure_ascii=False, indent=2))
    run("实验2 少一帧（删除第 4 帧 status）")

    # 实验 3：把 grounding 和 sources 互换位置，模拟帧序错乱
    d3 = json.loads(orig)
    d3[1], d3[2] = d3[2], d3[1]
    open(P, "w", encoding="utf-8").write(json.dumps(d3, ensure_ascii=False, indent=2))
    run("实验3 帧序错乱（sources 与 grounding 互换）")
finally:
    open(P, "w", encoding="utf-8").write(orig)
    print("=" * 60)
    print("已恢复快照原文，长度", len(orig), "字符")
