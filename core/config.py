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
RATE_PER_MINUTE = int(os.getenv("RATE_PER_MINUTE", "20"))
# 每个用户每日 slices（embedding 条数）上限；0 = 不限
DAILY_EMBED_LIMIT = int(os.getenv("DAILY_EMBED_LIMIT", "3000"))
# 每个用户每日问答次数上限；0 = 不限
DAILY_ASK_LIMIT = int(os.getenv("DAILY_ASK_LIMIT", "200"))

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
