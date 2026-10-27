from __future__ import annotations

"""SQLite 多会话管理。

设计：
  三张表：
    conversations  —— 会话元数据（归属 / 标题 / 绑定知识库 / 创建时间）
    messages       —— 消息历史（role / content / 时间）
    feedbacks      —— 消息赞踩

  所有 SQL 用参数化查询防注入；窗口截断：加载历史时只取最近
  MAX_HISTORY_TURNS 轮（默认 30 轮 = 60 条消息），避免长会话爆 token。

归属隔离（多用户改造 P1）
------------------------
**每一个函数都把 owner 作为第一个参数**，没有默认值。

这是刻意的：宁可让漏改的调用点抛 TypeError，也不要让它悄悄查到别人的数据。
如果给 owner 一个默认 None 再在 SQL 里写 `WHERE owner_id = ?`，漏传的场景
会退化成"查不到"（看起来正常）；写成必需参数，漏传直接启动失败。

老数据的 owner_id 是 NULL，`owner_id = ?` 对 NULL 永远不成立，
所以改造前的历史会话对任何账号都不可见——这是"默认拒绝"，
由 tools/migrate_owner.py 明确指派后才归某个真人所有。
"""

import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Literal

from core import config, tag_taxonomy

MAX_HISTORY_TURNS = 30  # 每个会话最多保留的对话轮数（1 轮 = user + assistant 两条）


# ---------- 数据库路径 ----------
def _db_path() -> str:
    """SQLite 文件路径（放在 PERSIST_DIR 同级）。

    纯计算、不做 IO：目录创建挪到 `_conn()` 建连前做。`_conn()` 每次查询都要取路径，
    此前顺带一次 `os.makedirs(exist_ok=True)`，等于每条 SQL 前都白做一次系统调用。
    测试 monkeypatch 本函数到临时目录时，建目录仍会跟着新路径走。
    """
    # 库路径覆盖：给"起真进程但用独立库"的场景用（tools/dashboard_e2e.py）。
    # 这是**测试缝**，不是配置项：默认不设，行为与只返回 PROJECT_ROOT 时完全一致。
    # 有它才能在没有生产库副本的机器上跑端到端，且不可能污染真实数据。
    override = os.getenv("AGENT_HUB_DB")
    if override:
        return override
    return os.path.join(config.PROJECT_ROOT, "data", "sessions.db")


# ---------- 连接管理 ----------
# 每次操作都新建 sqlite 连接，等于每次重开文件 + 重设 WAL；一次问答会开 6~7 个，
# 累计几毫秒、还都在请求线程里。改成按线程复用：连接只建一次、pragma 只设一次。
# 路径参与判断是因为测试会 monkeypatch _db_path 到临时目录——路径一变必须换连接，
# 否则会继续读写上一个库。
_LOCAL = threading.local()


def _conn() -> sqlite3.Connection:
    """取当前线程复用的 SQLite 连接（路径变了就重建）。"""
    path = _db_path()
    conn = getattr(_LOCAL, "conn", None)
    if conn is not None and getattr(_LOCAL, "path", None) == path:
        return conn
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    os.makedirs(
        os.path.dirname(path), exist_ok=True
    )  # 建连前确保目录存在（路径可能被测试改到临时目录）
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row  # 让 cursor 返回 dict-like 对象
    conn.execute("PRAGMA journal_mode=WAL")  # 写前日志，提升并发读性能
    conn.execute("PRAGMA synchronous=NORMAL")  # WAL 下不必每次 fsync，写更快
    conn.execute("PRAGMA busy_timeout=5000")  # 撞上并发写时等待而不是立刻报错
    _LOCAL.conn = conn
    _LOCAL.path = path
    return conn


