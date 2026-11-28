"""账号完整清理的后端服务（本阶段仅供停机维护脚本调用，不作为公开 API）。

数据库删除和文件删除不能组成同一个 SQLite 事务：先持久化清理清单，
再删除业务行，最后清文件。失败保留 pending 清单，重试不需要恢复账号。
必须先停止 API、worker 等写入进程，防止清理期间后台任务重新写回数据。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import time
from pathlib import Path

from core import account, config, session

_SCHEMA = """
CREATE TABLE IF NOT EXISTS account_deletion_jobs(
 uid TEXT PRIMARY KEY, state TEXT NOT NULL, manifest TEXT NOT NULL,
 created_at INTEGER NOT NULL, finished_at INTEGER);
CREATE TABLE IF NOT EXISTS owned_uploads(
 owner_id TEXT NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL,
 PRIMARY KEY(owner_id,kind,name));
"""
_OWNER_COLUMNS = {"owner_id", "user_id", "uid", "target_uid", "actor_uid", "follower", "followee", "admin_uid"}
_KINDS = {"chat": ("chat_uploads", ""), "forum": ("forum_img", ".webp"), "avatar": ("avatars", ".jpg")}
_HEX = re.compile(r"[0-9a-f]{16}")


def ensure_tables() -> None:
    account._ensure_tables()
    with session._get_conn() as db:
        db.executescript(_SCHEMA)


def record_upload(uid: str, kind: str, name: str) -> None:
    """只存服务端生成的文件名，不能存浏览器提交的任意绝对路径。"""
    if kind not in _KINDS or not name or Path(name).name != name or "/" in name or "\\" in name:
        raise ValueError("文件归属参数不合法")
    ensure_tables()
    with session._get_conn() as db:
        db.execute("INSERT OR IGNORE INTO owned_uploads VALUES (?,?,?)", (uid, kind, name))


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _tables(db) -> dict[str, set[str]]:
    return {
        name: {r[1] for r in db.execute(f"PRAGMA table_info({_quote(name)})")}
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    }


def _registry_dir() -> Path:
    # 与 kb.pipelines 现有路径一致；不能假设这四个登记文件已跟随 DATA_DIR。
    return Path(config.PROJECT_ROOT) / "data"


def _read_registry(name: str) -> dict:
    path = _registry_dir() / f"kb_{name}.json"
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("资料库登记文件损坏，需先修复再注销")
    return value


def _scheduler_path() -> Path:
    return Path(config.DATA_DIR) / "scheduler.db"


def _manifest(db, uid: str) -> dict:
    tables = _tables(db)
    owners = _read_registry("owners")
    kids = sorted(k for k, owner in owners.items() if owner == uid)
    # 登记文件损坏时拒绝开始，不能用空登记表覆盖原文件。
    if kids:
        for name in ("names", "files", "public"):
            _read_registry(name)
    files = [dict(row) for row in db.execute("SELECT kind,name FROM owned_uploads WHERE owner_id=?", (uid,))]
    user = db.execute("SELECT avatar,username FROM users WHERE id=?", (uid,)).fetchone()
    if user and (user["avatar"] or "").startswith("img:"):
        files.append({"kind": "avatar", "name": user["avatar"][4:]})
    subjects = [uid]
    if "delegations" in tables:
        subjects += [r[0] for r in db.execute("SELECT delegation_id FROM delegations WHERE owner_id=?", (uid,))]
    path = _scheduler_path()
    if path.exists():
        with sqlite3.connect(path) as scheduler:
            if "kb_upload_jobs" in _tables(scheduler):
                subjects += [r[0] for r in scheduler.execute("SELECT job_id FROM kb_upload_jobs WHERE uid=?", (uid,))]
    return {"kb_ids": kids, "files": files, "subjects": subjects, "username": user["username"] if user else ""}


def preview(uid: str) -> dict:
    """只读取归属与计数；不创建清理任务，不删除数据。"""
    # 账号表是已有应用的前置条件，预览不会迁移业务库。
    with sqlite3.connect(Path(session._db_path()).resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        tables = _tables(db)
        exists = bool(db.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone())
        counts = {}
        for table, columns in tables.items():
            fields = columns & _OWNER_COLUMNS
            if table in {"account_deletion_jobs", "deleted_users"} or not fields:
                continue
            condition = " OR ".join(f"{_quote(c)}=?" for c in sorted(fields))
            counts[table] = db.execute(
                f"SELECT count(*) FROM {_quote(table)} WHERE {condition}", [uid] * len(fields)
            ).fetchone()[0]
        return {
            "exists": exists,
            "tables": {k: v for k, v in counts.items() if v},
            "knowledge_bases": sum(owner == uid for owner in _read_registry("owners").values()),
        }


def _delete_rows(db, uid: str, plan: dict) -> dict[str, int]:
    """所有本站 SQLite 业务记录共用当前写事务；先删无 owner 字段的子表。"""
    tables = _tables(db)
    counts = {}

    def delete(table, where, args=()):
        if table in tables:
            counts[table] = (
                counts.get(table, 0) + db.execute(f"DELETE FROM {_quote(table)} WHERE {where}", args).rowcount
            )

    db.execute("CREATE TEMP TABLE purge_keys(kind TEXT, value TEXT, PRIMARY KEY(kind,value))")
    for kind, values in (("kb", plan["kb_ids"]), ("subject", plan["subjects"])):
        db.executemany("INSERT OR IGNORE INTO purge_keys VALUES (?,?)", ((kind, str(v)) for v in values))
    for table, column, kind in (
        ("conversations", "id", "sid"),
        ("reminders", "id", "reminder"),
        ("user_feedback", "id", "feedback"),
    ):
        if table in tables:
            owner = "uid" if table == "user_feedback" else "owner_id"
            db.execute(
                f"INSERT OR IGNORE INTO purge_keys SELECT ?,CAST({_quote(column)} AS TEXT) "
                f"FROM {_quote(table)} WHERE {owner}=?",
                (kind, uid),
            )
    sid = "SELECT value FROM purge_keys WHERE kind='sid'"
    if "messages" in tables:
        # 自进化回放集曾复制过聊天内容。删除源消息前，找出其 case_key。
        db.executemany(
            "INSERT OR IGNORE INTO purge_keys VALUES ('case',?)",
            (
                (hashlib.sha1(f"{r[0]}|{r[1]}".encode()).hexdigest()[:16],)
                for r in db.execute(
                    f"SELECT id,conversation_id FROM messages WHERE conversation_id IN ({sid})"
                ).fetchall()
            ),
        )
    delete("prompt_replay_items", "case_key IN (SELECT value FROM purge_keys WHERE kind='case')")
    if "prompt_evals" in tables:
        # 评估快照也可能复制回答；涉及本人的快照整行删除，避免只清源消息。
        for (case,) in db.execute("SELECT value FROM purge_keys WHERE kind='case'"):
            delete("prompt_evals", "instr(per_case_json,?)>0", (case,))
    if "prompt_variants" in tables:
        # 版本化提示词是项目全局配置，保留配置本身，清除管理员身份引用。
        db.execute("UPDATE prompt_variants SET created_by='manual:deleted' WHERE created_by=?", ("manual:" + uid,))
    if "prompt_replay_sets" in tables:
        db.execute(
            "UPDATE prompt_replay_sets SET size=(SELECT count(*) FROM prompt_replay_items i "
            "WHERE i.path=prompt_replay_sets.path AND i.set_version=prompt_replay_sets.version)"
        )
    delete("feedbacks", f"conversation_id IN ({sid})")
    delete("messages", f"conversation_id IN ({sid})")
    delete("reminder_fires", "reminder_id IN (SELECT value FROM purge_keys WHERE kind='reminder')")
    delete("user_feedback_replies", "fid IN (SELECT value FROM purge_keys WHERE kind='feedback')")
    if "kb_posts" in tables:
        # 沿用论坛现有规则：本人开的整个话题随注销删除；本人在别处的回复也删除。
        db.execute(
            "INSERT OR IGNORE INTO purge_keys SELECT 'post',CAST(id AS TEXT) FROM kb_posts "
            "WHERE user_id=? OR kb_id IN (SELECT value FROM purge_keys WHERE kind='kb')",
            (uid,),
        )
        db.execute(
            "INSERT OR IGNORE INTO purge_keys SELECT 'post',CAST(id AS TEXT) FROM kb_posts "
            "WHERE root_id IN (SELECT value FROM purge_keys WHERE kind='post')"
        )
        if "images" in tables["kb_posts"]:
            for (raw,) in db.execute(
                "SELECT images FROM kb_posts WHERE id IN (SELECT value FROM purge_keys WHERE kind='post')"
            ):
                plan["files"].extend({"kind": "forum", "name": h} for h in (raw or "").split(",") if _HEX.fullmatch(h))
        for table in ("forum_likes", "forum_favorites", "notifications", "forum_reports"):
            delete(table, "post_id IN (SELECT value FROM purge_keys WHERE kind='post')")
        delete("kb_posts", "id IN (SELECT value FROM purge_keys WHERE kind='post')")
        if "reply_to_id" in tables["kb_posts"]:
            db.execute(
                "UPDATE kb_posts SET reply_to_id=0 "
                "WHERE reply_to_id IN (SELECT value FROM purge_keys WHERE kind='post')"
            )
    # 验证码 / 一次性凭证的用户归属在 context JSON 内，不在 user_id 列内。
    if "verification_requests" in tables:
        db.execute(
            "INSERT OR IGNORE INTO purge_keys SELECT 'slot',slot FROM verification_requests "
            "WHERE json_valid(context) AND json_extract(context,'$.subject')=?",
            (uid,),
        )
        delete("verification_grants", "json_valid(context) AND json_extract(context,'$.subject')=?", (uid,))
        delete("verification_slots", "slot IN (SELECT value FROM purge_keys WHERE kind='slot')")
        delete("verification_requests", "json_valid(context) AND json_extract(context,'$.subject')=?", (uid,))
    for table, columns in tables.items():
        if table in {"account_deletion_jobs", "deleted_users", "users"}:
            continue
        fields = columns & _OWNER_COLUMNS
        if table == "tool_approvals":
            fields |= columns & {"user"}
        if fields:
            delete(table, " OR ".join(f"{_quote(c)}=?" for c in sorted(fields)), [uid] * len(fields))
        if "kb_id" in columns:
            if table in {"conversations", "delegations", "kb_proposals"}:
                # 他人使用公开库的会话/委托仍属于他人，只解除资料库引用。
                db.execute(
                    f"UPDATE {_quote(table)} SET kb_id=NULL "
                    "WHERE kb_id IN (SELECT value FROM purge_keys WHERE kind='kb')"
                )
            elif table in {"kb_posts", "kb_favorites", "kb_views", "sediment_batches"}:
                delete(table, "kb_id IN (SELECT value FROM purge_keys WHERE kind='kb')")
    # 管理员的审核署名不决定帖子归属：保留别人的帖子，只清本人的审核标识。
    if "kb_posts" in tables:
        for col in ("removed_by", "reviewed_by", "deleted_by"):
            if col in tables["kb_posts"]:
                db.execute(f"UPDATE kb_posts SET {col}='' WHERE {col}=?", (uid,))
        if "forum_reports" in tables and "report_n" in tables["kb_posts"]:
            db.execute(
                "UPDATE kb_posts SET report_n=(SELECT count(*) FROM forum_reports r WHERE r.post_id=kb_posts.id)"
            )
    if "rate_buckets" in tables:
        delete(
            "rate_buckets",
            "instr(':' || user_id || ':',?)>0 OR user_id=?",
            (":" + uid + ":", "auth:" + plan["username"].casefold()),
        )
    # 最小墓碑仅用来拒绝旧 JWT；不保留原用户名、邮箱或第三方标识。
    db.execute("INSERT OR REPLACE INTO deleted_users VALUES (?,?,?,?)", (uid, "", int(time.time()), "account_purge"))
    delete("users", "id=?", (uid,))
    return counts


def _is_link(path: Path) -> bool:
    try:
        # Python 3.10 没有 Path.is_junction，Windows reparse 属性也要检查。
        return path.is_symlink() or bool(
            getattr(path.lstat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        )
    except FileNotFoundError:
        return False


def _safe_child(root: Path, name: str) -> Path:
    """删除前检查绝对路径，拒绝根目录、目录穿越、符号链接和 Windows junction。"""
    if not name or name in {".", ".."} or Path(name).name != name or "/" in name or "\\" in name or ":" in name:
        raise ValueError("清理路径不合法")
    if _is_link(root):
        raise ValueError("清理目录不能是链接")
    root = root.resolve()
    target = root / name
    if _is_link(target) or target.resolve().parent != root:
        raise ValueError("清理目标超出允许目录")
    return target


def _remove(root: Path, name: str, *, directory: bool = False) -> None:
    path = _safe_child(root, name)
    if directory:
        if path.exists():
            # 顶层已校验；内部链接同样拒绝，防止递归清理跟随链接到别处。
            for child in path.rglob("*"):
                if _is_link(child):
                    raise ValueError("清理目录包含链接，请先人工检查")
            shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _purge_scheduler(uid: str, plan: dict) -> None:
    path = _scheduler_path()
    if not path.exists():
        return
    with sqlite3.connect(path) as db:
        tables = _tables(db)
        if "job_runs" in tables:
            db.executemany("DELETE FROM job_runs WHERE subject_id=?", ((s,) for s in plan["subjects"]))
        if "kb_upload_jobs" in tables:
            db.execute("DELETE FROM kb_upload_jobs WHERE uid=?", (uid,))


def _purge_files(uid: str, plan: dict) -> None:
    if plan["kb_ids"]:
        from kb import pipelines

        for kid in plan["kb_ids"]:
            # 不走现有静默吞错的 delete_kb：向量库、登记和原文件都必须确认清掉。
            pipelines.purge_account_kb(uid, kid)
    _remove(Path(config.DOWNLOAD_DIR), uid, directory=True)
    for item in plan["files"]:
        kind, name = item["kind"], item["name"]
        if kind not in _KINDS:
            raise ValueError("未知清理文件类型")
        with sqlite3.connect(session._db_path()) as db:
            tables = _tables(db)
            if db.execute("SELECT 1 FROM owned_uploads WHERE kind=? AND name=? LIMIT 1", (kind, name)).fetchone():
                continue  # 内容去重文件仍被其他人使用。
            if kind == "avatar" and db.execute("SELECT 1 FROM users WHERE avatar=?", ("img:" + name,)).fetchone():
                continue
            if kind == "forum" and "kb_posts" in tables and "images" in tables["kb_posts"]:
                if db.execute(
                    "SELECT 1 FROM kb_posts WHERE instr(',' || images || ',', ?) > 0 LIMIT 1", ("," + name + ",",)
                ).fetchone():
                    continue
        if kind in {"forum", "avatar"} and not _HEX.fullmatch(name):
            raise ValueError("图片清理标识不合法")
        subdir, suffix = _KINDS[kind]
        _remove(Path(config.DATA_DIR) / subdir, name + suffix)
    # audit.log 也含账号标识和改名前后的登录名。本服务仅在写入进程停止后运行。
    audit = _safe_child(Path(config.DATA_DIR), "audit.log")
    if audit.exists():
        kept = []
        for line in audit.read_text(encoding="utf-8").splitlines(keepends=True):
            if line.strip() and json.loads(line).get("uid") == uid:
                continue
            kept.append(line)
        tmp = audit.with_suffix(".purging")
        tmp.write_text("".join(kept), encoding="utf-8")
        os.replace(tmp, audit)


def purge(uid: str, *, confirm_uid: str, writers_stopped: bool = False) -> dict:
    """可信维护入口。禁止从 HTTP 路由直接调用；无密码账号同样可以注销。

    返回 done 才代表当前在线业务数据清理完成。文件/调度清理失败则抛错，
    pending 清单保留在数据库，可按相同 uid 重试。历史备份不在此范围内。
    """
    if not writers_stopped or not uid or confirm_uid != uid:
        raise ValueError("请先停止 API 和 worker，并明确确认要注销的用户 ID")
    _safe_child(Path(config.DOWNLOAD_DIR), uid)
    ensure_tables()
    with sqlite3.connect(session._db_path(), timeout=10) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN IMMEDIATE")
        job = db.execute("SELECT * FROM account_deletion_jobs WHERE uid=?", (uid,)).fetchone()
        if job and job["state"] == "done":
            return {"state": "done", "already_completed": True}
        if job:
            plan = json.loads(job["manifest"])
        else:
            if not db.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
                raise ValueError("账号不存在，且没有待重试的注销任务")
            plan = _manifest(db, uid)
            _delete_rows(db, uid, plan)
            # 原用户名仅用于本事务清理限流桶；清理清单不保留它。
            plan.pop("username", None)
            db.execute(
                "INSERT INTO account_deletion_jobs VALUES (?,?,?, ?,NULL)",
                (uid, "pending", json.dumps(plan, ensure_ascii=False), int(time.time())),
            )
    _purge_scheduler(uid, plan)
    _purge_files(uid, plan)
    with sqlite3.connect(session._db_path()) as db:
        db.execute(
            "UPDATE account_deletion_jobs SET state='done',manifest='{}',finished_at=? WHERE uid=?",
            (int(time.time()), uid),
        )
    return {"state": "done", "already_completed": False}
