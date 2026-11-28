"""只在独立临时库/目录测试注销；不接 main，不运行真实服务或删除已有用户数据。"""

import json
import sqlite3
from pathlib import Path

import pytest
from test_wechat_login import begin, finish
from test_wechat_login import wxenv as wxenv

from core import account, config, session
from core import account_deletion as deletion


@pytest.fixture
def environment(wxenv, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECT_ROOT", str(tmp_path / "project"))
    monkeypatch.setattr(config, "DOWNLOAD_DIR", str(tmp_path / "downloads"))
    root = Path(config.PROJECT_ROOT) / "data"
    root.mkdir(parents=True)
    for name in ("owners", "names", "files", "public"):
        (root / f"kb_{name}.json").write_text("{}", encoding="utf-8")
    deletion.ensure_tables()
    cli = wxenv[1]
    finish(cli, begin(cli))
    uid = cli.get("/api/auth/me/").json()["user"]["id"]
    other = account.create_user("other_person", "Strong_other_pass_9")["id"]
    return cli, uid, other


def put_file(kind, name):
    subdir, suffix = deletion._KINDS[kind]
    path = Path(config.DATA_DIR) / subdir / (name + suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"private-file")
    return path


def run(uid):
    return deletion.purge(uid, confirm_uid=uid, writers_stopped=True)


def test_complete_passwordless_account_purge_and_next_scan_gets_new_user(environment):
    cli, uid, other = environment
    old_token = cli.cookies.get("baomi_session")
    own_file = put_file("chat", "my_chat.jpg")
    other_file = put_file("chat", "other_chat.jpg")
    shared = put_file("forum", "a" * 16)
    private = put_file("forum", "b" * 16)
    avatar = put_file("avatar", "c" * 16)
    deletion.record_upload(uid, "chat", own_file.name)
    deletion.record_upload(other, "chat", other_file.name)
    deletion.record_upload(uid, "forum", "a" * 16)
    deletion.record_upload(other, "forum", "a" * 16)
    deletion.record_upload(uid, "avatar", "c" * 16)
    download = Path(config.DOWNLOAD_DIR) / uid
    download.mkdir(parents=True)
    (download / "downloaded.pdf").write_bytes(b"private")
    audit = Path(config.DATA_DIR) / "audit.log"
    audit.write_text(
        json.dumps({"uid": uid, "detail": "old name"}) + "\n" + json.dumps({"uid": other}) + "\n", encoding="utf-8"
    )
    with session._get_conn() as db:
        db.execute(
            "INSERT INTO conversations(id,owner_id,title,created_at,updated_at) VALUES ('my-chat',?,'secret',1,1)",
            (uid,),
        )
        db.execute(
            "INSERT INTO conversations(id,owner_id,title,created_at,updated_at) VALUES ('other-chat',?,'keep',1,1)",
            (other,),
        )
        mid = db.execute(
            "INSERT INTO messages(conversation_id,role,content,created_at) "
            "VALUES ('my-chat','user','private question',1)"
        ).lastrowid
        db.execute(
            "INSERT INTO feedbacks(conversation_id,message_id,rating,created_at) VALUES ('my-chat',?,'down',1)", (mid,)
        )
        db.execute(
            "INSERT INTO kb_posts(kb_id,user_id,body,created_at,images) VALUES ('forum',?,'private topic',1,?)",
            (uid, "b" * 16),
        )
        root = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute(
            "INSERT INTO kb_posts(kb_id,user_id,body,created_at,root_id) VALUES ('forum',?,'reply',1,?)", (other, root)
        )
        db.execute("INSERT INTO kb_posts(kb_id,user_id,body,created_at) VALUES ('forum',?,'other topic',1)", (other,))
        db.executescript("""
        CREATE TABLE IF NOT EXISTS focus_tasks(id TEXT,owner_id TEXT,name TEXT);
        CREATE TABLE IF NOT EXISTS reminders(id TEXT,owner_id TEXT);
        CREATE TABLE IF NOT EXISTS reminder_fires(reminder_id TEXT,status TEXT);
        CREATE TABLE IF NOT EXISTS tool_approvals(request_id TEXT,user TEXT);
        CREATE TABLE IF NOT EXISTS delegations(delegation_id TEXT,owner_id TEXT);
        CREATE TABLE IF NOT EXISTS user_feedback(id INTEGER,uid TEXT);
        CREATE TABLE IF NOT EXISTS user_feedback_replies(fid INTEGER,admin_uid TEXT);
        """)
        db.execute("INSERT INTO focus_tasks VALUES ('mine',?,'task')", (uid,))
        db.execute("INSERT INTO focus_tasks VALUES ('theirs',?,'keep')", (other,))
        db.execute("INSERT INTO reminders VALUES ('my-reminder',?)", (uid,))
        db.execute("INSERT INTO reminder_fires VALUES ('my-reminder','sent')")
        db.execute("INSERT INTO tool_approvals VALUES ('approval',?)", (uid,))
        db.execute("INSERT INTO delegations VALUES ('my-delegate',?)", (uid,))
        db.execute("INSERT INTO user_feedback VALUES (1,?)", (uid,))
        db.execute("INSERT INTO user_feedback_replies VALUES (1,?)", (other,))
        db.execute("INSERT INTO security_emails VALUES (?,?,1,1)", (uid, "remove@example.com"))
        db.execute("INSERT INTO external_identities VALUES ('alipay','test-app','test-subject',?,1)", (uid,))
        db.execute("CREATE TABLE IF NOT EXISTS rate_buckets(user_id TEXT,sec INTEGER,n INTEGER)")
        db.execute("INSERT INTO rate_buckets VALUES (?,1,1)", ("forum:" + uid + ":post",))
        context = json.dumps({"subject": uid, "address": "remove@example.com"})
        db.execute(
            "INSERT INTO verification_requests(id,slot,context,code_hash,expires,state) "
            "VALUES ('request','slot',?,'hash',9999999999,'sent')",
            (context,),
        )
        db.execute("INSERT INTO verification_slots VALUES ('slot','request','request')")
        db.execute("INSERT INTO verification_grants VALUES ('grant','request',?,9999999999,NULL)", (context,))
        case = deletion.hashlib.sha1(f"{mid}|my-chat".encode()).hexdigest()[:16]
        db.execute("INSERT INTO prompt_replay_sets VALUES ('agent',1,1,1,1,'test')")
        db.execute(
            "INSERT INTO prompt_replay_items VALUES ('agent',1,?,'private question',NULL,'','down','{}',1)", (case,)
        )
    with sqlite3.connect(deletion._scheduler_path()) as db:
        db.execute("CREATE TABLE job_runs(subject_id TEXT,status TEXT)")
        db.execute("CREATE TABLE kb_upload_jobs(job_id TEXT,uid TEXT,kb_id TEXT)")
        db.executemany(
            "INSERT INTO job_runs VALUES (?,'pending')", [(uid,), (other,), ("my-delegate",), ("my-upload",)]
        )
        db.execute("INSERT INTO kb_upload_jobs VALUES ('my-upload',?,'my-kb')", (uid,))
    assert deletion.preview(uid)["exists"] is True
    assert run(uid)["state"] == "done"  # 无密码账号无需先设置密码即可注销。
    assert account.get_user(uid) is None and account.get_user(other)
    with session._get_conn() as db:
        for table in (
            "messages",
            "feedbacks",
            "reminders",
            "reminder_fires",
            "tool_approvals",
            "delegations",
            "security_emails",
            "verification_requests",
            "verification_slots",
            "verification_grants",
            "external_identities",
            "external_auth_flows",
            "user_feedback",
            "user_feedback_replies",
            "prompt_replay_items",
        ):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table
        assert db.execute("SELECT title FROM conversations").fetchall()[0][0] == "keep"
        assert db.execute("SELECT name FROM focus_tasks").fetchall()[0][0] == "keep"
        assert db.execute("SELECT body FROM kb_posts").fetchall()[0][0] == "other topic"
        assert db.execute("SELECT size FROM prompt_replay_sets").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM rate_buckets WHERE instr(user_id,?)>0", (uid,)).fetchone()[0] == 0
        assert db.execute("SELECT username FROM deleted_users WHERE uid=?", (uid,)).fetchone()[0] == ""
        assert db.execute("SELECT manifest FROM account_deletion_jobs WHERE uid=?", (uid,)).fetchone()[0] == "{}"
    with sqlite3.connect(deletion._scheduler_path()) as db:
        assert db.execute("SELECT subject_id FROM job_runs").fetchall() == [(other,)]
        assert db.execute("SELECT count(*) FROM kb_upload_jobs").fetchone()[0] == 0
    assert not own_file.exists() and not private.exists() and not avatar.exists() and not download.exists()
    assert other_file.exists() and shared.exists()
    assert uid not in audit.read_text(encoding="utf-8") and other in audit.read_text(encoding="utf-8")
    cli.cookies.clear()
    assert cli.get("/api/auth/me/", headers={"Authorization": "Bearer " + old_token}).status_code == 401
    finish(cli, begin(cli))
    assert cli.get("/api/auth/me/").json()["user"]["id"] != uid


def test_failed_file_cleanup_stays_pending_and_retries_after_account_is_gone(environment, monkeypatch):
    _, uid, _ = environment
    path = put_file("chat", "locked.jpg")
    deletion.record_upload(uid, "chat", path.name)
    original = deletion._purge_files

    def fail(*args):
        raise PermissionError("simulated locked file")

    monkeypatch.setattr(deletion, "_purge_files", fail)
    with pytest.raises(PermissionError):
        run(uid)
    assert account.is_deleted(uid) and account.get_user(uid) is None and path.exists()
    with session._get_conn() as db:
        assert db.execute("SELECT state FROM account_deletion_jobs WHERE uid=?", (uid,)).fetchone()[0] == "pending"
    monkeypatch.setattr(deletion, "_purge_files", original)
    assert run(uid)["state"] == "done" and not path.exists()
    assert run(uid)["already_completed"] is True


def test_database_error_rolls_back_account_bindings_and_tombstone(environment):
    _, uid, _ = environment
    with session._get_conn() as db:
        db.execute("CREATE TRIGGER deny_account_delete BEFORE DELETE ON users BEGIN SELECT RAISE(ABORT,'test'); END")
    with pytest.raises(sqlite3.IntegrityError):
        run(uid)
    assert account.get_user(uid) and not account.is_deleted(uid)
    with session._get_conn() as db:
        assert db.execute("SELECT count(*) FROM external_identities WHERE user_id=?", (uid,)).fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM account_deletion_jobs").fetchone()[0] == 0


def test_refuse_without_confirmation_and_reject_corrupt_registry(environment):
    _, uid, _ = environment
    for kwargs in ({"confirm_uid": uid}, {"confirm_uid": "another", "writers_stopped": True}):
        with pytest.raises(ValueError):
            deletion.purge(uid, **kwargs)
    (deletion._registry_dir() / "kb_owners.json").write_text("broken", encoding="utf-8")
    with pytest.raises(ValueError):
        run(uid)
    assert account.get_user(uid)


@pytest.mark.parametrize("name", ["..", ".", "../other", "a/b", "a\\b", "C:target"])
def test_file_cleanup_refuses_path_escape(tmp_path, name):
    keep = tmp_path / "keep.txt"
    keep.write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError):
        deletion._remove(tmp_path, name, directory=True)
    assert keep.read_text(encoding="utf-8") == "keep"