@contextmanager
def _get_conn() -> None:
    """获取当前线程复用的 SQLite 连接（context manager）。"""
    conn = _conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _init_db() -> None:
    """首次运行时创建表（幂等）。

    新建库直接带 owner_id；老库靠后面的 ALTER 补列。
    两种路径得到同一套结构，省得迁移脚本和建表语句各写一遍。
    """
    with _get_conn() as conn:
        # 令牌吊销名单：JWT 是无状态的，退出登录 / 强制下线只能靠这份黑名单。
        # 只存 jti + 过期时间，过期行由 core/token_store.py 顺手清掉，
        # 所以这张表不会无限长。
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS revoked_tokens (
                jti TEXT PRIMARY KEY,
                exp INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_revoked_exp ON revoked_tokens(exp);
        """)
    with _get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id          TEXT PRIMARY KEY,
                owner_id    TEXT,
                title       TEXT NOT NULL,
                kb_id       TEXT,
                created_at  INTEGER NOT NULL,
                updated_at  INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_conv_updated ON conversations(updated_at DESC);

            CREATE TABLE IF NOT EXISTS messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                role            TEXT NOT NULL CHECK(role IN ('system','user','assistant')),
                content         TEXT NOT NULL,
                refs            TEXT,
                created_at      INTEGER NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, id);

            CREATE TABLE IF NOT EXISTS feedbacks (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                message_id      INTEGER,
                rating          TEXT NOT NULL CHECK(rating IN ('up','down')),
                comment         TEXT,
                created_at      INTEGER NOT NULL
            );
            -- 一条消息只保留最后一次反馈（点赞再点踩就是改，不是追加）
            CREATE UNIQUE INDEX IF NOT EXISTS idx_fb_msg
                ON feedbacks(conversation_id, message_id)
                WHERE message_id IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_fb_conv ON feedbacks(conversation_id);

            -- 用户画像标签（方案 C · B 块）：沉淀"这个用户关心什么"，
            -- 供后续 C2 聚合 / D 检索偏置使用。weight 随命中次数累加。
            CREATE TABLE IF NOT EXISTS user_tags (
                owner_id    TEXT NOT NULL,
                tag         TEXT NOT NULL,
                weight      REAL NOT NULL DEFAULT 1.0,
                updated_at  INTEGER NOT NULL,
                PRIMARY KEY (owner_id, tag)
            );
            CREATE INDEX IF NOT EXISTS idx_usertag_owner ON user_tags(owner_id);

            -- 用户画像快照（方案 C · C2 块）：由 worker 定时聚合 user_tags 生成，
            -- 供前端展示「用户兴趣画像」与 D 块检索偏置读取。零 LLM 调用。
            CREATE TABLE IF NOT EXISTS user_profiles (
                owner_id    TEXT PRIMARY KEY,
                top_tags   TEXT NOT NULL,        -- JSON: [{"tag":..., "weight":...}, ...] 权重降序
                weight_sum REAL NOT NULL DEFAULT 0,
                tag_count  INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL
            );

            -- ========== Prompt 自进化（GEPA 式 · issue #12）==========
            -- ⑤ 存储层：每条提示词快照一行。version=0 是内置基线（不落库），
            -- 所以这里从 1 开始；同一 path 内 version 唯一。
            CREATE TABLE IF NOT EXISTS prompt_variants (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                path        TEXT NOT NULL,          -- 'agent' | 'rag_note_low' | 'rag_note_medium'
                version     INTEGER NOT NULL,
                parent_id   INTEGER,                -- 变异来源（基线用 NULL）
                diff_json   TEXT,                   -- 结构化 diff（可读可审计）
                prompt_text TEXT NOT NULL,          -- 全量快照：retired 也能一键回退
                metrics_json TEXT,                  -- {M1..M6, gate:{...}, pareto:bool}
                status      TEXT NOT NULL DEFAULT 'candidate',
                created_by  TEXT NOT NULL,          -- 'evolve:<run_id>' | 'manual:<uid>'
                created_at  INTEGER NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS ux_prompt_variant
                ON prompt_variants(path, version);
            CREATE INDEX IF NOT EXISTS idx_prompt_variant_status
                ON prompt_variants(path, status, id DESC);

            -- ③ 评估层明细：每条 case 的判分都留档，指标能复盘到样本。
            -- variant_id=0 表示内置基线（基线每次回放也存一份，否则"相对谁下降"不可查）。
            CREATE TABLE IF NOT EXISTS prompt_evals (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                path               TEXT NOT NULL,
                variant_id         INTEGER NOT NULL DEFAULT 0,
                replay_set_version INTEGER NOT NULL,
                per_case_json      TEXT NOT NULL,
                summary_json       TEXT NOT NULL,
                elapsed_ms         INTEGER NOT NULL DEFAULT 0,
                created_at         INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_prompt_eval
                ON prompt_evals(path, variant_id, replay_set_version);

            -- 回放集版本表 + 明细。只增不改：要改就升版本，老版本的样本原样留着，
            -- 这样两轮指标永远能对上"当时那批题"。
            CREATE TABLE IF NOT EXISTS prompt_replay_sets (
                path       TEXT NOT NULL,
                version    INTEGER NOT NULL,
                seed       INTEGER NOT NULL,
                size       INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                note       TEXT,
                PRIMARY KEY (path, version)
            );
            CREATE TABLE IF NOT EXISTS prompt_replay_items (
                path        TEXT NOT NULL,
                set_version INTEGER NOT NULL,
                case_key    TEXT NOT NULL,
                question    TEXT NOT NULL,          -- 已脱敏
                gold        TEXT,
                tags        TEXT,                   -- 失败模式标签（逗号分隔）
                source      TEXT NOT NULL,          -- 'down' | 'up'
                meta_json   TEXT,
                created_at  INTEGER NOT NULL,
                PRIMARY KEY (path, set_version, case_key)
            );

            -- 用户**亲手勾选**的兴趣标签（与 user_tags 分工不同）：
            --   user_tags  = 权重事实（对话自动抽的 + 手选的，都只是权重，分不出谁是谁）
            --   user_tag_picks = "他明确声明过什么"，只用于前端回显"我选过哪些"，
            --                    以及让"取消勾选"能被记住（user_tags 里那条不删，
            --                    因为它可能同时是聊出来的证据）。
            -- 加这张表而不是给 user_tags 加 source 列：主键是 (owner_id, tag)，
            -- 同一个标签两种来源在物理上就只能存一行。
            CREATE TABLE IF NOT EXISTS user_tag_picks (
                owner_id   TEXT NOT NULL,
                tag        TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (owner_id, tag)
            );
            CREATE INDEX IF NOT EXISTS idx_tagpick_owner ON user_tag_picks(owner_id);
        """)

        # 迁移：老库补 refs 列（已存在则忽略）
        try:
            conn.execute("ALTER TABLE messages ADD COLUMN refs TEXT")
        except Exception:
            pass

        # 迁移（审计 A1）：老库补 truncated 列。
        # 1 = 这条回答因 max_tokens / 上下文上限被截断（上游 finish_reason=length）。
        # 为什么要落库而不是只在 SSE 里提示一下：这是唯一能回答
        # 「我们有多少回答其实是残缺的」的数据。塞进 refs 会污染来源引用，
        # 另建表又不值得——一个 BOOLEAN 语义的整数列，成本一行。
        try:
            conn.execute("ALTER TABLE messages ADD COLUMN truncated INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass

        # 迁移：老库补 owner_id 列。旧行留 NULL = 谁都不属于，默认不可见
        try:
            conn.execute("ALTER TABLE conversations ADD COLUMN owner_id TEXT")
        except Exception:
            pass
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_conv_owner ON conversations(owner_id, updated_at DESC)"
        )

        # 迁移（P0-2 上下文压缩）：老库补三个字段。
        #   compressed_summary     当前压缩块（含 [COMPRESSED-n] 分段标记）
        #   compressed_summary_prev 上一版压缩块（L1 安全阀回退用；方案文档指出
        #                           单 TEXT 字段无法回退，故显式存一份）
        #   compressed_upto        已压缩到的 message id；build 时以此过滤原文，
        #                           避免"压缩块 + 原文"重复进上下文
        for col, decl in (
            ("compressed_summary", "TEXT"),
            ("compressed_summary_prev", "TEXT"),
            ("compressed_upto", "INTEGER"),
        ):
            try:
                conn.execute(f"ALTER TABLE conversations ADD COLUMN {col} {decl}")
            except Exception:
                pass  # 已存在


