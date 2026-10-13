# -*- coding: utf-8 -*-
"""集中配置管理。

所有 API Key、地址、路径、默认参数统一在此读取，业务代码禁止硬编码。
密钥一律来自 .env（已被 .gitignore 忽略，严禁提交）。
"""

import os

from dotenv import load_dotenv

# ---------- 路径预处理：基于 __file__ 算绝对路径 ----------
# 无论从哪个 cwd 启动，都能正确定位项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT_ROOT)  # 切到根目录，让 .env / data / chroma_db 相对路径全部正确

# 从项目根加载 .env
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

# ---------- 大模型（DeepSeek Chat）----------
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# ---------- Embedding（向量化）----------
# DeepSeek 不提供 embedding 接口，必须单独配置（阿里百炼等 OpenAI 兼容服务）
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "")
EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-v3")

# ---------- 视觉模型（图片理解，复用阿里百炼 DashScope）----------
# DashScope 兼容 OpenAI 接口，qwen-vl-plus 支持图片理解
VISION_API_KEY = os.getenv("VISION_API_KEY", EMBEDDING_API_KEY)
VISION_BASE_URL = os.getenv("VISION_BASE_URL", EMBEDDING_BASE_URL)
VISION_MODEL = os.getenv("VISION_MODEL", "qwen-vl-plus")

# ---------- 路径 ----------
DATA_DIR = os.getenv("DATA_DIR", os.path.join(PROJECT_ROOT, "data"))
PERSIST_DIR = os.getenv("PERSIST_DIR", os.path.join(PROJECT_ROOT, "chroma_db"))
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", os.path.join(PROJECT_ROOT, "downloads"))

# ---------- 默认参数 ----------
CHUNK_SIZE_DEFAULT = int(os.getenv("CHUNK_SIZE", "500"))
CHUNK_OVERLAP_DEFAULT = int(os.getenv("CHUNK_OVERLAP", "50"))
TOP_K_DEFAULT = int(os.getenv("TOP_K", "3"))

# Embedding 接口单次批量上限（阿里百炼为 10）
EMBED_BATCH_SIZE = int(os.getenv("EMBED_BATCH_SIZE", "10"))
# 入库时并发发出的 embedding 批次数（每批仍是 EMBED_BATCH_SIZE 条）。
# 瓶颈在网络往返，串行等 N 批 = N 个 RTT 叠加；并发 4 基本能把 RTT 藏起来。
# 调太高会撞服务端限流（429），一般 3~4 足够；1 = 退回串行。
EMBED_CONCURRENCY = int(os.getenv("EMBED_CONCURRENCY", "4"))

# ---------- 网络超时与重试（防单次请求无限挂起）----------
# 单次 LLM 请求的超时秒数。流式下这是「字节间隔」超时：只要还在出字就不会触发，
# 只有整段卡死（服务端长时间不吐字节）才中断。默认 60s 够宽松，不会误杀长回答。
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "60"))
# 失败自动重试次数（含 429 / 5xx / 超时）。重试会重复计费，别调太大。
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))
# 向量化请求超时：embedding 是批量小请求，短超时即可。
EMBED_TIMEOUT = float(os.getenv("EMBED_TIMEOUT", "30"))
EMBED_MAX_RETRIES = int(os.getenv("EMBED_MAX_RETRIES", "2"))

# ---------- 检索策略（候选池 → 离群过滤 → 多样性挑选）----------
# 先多取几倍候选再挑，否则 top_k 名额会被相邻重复切片占满
RETRIEVE_FETCH_MULT = int(os.getenv("RETRIEVE_FETCH_MULT", "3"))
# 候选池下限：top_k 设得小时也保证有得挑
RETRIEVE_MIN_FETCH = int(os.getenv("RETRIEVE_MIN_FETCH", "12"))
# MMR 相关度权重：1 = 完全按相关度排（等同旧行为），越小越强调多样性
RETRIEVE_MMR_LAMBDA = float(os.getenv("RETRIEVE_MMR_LAMBDA", "0.65"))
# 余弦相似度下限：低于它当不相关直接丢。0 = 不做离群过滤
# 0.25 是刻意留宽的：宁可多带一条弱的，也别把能用的片段误杀。
# 觉得回答还是太"硬答"就往 0.4 调；觉得资料太少就调回 0.2 或 0。
RETRIEVE_MIN_SIM = float(os.getenv("RETRIEVE_MIN_SIM", "0.25"))

# ---------- 可信度（P0：让模型"敢说知识库里没有"）----------
# ⚠️ 和上面的 RETRIEVE_MIN_SIM 是**两件事**，别合并成一个：
#   RETRIEVE_MIN_SIM 决定「这段资料丢不丢」——丢完一条不剩时会强留最相关那条；
#   RETRIEVE_LOW_SIM 决定「留下来的这段够不够格当依据」——不丢，但要在回答里示弱。
# 用户调研（56 份）里「答案不准还装自信」占 83.9%，是压倒性第一担忧，
# 这个阈值就是拿来对冲它的：相似度低于它 = 没找到足够依据。
#
# 为什么 0.45：同一份资料里的原文提问通常能到 0.6+，换个说法问大约 0.5，
# 只是"用词沾边但讲的是另一件事"落在 0.3 附近。0.45 卡在中间，
# 会漏判一部分边缘情况，但宁可多示弱一次，也别再装一次自信。
RETRIEVE_LOW_SIM = float(os.getenv("RETRIEVE_LOW_SIM", "0.45"))

