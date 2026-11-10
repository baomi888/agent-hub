"""兼容壳（deprecated）：实现已并入 ``core/prompt_security.py``。

为什么还留这个文件
------------------
2026-11-08 这版防护先把入站粗筛与出站检查放在本模块；2026-11-10 的三级判定版
（canary + 24 字指纹 + holdback + soft 中和）落在 ``core/prompt_security.py``。
两套并存时出现过三种漂移：拒答文案两套、指标名两套、短指纹只有一边认。

现在规则表、判定与文案**只有一份**（prompt_security），本模块只做名字转发：

  - ``classify`` / ``looks_like_extraction`` / ``find_leak`` / ``StreamGuard``
    / ``LeakDetected`` / ``REFUSAL`` 保持旧签名，旧调用方与旧测试不用改；
  - 不要在别处新增实现，也不要在这里加规则——加在 prompt_security 里。

新代码请直接 ``from core import prompt_security``：入站用
``detect_prompt_extraction``，出站用 ``LeakFilter``（比 ``StreamGuard`` 多一层
holdback，指纹成形前不发字符）。
"""

from __future__ import annotations

from core.prompt_security import (
    REFUSAL_TEXT as REFUSAL,
)
from core.prompt_security import (
    SHORT_FINGERPRINTS as SYSTEM_FINGERPRINTS,
)
from core.prompt_security import (
    LeakDetected,
    StreamGuard,
    classify,
    find_leak,
    looks_like_extraction,
)

__all__ = [
    "REFUSAL",
    "SYSTEM_FINGERPRINTS",
    "LeakDetected",
    "StreamGuard",
    "classify",
    "find_leak",
    "looks_like_extraction",
]