# 模块加载时自动建表
_init_db()


def _owned(owner: str, sid: str) -> bool:
    """会话 sid 是否属于 owner。老数据 owner_id 为 NULL，一律判 False。"""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM conversations WHERE id = ? AND owner_id = ?", (sid, owner)
        ).fetchone()
        return row is not None


# ==================== 会话 CRUD ====================


def create_conversation(owner: str, title: str = "新对话", kb_id: str | None = None) -> dict:
    """新建会话，返回会话 dict。"""
    sid = uuid.uuid4().hex[:12]  # 短 ID，前端好展示
    now = int(time.time())
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO conversations (id, owner_id, title, kb_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sid, owner, title, kb_id, now, now),
        )
    return get_conversation(owner, sid)


def get_conversation(owner: str, sid: str) -> dict | None:
    """按 id 取会话；不存在**或不属于该用户**都返回 None。

    刻意不区分这两种情况：区分了就等于告诉调用方"这个 id 存在但没权限"，
    那是一条可以用来枚举别人会话 id 的信息。
    """
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT id, title, kb_id, created_at, updated_at FROM conversations "
            "WHERE id = ? AND owner_id = ?",
            (sid, owner),
        ).fetchone()
        return dict(row) if row else None


def list_conversations(owner: str) -> list[dict]:
    """列出该用户的会话（按 updated_at 倒序）。老数据 owner_id 为 NULL，不会混进来。"""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, kb_id, created_at, updated_at FROM conversations "
            "WHERE owner_id = ? ORDER BY updated_at DESC",
            (owner,),
        ).fetchall()
        return [dict(r) for r in rows]


