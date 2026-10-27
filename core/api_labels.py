"""接口的中文业务名 —— 看板「接口排行」以这个为准。

**为什么要有这个文件**：`/api/kb/{id}/rebuild` 这种路由模板对开发者有意义，
对老师 / 评委没有 —— 他们要看到的是「知识库 · 重建索引」，一眼知道这是产品
的哪块能力被用了多少次。展示层做字符串替换会把这件事散到前端各处，
所以单一来源放在这里，前后端都不许自己拼中文。

**约定**：
- 标签形如 `模块 · 动作`，模块用产品里用户看得到的叫法（对话 / 知识库 / 打卡），
  不用技术分区名（kb / delegate / hybrid）。
- 参数段一律归一成 `{}` 再匹配，路由改参数名（`{sid}` → `{id}`）不会失效。
- 尾斜杠也归一掉 —— 项目里 `/api/auth/me/` 与 `/api/sessions` 两种风格并存。

**兜底**：表里查不到时按一级模块给个泛称（`知识库 · 其他操作`）。
兜底**不是合格状态** —— `tests/test_dashboard.py` 有一条断言要求所有注册路由
都能在表里精确命中，新加接口忘了登记会直接红。
"""

from __future__ import annotations

import re

# 一级模块名（路径第二段 → 用户视角的叫法）
_MODULE_NAMES = {
    "admin": "看板",
    "agent": "智能体",
    "auth": "账号",
    "cet": "四六级",
    "chat": "对话",
    "checkin": "打卡",
    "config": "设置",
    "delegate": "每日委托",
    "feedback": "意见反馈",
    "geo": "定位",
    "hybrid": "工作流",
    "kb": "知识库",
    "metrics": "监控",
    "path": "学习路径",
    "profile": "个人画像",
    "prompt-evo": "提示词进化",
    "query": "检索优化",
    "report": "日报",
    "schedule": "定时任务",
    "sessions": "会话",
    "templates": "模板",
    "usage": "用量",
    "user-feedback": "意见反馈",
}

_VERB_FALLBACK = {
    "GET": "读取",
    "POST": "提交",
    "PUT": "保存",
    "PATCH": "更新",
    "DELETE": "删除",
}

