# -*- coding: utf-8 -*-
"""金标准帧快照（characterization test）：录制与比对。

它不判断「行为对不对」，只判断「行为有没有变」。

用法是两段式：

    RECORD_GOLDEN=1 pytest tests/test_stream_golden.py -q   # 录一次，把 JSON 一起提交
    pytest tests/test_stream_golden.py -q                   # 之后每次跑都是比对

三条约束，少一条快照就会变成摆设或 flaky：

1. **快照是列表，下标即帧序**。两帧互换位置列表就不等——这正是「顺序变了」的
   检测方式，不需要额外的顺序断言。
2. **默认不做归一化**。所有会变的值（LLM 输出、检索结果、消息 id、标题）都在
   fixture 里钉死；归一化会把「这个值又变回随机了」这种问题一起抹掉，等于降低
   断言强度。确实固定不住的字段（如 uuid）用 ``assert_golden(..., volatile={"run_id"})``
   显式声明，让「哪些字段不比较」出现在代码里，而不是藏在默认行为里。
3. **本模块不 import 任何业务代码**。它要能在 config 覆盖、sys.path 调整之前被
   安全导入，所以只做字符串与 JSON 处理。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

GOLDEN_DIR = Path(__file__).parent / "golden"


# ==================== 响应体 → 帧列表 ====================


def parse_frames(body: str) -> list[tuple[str, str]]:
    """把 SSE 响应体切成 [(事件名, data 文本)]。

    顺序就是响应体里的出现顺序——这是「帧序」的唯一来源。
    与 ``tests/test_baseline_chat_stream.py`` 原来的 ``_frames`` 同一实现。
    """
    out: list[tuple[str, str]] = []
    for raw in body.split("\n\n"):
        raw = raw.strip()
        if not raw:
            continue
        name = ""
        data = ""
        for line in raw.split("\n"):
            if line.startswith("event:"):
                name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data += line[len("data:") :].strip()
        out.append((name, data))
    return out


def frames_of(body: str) -> list[dict]:
    """响应体 → 可直接与快照比对的结构：[{"event": 帧名, "data": payload}]。"""
    out = []
    for name, data in parse_frames(body):
        out.append({"event": name, "data": json.loads(data) if data else {}})
    return out


# ==================== 比对 ====================


def _clean(obj, volatile) -> object:
    """把显式声明为 volatile 的键抹成固定串（保留键本身，仍校验存在性）。"""
    if not volatile:
        return obj
    if isinstance(obj, dict):
        return {
            k: ("<volatile>" if k in volatile else _clean(v, volatile))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_clean(x, volatile) for x in obj]
    return obj


def assert_golden(name: str, frames: list, *, volatile=frozenset()) -> None:
    """与 ``tests/golden/<name>.json`` 对比。``RECORD_GOLDEN=1`` 时改为写文件。"""
    actual = _clean(frames, volatile)
    path = GOLDEN_DIR / f"{name}.json"

    if os.environ.get("RECORD_GOLDEN") == "1":
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        # newline="\n" 是刻意的：默认行为在 Windows 上写 CRLF、Linux 上写 LF，
        # 同一份快照在不同机器录出来字节不同，git diff 会整文件变更。
        with path.open("w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(actual, ensure_ascii=False, indent=2) + "\n")
        return

    if not path.exists():
        raise AssertionError(
            f"缺少快照 tests/golden/{name}.json。"
            f"先跑 RECORD_GOLDEN=1 pytest tests/test_stream_golden.py -q 录一次并提交"
        )

    expected = json.loads(path.read_text(encoding="utf-8"))
    if actual != expected:
        raise AssertionError(describe_diff(expected, actual, name))


def _short(obj) -> str:
    s = json.dumps(obj, ensure_ascii=False)
    return s if len(s) <= 160 else s[:157] + "..."


def describe_diff(expected: list, actual: list, name: str = "") -> str:
    """把列表差异翻译成人话：帧数、第几帧、哪个字段、两边各是什么。"""
    lines = [f"帧序列与快照 {name or ''} 不一致".strip()]

    if len(expected) != len(actual):
        lines.append(f"帧数：快照 {len(expected)}，实际 {len(actual)}")
        if len(actual) > len(expected):
            lines.append(f"  多出第 {len(expected) + 1} 帧：{_short(actual[len(expected)])}")
        else:
            lines.append(f"  缺少第 {len(actual) + 1} 帧：{_short(expected[len(actual)])}")

    shown = 0
    for i in range(min(len(expected), len(actual))):
        if expected[i] == actual[i]:
            continue
        shown += 1
        if shown > 3:
            lines.append("  …（更多差异已省略）")
            break
        e, a = expected[i], actual[i]
        lines.append(f"第 {i + 1} 帧：")
        if e.get("event") != a.get("event"):
            lines.append(f"  帧名：快照={e.get('event')!r} 实际={a.get('event')!r}")
        ed = e.get("data") or {}
        ad = a.get("data") or {}
        if isinstance(ed, dict) and isinstance(ad, dict):
            missing = [k for k in ed if k not in ad]
            extra = [k for k in ad if k not in ed]
            if missing:
                lines.append(f"  字段缺失：{missing}")
            if extra:
                lines.append(f"  字段多出：{extra}")
            for k in ed:
                if k in ad and ad[k] != ed[k]:
                    lines.append(f"  {k}：快照={_short(ed[k])} 实际={_short(ad[k])}")
            if not missing and not extra and ed == ad:
                lines.append(f"  整帧不同：{_short(e)} != {_short(a)}")
        else:
            lines.append(f"  快照={_short(e)}")
            lines.append(f"  实际={_short(a)}")
    return "\n".join(lines)