def test_vector_registry_and_source_cleanup_is_strict_and_retryable(environment, monkeypatch):
    from kb import pipelines

    _, uid, other = environment
    kid, keep = "my-kb-123", "other-kb-123"
    for name in ("owners", "names", "files", "public"):
        values = {kid: uid, keep: other} if name == "owners" else {kid: "private", keep: "keep"}
        (deletion._registry_dir() / f"kb_{name}.json").write_text(json.dumps(values), encoding="utf-8")
    root = Path(config.DATA_DIR) / "kb_sources" / kid
    root.mkdir(parents=True)
    (root / "private.pdf").write_bytes(b"private")

    class FakeChroma:
        names = {kid, keep}
        fail = True

        def list_collections(self):
            return sorted(self.names)

        def delete_collection(self, name):
            if self.fail:
                raise OSError("simulated vector disk failure")
            self.names.remove(name)

    client = FakeChroma()
    monkeypatch.setattr(pipelines, "chroma_client", lambda: client)
    with pytest.raises(OSError):
        run(uid)
    assert kid in deletion._read_registry("owners") and root.exists()
    client.fail = False
    assert run(uid)["state"] == "done"
    assert not root.exists() and client.names == {keep}
    for name in ("owners", "names", "files", "public"):
        assert set(deletion._read_registry(name)) == {keep}