def rename_conversation(owner: str, sid: str, title: str) -> bool:
    """重命名会话，返回是否成功。"""
    now = int(time.time())
    with _get_conn() as conn:
        cur = conn.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ? AND owner_id = ?",
            (title, now, sid, owner),
        )
        return cur.rowcount > 0


def bind_kb(owner: str, sid: str, kb_id: str | None) -> bool:
    """绑定或解绑知识库（kb_id=None 表示解绑），返回是否成功。"""
    now = int(time.time())
    with _get_conn() as conn:
        cur = conn.execute(
            "UPDATE conversations SET kb_id = ?, updated_at = ? WHERE id = ? AND owner_id = ?",
            (kb_id, now, sid, owner),
        )
        return cur.rowcount > 0


def delete_conversation(owner: str, sid: str) -> bool:
    """删除会话及其全部消息，返回是否成功。"""
    with _get_conn() as conn:
        # ON DELETE CASCADE 会自动删 messages，但保险起见先删消息
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (sid,))
        conn.execute("DELETE FROM feedbacks WHERE conversation_id = ?", (sid,))
        cur = conn.execute("DELETE FROM conversations WHERE id = ? AND owner_id = ?", (sid, owner))
        return cur.rowcount > 0


def claim_orphan_conversations(owner: str) -> int:
    """把改造前遗留的（owner_id 为 NULL 的）会话指派给某个账号。

    必须显式调用（tools/migrate_owner.py），不能自动执行：
    否则第一个注册的人就把别人的历史数据认领走了。返回实际认领条数。
    """
    with _get_conn() as conn:
        cur = conn.execute("UPDATE conversations SET owner_id = ? WHERE owner_id IS NULL", (owner,))
        return cur.rowcount


# ==================== 消息管理 ====================


class NotOwned(Exception):
    """会话不属于当前用户。

    正常流程下 API 层已经用 get_conversation(owner, sid) 验过一次，
    这里再验一遍是"纵深防御"：消息写入比读取更危险，
    宁可多跑一次带索引的 EXISTS（约 0.1ms），也不要把写权限寄托在调用方记得校验。
    """