# (方法, 归一化路径) → 中文名。键路径**不带尾斜杠、参数写作 {}**。
_LABELS: dict[tuple[str, str], str] = {
    # ---------- 看板 ----------
    ("GET", "/api/admin/overview"): "看板 · 今日总览",
    ("GET", "/api/admin/apis"): "看板 · 接口排行",
    ("GET", "/api/admin/dict"): "看板 · 指标说明",
    ("GET", "/api/admin/feedback"): "看板 · 反馈列表",
    ("PATCH", "/api/admin/feedback/{}"): "看板 · 处理反馈",
    # ---------- 账号 ----------
    ("GET", "/api/auth/config"): "账号 · 登录方式",
    ("POST", "/api/auth/login"): "账号 · 登录",
    ("POST", "/api/auth/logout"): "账号 · 退出登录",
    ("GET", "/api/auth/me"): "账号 · 当前用户",
    ("POST", "/api/auth/register"): "账号 · 注册",
    # ---------- 对话 ----------
    ("POST", "/api/chat/stream"): "对话 · 提问（逐字回复）",
    ("POST", "/api/chat/upload"): "对话 · 上传附件",
    ("GET", "/api/sessions"): "对话 · 会话列表",
    ("POST", "/api/sessions"): "对话 · 新建会话",
    ("GET", "/api/sessions/{}"): "对话 · 打开会话",
    ("PATCH", "/api/sessions/{}"): "对话 · 会话改名",
    ("DELETE", "/api/sessions/{}"): "对话 · 删除会话",
    ("DELETE", "/api/sessions/{}/messages/{}"): "对话 · 删除单条消息",
    # ---------- 知识库 ----------
    ("GET", "/api/kb"): "知识库 · 列表",
    ("POST", "/api/kb/create"): "知识库 · 新建",
    ("GET", "/api/kb/download"): "知识库 · 导出",
    ("POST", "/api/kb/import-search"): "知识库 · 联网搜索导入",
    ("GET", "/api/kb/{}"): "知识库 · 详情",
    ("DELETE", "/api/kb/{}"): "知识库 · 删除",
    ("GET", "/api/kb/{}/files"): "知识库 · 文件列表",
    ("DELETE", "/api/kb/{}/files"): "知识库 · 删除文件",
    ("POST", "/api/kb/{}/upload"): "知识库 · 上传文件",
    ("POST", "/api/kb/{}/import-url"): "知识库 · 从网址导入",
    ("POST", "/api/kb/{}/import-batch"): "知识库 · 批量导入",
    ("GET", "/api/kb/{}/file-content"): "知识库 · 预览文件原文",
    ("POST", "/api/kb/{}/rebuild"): "知识库 · 重建索引",
    ("POST", "/api/kb/{}/query"): "知识库 · 检索提问",
    # ---------- 每日委托 / 定时 ----------
    ("GET", "/api/delegate"): "每日委托 · 列表",
    ("POST", "/api/delegate"): "每日委托 · 新建",
    ("GET", "/api/delegate/{}"): "每日委托 · 详情",
    ("PATCH", "/api/delegate/{}"): "每日委托 · 修改设置",
    ("DELETE", "/api/delegate/{}"): "每日委托 · 删除",
    ("POST", "/api/delegate/{}/run"): "每日委托 · 立即执行",
    ("GET", "/api/delegate/{}/runs"): "每日委托 · 执行记录",
    ("GET", "/api/schedule/jobs"): "定时任务 · 任务列表",
    ("GET", "/api/schedule/runs"): "定时任务 · 执行记录",
    ("GET", "/api/schedule/health"): "定时任务 · 健康检查",
    ("POST", "/api/schedule/run-now"): "定时任务 · 立即执行",
    ("POST", "/api/schedule/retry"): "定时任务 · 重试失败任务",
    # ---------- 打卡 / 日报 ----------
    ("GET", "/api/checkin/streak"): "打卡 · 连续天数",
    ("GET", "/api/checkin/history"): "打卡 · 历史记录",
    ("POST", "/api/checkin/generate"): "打卡 · 生成内容",
    ("POST", "/api/checkin/share"): "打卡 · 分享",
    ("GET", "/api/report/today"): "日报 · 今天",
    ("GET", "/api/report/{}"): "日报 · 详情",
    ("POST", "/api/report/generate"): "日报 · 生成",
    # ---------- 四六级 ----------
    ("GET", "/api/cet/overview"): "四六级 · 学习概览",
    ("POST", "/api/cet/plan"): "四六级 · 生成备考计划",
    ("GET", "/api/cet/scores"): "四六级 · 成绩列表",
    ("POST", "/api/cet/scores"): "四六级 · 记录一次成绩",
    ("DELETE", "/api/cet/scores/{}"): "四六级 · 删除成绩",
    ("PATCH", "/api/cet/task"): "四六级 · 勾选今日任务",
    ("GET", "/api/cet/vocab"): "四六级 · 单词表",
    ("POST", "/api/cet/vocab"): "四六级 · 添加单词",
    ("POST", "/api/cet/vocab/review"): "四六级 · 单词复习",
    # ---------- 学习路径 / 画像 ----------
    ("GET", "/api/path/overview"): "学习路径 · 概览",
    ("GET", "/api/profile"): "个人画像 · 快照",
    ("GET", "/api/profile/tags"): "个人画像 · 标签体系",
    ("PUT", "/api/profile/tags"): "个人画像 · 保存标签",
    ("GET", "/api/templates"): "模板 · 列表",
    ("POST", "/api/templates/{}/apply"): "模板 · 套用模板",
    # ---------- 工作流 ----------
    ("GET", "/api/hybrid/workflows"): "工作流 · 列表",
    ("POST", "/api/hybrid/workflows"): "工作流 · 新建",
    ("GET", "/api/hybrid/workflows/{}"): "工作流 · 详情",
    ("PUT", "/api/hybrid/workflows/{}"): "工作流 · 修改",
    ("DELETE", "/api/hybrid/workflows/{}"): "工作流 · 删除",
    ("GET", "/api/hybrid/rules"): "工作流 · 触发规则",
    ("PUT", "/api/hybrid/rules/{}"): "工作流 · 修改规则",
    ("DELETE", "/api/hybrid/rules/{}"): "工作流 · 删除规则",
    ("GET", "/api/hybrid/runs"): "工作流 · 运行记录",
    ("GET", "/api/hybrid/runs/{}"): "工作流 · 运行详情",
    ("GET", "/api/hybrid/runs/{}/logs"): "工作流 · 运行日志",
    ("POST", "/api/hybrid/runs/{}/resume"): "工作流 · 继续运行",
    ("POST", "/api/hybrid/runs/{}/switch"): "工作流 · 切换模式",
    # ---------- 检索优化 ----------
    ("POST", "/api/query/enhance"): "检索优化 · 术语预览",
    ("GET", "/api/query/vocab"): "检索优化 · 词表版本",
    # ---------- 智能体 / 提示词 ----------
    ("GET", "/api/agent/tools"): "智能体 · 工具清单",
    ("POST", "/api/agent/tools/discover"): "智能体 · 发现新工具",
    ("GET", "/api/prompt-evo/status"): "提示词进化 · 当前状态",
    ("GET", "/api/prompt-evo/config"): "提示词进化 · 配置",
    ("GET", "/api/prompt-evo/variants"): "提示词进化 · 候选版本",
    ("GET", "/api/prompt-evo/variants/{}"): "提示词进化 · 候选详情",
    ("POST", "/api/prompt-evo/variants/{}/promote"): "提示词进化 · 采纳候选",
    ("POST", "/api/prompt-evo/run"): "提示词进化 · 跑一轮进化",
    ("POST", "/api/prompt-evo/rollback"): "提示词进化 · 回滚",
    ("GET", "/api/prompt-evo/replay-sets"): "提示词进化 · 回归测试集",
    # ---------- 反馈 / 用量 / 杂项 ----------
    ("POST", "/api/feedback"): "意见反馈 · 提交（旧版）",
    ("GET", "/api/feedback/{}"): "意见反馈 · 详情（旧版）",
    ("POST", "/api/user-feedback"): "意见反馈 · 提交",
    ("GET", "/api/user-feedback/mine"): "意见反馈 · 我的反馈",
    ("GET", "/api/user-feedback/dict"): "意见反馈 · 分类字典",
    ("GET", "/api/usage/summary"): "用量 · 今日额度",
    ("GET", "/api/config/defaults"): "设置 · 默认配置",
    ("GET", "/api/geo/locate"): "定位 · 自动识别城市",
    ("GET", "/api/metrics"): "监控 · 指标页",
    ("GET", "/api/metrics/json"): "监控 · 指标数据",
}

_PARAM_RE = re.compile(r"\{[^}]*\}")


def _normalize(path: str) -> str:
    """参数段归一成 `{}`，去掉尾斜杠 —— 只用于查表，不用于展示。"""
    p = _PARAM_RE.sub("{}", str(path or "").strip())
    if len(p) > 1 and p.endswith("/"):
        p = p.rstrip("/")
    return p


def _module_of(path: str) -> str | None:
    parts = [x for x in _normalize(path).split("/") if x]
    if len(parts) >= 2 and parts[0] == "api":
        return _MODULE_NAMES.get(parts[1])
    return None


def label_for(method: str, path: str) -> str:
    """接口的中文业务名。查不到返回 `模块 · 其他操作`（这种状态会被测试拦下）。"""
    m = str(method or "").upper()
    exact = _LABELS.get((m, _normalize(path)))
    if exact:
        return exact
    module = _module_of(path) or "系统"
    return f"{module} · 其他{_VERB_FALLBACK.get(m, '调用')}"


def is_known(method: str, path: str) -> bool:
    """是否在表里精确登记过（供测试断言，不用于展示）。"""
    return (str(method or "").upper(), _normalize(path)) in _LABELS


def known_count() -> int:
    return len(_LABELS)