# 低置信提示开关。关掉后检索链路行为完全回到改造前（不示弱、不追加提示），
# 留着它是为了出问题时能一键回退，不用回滚代码。
GROUNDING_NOTICE = os.getenv("GROUNDING_NOTICE", "1") not in ("0", "false", "False")

# ---------- 多用户 / 认证 ----------
# 会话 token 的签发密钥。不配的话会自动在 data/.auth_secret 生成并持久化，
# 好处是重启不丢登录态；坏处是迁移机器时要把这个文件一起带走，否则所有人掉线一次。
AUTH_SECRET = os.getenv("AUTH_SECRET", "")
_AUTH_SECRET_FILE = os.path.join(DATA_DIR, ".auth_secret")
# 登录态有效期（天）
AUTH_TTL_DAYS = int(os.getenv("AUTH_TTL_DAYS", "7"))
# 是否开放自助注册。演示期默认开着；正式用建议关掉并改成邀请码
ALLOW_SIGNUP = os.getenv("ALLOW_SIGNUP", "1").strip().lower() in ("1", "true", "yes")
# 口令散列轮数（PBKDF2-SHA256）。机器好可以调高，反之别低于 10 万
AUTH_PBKDF2_ROUNDS = int(os.getenv("AUTH_PBKDF2_ROUNDS", "200000"))

# 迁移账号：历史数据（改造前建的会话 / 知识库）先锁在这个名下，谁都看不到，
# 由 tools/migrate_owner.py 明确指派给某个真人账号后才恢复可用。
# 这是刻意的"默认拒绝"——宁可暂时看不见，也不能让第一个登录的人顺手认领别人的数据。
LEGACY_OWNER = "legacy"

# ---------- 配额（防止别人用你的界面烧你的额度）----------
# 每个用户每分钟最多多少次问答 / 建库类写操作；0 = 不限
RATE_PER_MINUTE = int(os.getenv("RATE_PER_MINUTE", "10"))
# 每个用户每日 slices（embedding 条数）上限；0 = 不限
DAILY_EMBED_LIMIT = int(os.getenv("DAILY_EMBED_LIMIT", "1000"))
# 每个用户每日问答次数上限；0 = 不限
DAILY_ASK_LIMIT = int(os.getenv("DAILY_ASK_LIMIT", "60"))
# 每个用户每日「自动调用」次数上限（定时委托投递 + 每日总结生成，kind="auto"）；0 = 不限。
# DAILY_ASK_LIMIT 挡的是用户主动提问，无人值守的定时任务完全在它之外——
# 那条链路没人盯着，一旦写错（比如 is_due 判断反了）就是静默烧钱，所以单独卡一道。
# 默认 120 = 3 人 ×（5 委托 + 1 总结）× 重试与手动补生成余量（见 docs/接口文档.html 调度节）。
DAILY_AUTO_LIMIT = int(os.getenv("DAILY_AUTO_LIMIT", "120"))

# ---------- 定时任务（独立 worker 进程）----------
# 调度与解释统一用这个时区。禁止裸 datetime.now()——容器镜像的 TZ 常常是 UTC，
# 一旦漏带时区，「早上七点」会整体偏移 8 小时且不报错，只会安静地错。
SCHEDULER_TZ = os.getenv("SCHEDULER_TZ", "Asia/Shanghai")
# 每日总结的生成时刻（北京时间）。默认 23:30：留出当晚补记的时间，
# 次日跑到点时昨天的会话已经全部落盘。写进 schedule_jobs.cron 的初始值，
# 之后改周期以表为准（改表不用改代码、不用发版）。
REPORT_HOUR = int(os.getenv("REPORT_HOUR", "23"))
REPORT_MINUTE = int(os.getenv("REPORT_MINUTE", "30"))

# 管理员用户名（逗号分隔）。管理员不受配额与每分钟限流约束，相当于主账号。
# 默认把项目作者账号设为管理员；其他部署在 .env 里改成自己的用户名即可
# （例如 ADMIN_USERNAMES=admin,alice）。名单为空则没有管理员。
ADMIN_USERNAMES = [x.strip() for x in os.getenv("ADMIN_USERNAMES", "苞米呀").split(",") if x.strip()]

# ---------- 可选：天气工具 ----------
AMAP_API_KEY = os.getenv("AMAP_API_KEY", "")

# ---------- 可选：联网搜索备选 ----------
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")


def validate() -> list[str]:
    """启动前校验关键配置，返回缺失项的中文提示列表。"""
    missing = []
    if not DEEPSEEK_API_KEY:
        missing.append("缺少 DEEPSEEK_API_KEY（大模型对话密钥）")
    if not EMBEDDING_API_KEY:
        missing.append("缺少 EMBEDDING_API_KEY（向量化服务密钥）")
    if not EMBEDDING_BASE_URL:
        missing.append("缺少 EMBEDDING_BASE_URL（向量化服务地址）")
    return missing