def add_message(
    owner: str,
    sid: str,
    role: Literal["user", "assistant", "system"],
    content: str,
    refs: str = "",
    truncated: bool = False,
) -> int:
    """追加一条消息，返回 message id。同时更新会话的 updated_at。

    truncated=True 表示这条回答被长度上限截断了（见 core/session.py 的迁移注释与
    审计报告 A1）。默认 False，所有既有调用方不需要改。
    """
    if not _owned(owner, sid):
        raise NotOwned(sid)
    now = int(time.time())
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content, refs, truncated, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sid, role, content, refs, 1 if truncated else 0, now),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, sid),
        )
        # 取自增 id
        row = conn.execute("SELECT last_insert_rowid() AS id").fetchone()
        return row["id"]


# ==================== 用户画像标签（方案 C · B 块）====================


def add_user_tags(owner: str, tags: list[str], delta: float = 1.0) -> None:
    """累加用户的画像标签权重（同一标签多次出现 → 权重 +delta）。

    调用方（api/chat.py 后台钩子）只传词表内的标签，这里再兜底一次去重与清洗。
    失败静默：画像只是辅助，绝不能因为写标签失败而把正常问答搞崩。

    ★ 落库前先过 `tag_taxonomy.normalize_tag`：把"CET6 / 六级 / 英语四六级"
      统一成"英语四六级"。**这里只归一、不过滤** —— 词表外的主题词（"数学""英语"）
      原样保留，因为检索偏置那边也会写词表外的主题；硬闸门在抽标签那一侧。
    """
    if not owner or not tags:
        return
    now = int(time.time())
    clean: list[str] = []
    seen: set[str] = set()
    for t in tags:
        t = tag_taxonomy.normalize_tag(t).strip("|").strip()
        if t and t not in seen:
            seen.add(t)
            clean.append(t)
    if not clean:
        return
    try:
        with _get_conn() as conn:
            for t in clean:
                conn.execute(
                    "INSERT INTO user_tags (owner_id, tag, weight, updated_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(owner_id, tag) DO UPDATE SET weight = weight + ?, updated_at = ?",
                    (owner, t, delta, now, delta, now),
                )
    except Exception:
        pass


# 手选标签的权重：一次声明 ≈ 3 次对话证据。
# 太低会被聊天的噪声盖过，太高则用户改主意后旧标签赖着不走。
PICK_WEIGHT = 3.0
# 单次手选上限。汇总文档的建议是"选 1~3 个"，留到 6 是给跨方向的同学余地，
# 再多就变成"全选"，推荐会退化成"什么都有"，等于没选。
MAX_PICKS = 6


def pick_user_tags(owner: str, tags: list[str], weight: float = PICK_WEIGHT) -> list[str]:
    """保存用户**手选**的兴趣标签（覆盖式），返回实际生效的标签名列表。

    两个必须满足的性质：

    1. **幂等**：同一个标签反复提交不会把权重越堆越高。做法是
       `ON CONFLICT DO NOTHING` —— 已存在的标签（不管是手选的还是聊出来的）
       权重一律不动，只补新标签。
    2. **可取消**：这次没选的、上次手选过的，从 picks 表里删掉（前端不再回显），
       但 `user_tags` 里那条记录**保留** —— 它可能同时是"聊出来"的证据，
       删了等于替用户抹掉他真实问过的问题。

    词表外的标签直接丢弃：手选只发生在 30 条定稿清单里
    （前端渲染的就是这份清单，脏值必然来自被改过的请求）。
    """
    if not owner:
        return []
    chosen = tag_taxonomy.filter_known_tags(tags, limit=MAX_PICKS)
    now = int(time.time())
    try:
        with _get_conn() as conn:
            for t in chosen:
                conn.execute(
                    "INSERT INTO user_tags (owner_id, tag, weight, updated_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(owner_id, tag) DO UPDATE SET updated_at = ?",
                    (owner, t, weight, now, now),
                )
            # 覆盖式：先清掉旧的手选记录，再写入本次的
            conn.execute("DELETE FROM user_tag_picks WHERE owner_id = ?", (owner,))
            # created_at 加序号偏移：同一秒内写入的多条 created_at 全等，
            # 排序会退化成按 tag 的字节序，回显顺序就和用户勾选顺序对不上了
            for i, t in enumerate(chosen):
                conn.execute(
                    "INSERT INTO user_tag_picks (owner_id, tag, created_at) VALUES (?, ?, ?)",
                    (owner, t, now + i),
                )
    except Exception:
        return []
    return chosen


