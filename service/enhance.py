# -*- coding: utf-8 -*-
"""检索词增强的业务层（roadmap B3 背后的东西，B4 接聊天时复用同一份）。

为什么单独一层，而不是把逻辑写在 api/query.py 里
------------------------------------------------
因为下一步 B4 要把这件事接进聊天链路，而 worker / 聊天 / 预览用的必须是**同一套**
词表与规则。写进 HTTP 层，第二个调用方就得复制一份，然后两份慢慢走偏。
（项目里 service/daily.py 也是这个理由：手动触发与定时触发共用一套函数。）

两件关键设计
------------
1. **词表按 mtime 热加载，不重启进程。**
   词表是组员反复迭代的文件，一天可能改十几版。每次改都要重启服务，
   等于没人愿意改。所以这里比对 (路径, mtime_ns, size)，变了才重读。
2. **词表路径自动挑选。**
   `config.ENHANCE_VOCAB_PATH` 留空时优先用 `vocab_v1.csv`（X1 正式词表），
   没有再退回 `temp_from_golden.csv`（从金标集派生的临时词表）。
   X1 到货那天只要把文件放进去，服务自己就换过去了。
"""
from __future__ import annotations

import logging
import os
import threading

from core import config
from core.query_enhance import (
    blocked_for_query,
    clarify_questions,
    load_adjudication,
    load_vocab,
    match,
)

_LOCK = threading.Lock()
_CACHE: dict[str, dict] = {}      # key -> {"stamp": tuple, "obj": 对象, "warns": list}

# 检索链路降级只报一次警：词表坏了会让每一句话都走异常分支，
# 不设这个标志，日志会被同一行刷满，真正的问题反而被淹掉。
_DEGRADE_REPORTED = False

_VOCAB_CANDIDATES = ("vocab_v1.csv", "temp_from_golden.csv")


def vocab_dir() -> str:
    """词表目录。**调用时才读 config.DATA_DIR，不在 import 时算死。**

    import 时算死过一次：有测试会把 config.DATA_DIR 改到临时目录，
    于是"谁先 import 谁决定路径"，后改的模块读到别人的目录（全量跑时就是这么红的）。
    线上不会改 DATA_DIR，但别让 import 顺序变成隐式契约。
    """
    return os.path.join(config.DATA_DIR, "vocab")


def resolve_vocab_path() -> str:
    """按优先级挑词表文件。env 显式指定则直接用（哪怕它不存在，好让报错更明确）。"""
    if config.ENHANCE_VOCAB_PATH:
        return config.ENHANCE_VOCAB_PATH
    d = vocab_dir()
    for name in _VOCAB_CANDIDATES:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return os.path.join(d, _VOCAB_CANDIDATES[-1])


def _stamp(path: str) -> tuple:
    try:
        st = os.stat(path)
        return (path, st.st_mtime_ns, st.st_size)
    except OSError:
        return (path, 0, 0)


def _cached(key: str, path: str, loader):
    """通用缓存：stamp 变了才重新加载。loader 返回 (对象, 警告列表)。"""
    stamp = _stamp(path)
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and hit["stamp"] == stamp:
            return hit["obj"], hit["warns"]
    obj, warns = loader(path)
    with _LOCK:
        _CACHE[key] = {"stamp": stamp, "obj": obj, "warns": warns}
    return obj, warns


def get_vocab():
    path = resolve_vocab_path()
    return _cached("vocab", path, load_vocab)


def get_adjudication():
    """裁决表缺失时返回空字典 —— 歧义拦截是增强项，不能因为它把接口搞挂。"""
    path = config.ENHANCE_ADJUDICATION_PATH
    return _cached("adj", path, lambda p: (load_adjudication(p), []))


def reset_cache() -> None:
    """测试用：清缓存。生产不需要（mtime 会自己触发重载）。"""
    with _LOCK:
        _CACHE.clear()


def stats() -> dict:
    """词表与裁决表的规模/版本，给前端"用的是哪版词表"和排障用。"""
    vocab, warns = get_vocab()
    adj, _ = get_adjudication()
    path = resolve_vocab_path()
    mtime = None
    try:
        mtime = int(os.stat(path).st_mtime)
    except OSError:
        pass
    return {
        "path": path,
        "file": os.path.basename(path),
        "mtime": mtime,
        "terms": len(vocab),
        "aliases": len(vocab.alias_to_canonical),
        "negatives": len(vocab.negatives),
        "adjudication": {
            "words": len(adj),
            "blocked": sum(1 for a in adj.values() if a.is_blocked),
            "clarify": sum(1 for a in adj.values() if a.needs_clarify),
            "conditional": sum(1 for a in adj.values() if a.is_conditional),
        },
        "warnings": warns,
    }