def test_real_chroma_collection_is_deleted_while_other_collection_remains(environment, tmp_path, monkeypatch):
    import chromadb
    from chromadb.config import Settings

    from kb import pipelines

    _, uid, other = environment
    kid, keep = "kb_owned_delete", "kb_other_keep"
    for name in ("owners", "names", "files", "public"):
        values = {kid: uid, keep: other} if name == "owners" else {kid: "mine", keep: "keep"}
        (deletion._registry_dir() / f"kb_{name}.json").write_text(json.dumps(values), encoding="utf-8")
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"), settings=Settings(anonymized_telemetry=False))
    for name in (kid, keep):
        collection = client.create_collection(name)
        collection.add(ids=["document"], documents=["private text"], embeddings=[[0.1, 0.2, 0.3]])
    monkeypatch.setattr(pipelines, "chroma_client", lambda: client)
    with session._get_conn() as db:
        db.execute(
            "INSERT INTO conversations(id,owner_id,title,kb_id,created_at,updated_at) "
            "VALUES ('shared-reader',?,'keep this conversation',?,1,1)",
            (other, kid),
        )
        db.execute("CREATE TABLE delegations(delegation_id TEXT,owner_id TEXT,kb_id TEXT)")
        db.execute("INSERT INTO delegations VALUES ('other-delegate',?,?)", (other, kid))
    assert run(uid)["state"] == "done"
    assert {c.name for c in client.list_collections()} == {keep}
    assert client.get_collection(keep).count() == 1
    assert uid not in deletion._read_registry("owners").values()
    with session._get_conn() as db:
        assert db.execute("SELECT title,kb_id FROM conversations WHERE id='shared-reader'").fetchone()[:] == (
            "keep this conversation",
            None,
        )
        assert db.execute("SELECT owner_id,kb_id FROM delegations").fetchone()[:] == (other, None)


def test_preview_does_not_create_missing_database(tmp_path, monkeypatch):
    missing = tmp_path / "missing.db"
    monkeypatch.setattr(session, "_db_path", lambda: str(missing))
    with pytest.raises(sqlite3.OperationalError):
        deletion.preview("nobody")
    assert not missing.exists()