def get_user_tag_picks(owner: str) -> list[str]:
    """取用户手选过的标签（前端回显已选状态用）。"""
    if not owner:
        return []
    try:
        with _get_conn() as conn:
            rows = conn.execute(
                "SELECT tag FROM user_tag_picks WHERE owner_id = ? ORDER BY created_at ASC, tag ASC",
                (owner,),
            ).fetchall()
    except Exception:
        return []
    return [r["tag"] for r in rows]


def get_user_tags(owner: str, limit: int = 30) -> list[dict]:
    """取用户画像标签，按权重降序。"""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT tag, weight, updated_at FROM user_tags WHERE owner_id = ? "
            "ORDER BY weight DESC, updated_at DESC LIMIT ?",
            (owner, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ==================== 用户画像快照（方案 C · C2 块）====================


def get_owners_with_tags(limit: int | None = None) -> list[str]:
    """返回所有沉淀过画像标签的 owner（供 worker 枚举聚合对象）。"""
    with _get_conn() as conn:
        sql = "SELECT DISTINCT owner_id FROM user_tags"
        if limit:
            rows = conn.execute(sql + " LIMIT ?", (int(limit),)).fetchall()
        else:
            rows = conn.execute(sql).fetchall()
    return [r["owner_id"] for r in rows]


def save_user_profile(
    owner: str, top_tags: list[dict], weight_sum: float, tag_count: int, updated_at: int
) -> None:
    """upsert 用户画像快照（C2 聚合结果）。失败静默。"""
    if not owner:
        return
    try:
        import json

        with _get_conn() as conn:
            conn.execute(
                "INSERT INTO user_profiles (owner_id, top_tags, weight_sum, tag_count, updated_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(owner_id) DO UPDATE SET "
                "top_tags=excluded.top_tags, weight_sum=excluded.weight_sum, "
                "tag_count=excluded.tag_count, updated_at=excluded.updated_at",
                (
                    owner,
                    json.dumps(top_tags, ensure_ascii=False),
                    float(weight_sum),
                    int(tag_count),
                    int(updated_at),
                ),
            )
    except Exception:
        pass


def get_user_profile(owner: str) -> dict | None:
    """取用户画像快照；未聚合过返回 None。"""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT owner_id, top_tags, weight_sum, tag_count, updated_at "
            "FROM user_profiles WHERE owner_id = ?",
            (owner,),
        ).fetchone()
    if not row:
        return None
    import json

    return {
        "owner_id": row["owner_id"],
        "top_tags": json.loads(row["top_tags"]),
        "weight_sum": row["weight_sum"],
        "tag_count": row["tag_count"],
        "updated_at": row["updated_at"],
    }