def preview(text: str, with_clarify: bool = True) -> dict:
    """给一句话做增强预览。**不写库、不调模型，纯本地计算。**

    返回的 `original` 与 `retrieval` 必须分开给前端：
    前者用于展示与聊天记录（三原则之"不改展示"），后者只用于检索。

    ★ 两者不再是"两种语法"：`retrieval` = `original` + 空格 + `added` 里的术语串。
      原话是恒定的前缀（2026-10-24 从替换改追加，见 matcher.EnhanceResult）。
      前端要展示"多加了什么"，应该渲染 `added`，不要拿 `retrieval` 整行去显示。
    """
    vocab, warns = get_vocab()
    adj, _ = get_adjudication()

    blocked = blocked_for_query(adj, text) if adj else set()
    result = match(text, vocab, global_block=blocked)

    return {
        "original": result.original,
        "retrieval": result.retrieval_text,
        # ★ retrieval 现在是「原话 + 空格 + 术语串」，原文恒为前缀。
        # 前端要用的是「追加了哪几个术语」这个列表，而不是把 retrieval 整行显示——
        # 整行显示等于把用户原话再念一遍，又长又难看。去重、保持出现顺序。
        "added": result.added_terms,
        "changed": bool(result.hits),
        "hits": [
            {"raw": h.raw, "canonical": h.canonical, "start": h.start, "end": h.end}
            for h in result.hits
        ],
        # 命中但没换的，附上原因。前端不展示，但排查"为什么这里没换"时全靠它。
        "skipped": [
            {"raw": s.raw, "canonical": s.canonical, "reason": s.reason}
            for s in result.skipped
        ],
        "clarify": clarify_questions(adj, text) if (with_clarify and adj) else [],
        "vocab": {"file": os.path.basename(resolve_vocab_path()),
                  "terms": len(vocab)},
        "warnings": warns,
    }


def enhance_for_retrieval(text: str) -> tuple[str, list[dict]]:
    """检索链路专用（B4）：只返回「检索文本 + 命中了哪些口语词」，不做统计与追问。

    返回的检索文本是 **原话 + 追加的标准词**（例：`高数怎么复习 高等数学`）。
    原话一个字没动 —— 追加式增强在结构上就不会造病句（替换式会，实测每 3 条坏 1 条）。

    和 preview() 的分工
    -------------------
    preview() 是给人看的：要 stats、要 skipped、要 clarify，出错就该红着脸报出来。
    这个函数是给检索用的：**出错必须原样放行**。

    为什么必须吞异常（不是偷懒）
    ----------------------------
    词表是组员每天在改的文件。改坏一次（删列、改名、编码搞错）`load_vocab` 就会
    抛 `ValueError`。预览接口挂掉是好事——谁改坏的谁立刻看见；但聊天不能因此整条
    链路挂掉。换词是**增益**，不是聊天和检索的前置条件。

    返回值不带 start/end
    --------------------
    传进来的 text 是 `rewrite_query` 的产物（可能已被 LLM 补全指代），不是用户原话，
    偏移在原话上没有意义。要做高亮走 preview()，那里的偏移是在 original 上的。
    """
    try:
        vocab, _ = get_vocab()
        adj, _ = get_adjudication()
        blocked = blocked_for_query(adj, text) if adj else set()
        r = match(text, vocab, global_block=blocked)
        return r.retrieval_text, [
            {"raw": h.raw, "canonical": h.canonical} for h in r.hits
        ]
    except Exception as exc:  # noqa: BLE001 —— 见上：换词失败绝不能让检索挂掉
        # 但要留下痕迹：静默降级等于"接了跟没接一样，却没人知道"。
        # 只报第一次，避免每句话刷一行日志把真问题淹掉。
        global _DEGRADE_REPORTED
        if not _DEGRADE_REPORTED:
            _DEGRADE_REPORTED = True
            logging.warning("[检索词增强] 词表加载失败，检索已降级为不换词：%s", exc)
        return text, []