def get_messages(
    owner: str,
    sid: str,
    limit_turns: int = MAX_HISTORY_TURNS,
    include_system: bool = True,
) -> list[dict]:
    """取会话消息历史（按时间正序）。不是自己的会话直接返回空列表。

    归属校验并进查询：JOIN conversations 后按 owner 过滤，一次查询同时完成
    "验归属 + 取消息"，省掉一次独立的 EXISTS。

    Args:
        owner: 会话归属用户 id
        sid: 会话 id
        limit_turns: 最多保留的对话轮数（每轮 = user + assistant），None 表示不限
        include_system: 是否包含 system 消息

    Returns:
        [{role, content, created_at}, ...]
    """
    cols = "m.id, m.role, m.content, m.refs, m.created_at"
    join = (
        "FROM messages m JOIN conversations c ON m.conversation_id = c.id "
        "WHERE m.conversation_id = ? AND c.owner_id = ?"
    )
    with _get_conn() as conn:
        if limit_turns and limit_turns > 0:
            # 只取尾部：长会话全量载入既慢又占内存，而窗口截断只要最近
            # limit_turns 轮。多留 10 行缓冲，避免 Agent 一轮产出多段 assistant
            # 时把窗口起点切掉；倒序取再翻转，等价于"全量后截断"。
            cap = limit_turns * 2 + 10
            rows = conn.execute(
                f"SELECT * FROM (SELECT {cols} {join} ORDER BY m.id DESC LIMIT ?) ORDER BY id ASC",
                (sid, owner, cap),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT {cols} {join} ORDER BY m.id ASC",
                (sid, owner),
            ).fetchall()

    msgs = [dict(r) for r in rows]
    if not msgs:
        return []

    # 窗口截断：只保留最近 limit_turns 轮
    if limit_turns and limit_turns > 0:
        # 从后往前数，找到最后 limit_turns 个 user 消息的位置
        user_indices = [i for i, m in enumerate(msgs) if m["role"] == "user"]
        if len(user_indices) > limit_turns:
            start = user_indices[-limit_turns]
            msgs = msgs[start:]

    # system 消息保留最靠前的一条（通常是 RAG 系统提示词）
    if not include_system:
        msgs = [m for m in msgs if m["role"] != "system"]

    return msgs


def count_messages(owner: str, sid: str) -> int:
    """会话消息总数。不是自己的会话返回 0。

    同 get_messages：归属校验并进 JOIN，省一次独立 EXISTS。
    """
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM messages m "
            "JOIN conversations c ON m.conversation_id = c.id "
            "WHERE m.conversation_id = ? AND c.owner_id = ?",
            (sid, owner),
        ).fetchone()
        return row["c"]


def get_message(owner: str, sid: str, msg_id: int) -> dict | None:
    """按 id 取单条消息。不是自己的会话返回 None。"""
    if not _owned(owner, sid):
        return None
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT id, role, content, refs, created_at FROM messages "
            "WHERE conversation_id = ? AND id = ?",
            (sid, msg_id),
        ).fetchone()
        return dict(row) if row else None


def find_turn_ids(owner: str, sid: str, msg_id: int) -> list[int]:
    """找到某条消息所在的完整「一轮对话」包含的所有消息 id。

    规则：
      - 若目标是 user 消息：包含该 user 及其后连续的所有 assistant 消息，直到下一条 user 前。
      - 若目标是 assistant 消息：包含其前最近一条 user 消息，
        以及该 user 与目标之间的所有 assistant。
    这样前端点 user 或 assistant 的删除，都能整轮移除。
    """
    if not _owned(owner, sid):
        return []
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT id, role FROM messages WHERE conversation_id = ? ORDER BY id ASC",
            (sid,),
        ).fetchall()

    msgs = [(r["id"], r["role"]) for r in rows]
    try:
        idx = next(i for i, (mid, _) in enumerate(msgs) if mid == msg_id)
    except StopIteration:
        return []

    role = msgs[idx][1]
    if role == "user":
        start = idx
    else:
        # assistant：向前找最近的 user
        start = next((i for i in range(idx, -1, -1) if msgs[i][1] == "user"), idx)

    ids = [msgs[start][0]]
    for i in range(start + 1, len(msgs)):
        if msgs[i][1] == "user":
            break
        ids.append(msgs[i][0])
    return ids


# ==================== 消息反馈（赞 / 踩）====================


def add_feedback(
    owner: str,
    sid: str,
    msg_id: int | None,
    rating: str,
    comment: str = "",
) -> bool:
    """记录一条消息反馈，同一条消息以最后一次为准（覆盖写）。

    Args:
        owner: 会话归属用户 id（不是自己的会话写不进去）
        sid: 会话 id
        msg_id: messages 表自增 id；None 表示前端还没拿到 id（流式未结束）
        rating: "up" 赞 / "down" 踩
        comment: 可选文字补充（踩的时候前端可以追问）

    Returns:
        是否写入成功
    """
    if rating not in ("up", "down"):
        return False
    if not _owned(owner, sid):
        return False
    now = int(time.time())
    with _get_conn() as conn:
        # 先撤掉这条消息上的旧反馈，再插新的：SQLITE 的 upsert 对
        # 部分唯一索引（message_id IS NOT NULL）支持不好，两步写最稳
        if msg_id is not None:
            conn.execute(
                "DELETE FROM feedbacks WHERE conversation_id = ? AND message_id = ?",
                (sid, msg_id),
            )
        conn.execute(
            "INSERT INTO feedbacks (conversation_id, message_id, rating, comment, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (sid, msg_id, rating, comment or None, now),
        )
        return True


def list_feedbacks(owner: str, sid: str) -> list[dict]:
    """取某会话的全部反馈（供日后复盘/导出用）。不是自己的会话返回空列表。"""
    if not _owned(owner, sid):
        return []
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT id, message_id, rating, comment, created_at FROM feedbacks "
            "WHERE conversation_id = ? ORDER BY id ASC",
            (sid,),
        ).fetchall()
        return [dict(r) for r in rows]


def feedback_stats(owner: str, recent_limit: int = 5) -> dict:
    """当前用户全部会话的赞踩聚合 + 最近几条原始反馈。

    只统计自己的会话（JOIN conversations 按 owner_id 过滤），
    和其它接口一样：别人的数据在这里等于不存在。
    返回 {up, down, total, recent: [{rating, comment, title, sid, created_at}]}。
    """
    with _get_conn() as conn:
        counts = {"up": 0, "down": 0}
        for r in conn.execute(
            "SELECT f.rating AS rating, COUNT(*) AS n FROM feedbacks f "
            "JOIN conversations c ON c.id = f.conversation_id "
            "WHERE c.owner_id = ? GROUP BY f.rating",
            (owner,),
        ).fetchall():
            counts[r["rating"]] = int(r["n"])
        rows = conn.execute(
            "SELECT f.rating AS rating, f.comment AS comment, f.created_at AS created_at, "
            "c.title AS title, c.id AS sid "
            "FROM feedbacks f JOIN conversations c ON c.id = f.conversation_id "
            "WHERE c.owner_id = ? ORDER BY f.id DESC LIMIT ?",
            (owner, max(1, min(int(recent_limit), 20))),
        ).fetchall()
    recent = [
        {
            "rating": r["rating"],
            "comment": r["comment"] or "",
            "title": r["title"],
            "sid": r["sid"],
            "created_at": int(r["created_at"]),
        }
        for r in rows
    ]
    up, down = counts["up"], counts["down"]
    return {"up": up, "down": down, "total": up + down, "recent": recent}


def delete_messages(owner: str, sid: str, msg_ids: list[int]) -> bool:
    """批量删除消息，返回是否实际删到了。同时更新会话 updated_at。"""
    if not msg_ids:
        return False
    if not _owned(owner, sid):
        return False
    now = int(time.time())
    with _get_conn() as conn:
        placeholders = ",".join("?" * len(msg_ids))
        cur = conn.execute(
            f"DELETE FROM messages WHERE conversation_id = ? AND id IN ({placeholders})",
            (sid, *msg_ids),
        )
        # 消息没了，挂在它上面的赞踩也一起走，否则 feedbacks 会攒下悬空的 message_id
        conn.execute(
            f"DELETE FROM feedbacks WHERE conversation_id = ? AND message_id IN ({placeholders})",
            (sid, *msg_ids),
        )
        if cur.rowcount > 0:
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, sid),
            )
        return cur.rowcount > 0
