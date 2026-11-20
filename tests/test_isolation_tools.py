"""隔离入口的边界：合法 URI 可用，越界 URI 与已有业务端口始终拒绝。"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("kind", ["plain", "uri", "escaped_uri", "outside", "outside_uri", "remote_uri"])
def test_sqlite_uri_boundary_in_fresh_process(kind):
    code = '''
import sqlite3, sys
from pathlib import Path
from urllib.parse import quote
from tools.run_isolated_tests import configure
root = Path(sys.argv[1])
sandbox = configure(root)
inside = sandbox / "data" / "space database.db"
kind = sys.argv[2]
paths = {
    "plain": str(inside),
    "uri": "file:" + str(inside) + "?mode=rwc",
    "escaped_uri": "file:" + quote(str(inside).replace(chr(92), "/"), safe="/:") + "?mode=rwc",
    "outside": str(root / "must-not-create-isolation.db"),
    "outside_uri": "file:" + str(root / "must-not-create-isolation.db") + "?mode=rwc",
    "remote_uri": "file://example.invalid/any.db?mode=rwc",
}
try:
    with sqlite3.connect(paths[kind], uri=kind != "plain") as db:
        db.execute("CREATE TABLE probe(value INTEGER)")
        db.execute("INSERT INTO probe VALUES (42)")
        assert db.execute("SELECT value FROM probe").fetchone() == (42,)
    assert kind in {"plain", "uri", "escaped_uri"}
except PermissionError:
    assert kind in {"outside", "outside_uri", "remote_uri"}
'''
    result = subprocess.run(
        [sys.executable, "-B", "-c", code, str(ROOT), kind],
        cwd=ROOT, capture_output=True, text=True, timeout=15, env=dict(os.environ, PYTHONIOENCODING="utf-8"),
    )
    assert result.returncode == 0, result.stderr
    assert not (ROOT / "must-not-create-isolation.db").exists()


def test_default_business_port_is_never_contacted():
    code = '''
import socket, sys
from pathlib import Path
from tools.run_isolated_tests import configure
configure(Path(sys.argv[1]))
with socket.socket() as conn:
    try:
        conn.connect(("127.0.0.1", 8000))
    except PermissionError as exc:
        assert "8000" in str(exc)
    else:
        raise AssertionError("existing business service must never be contacted")
'''
    result = subprocess.run(
        [sys.executable, "-B", "-c", code, str(ROOT)],
        cwd=ROOT, capture_output=True, text=True, timeout=15, env=dict(os.environ, PYTHONIOENCODING="utf-8"),
    )
    assert result.returncode == 0, result.stderr


def test_script_mode_rejects_non_test_target():
    result = subprocess.run(
        [sys.executable, "-B", "tools/run_isolated_tests.py", "--script", "main.py"],
        cwd=ROOT, capture_output=True, timeout=15, env=dict(os.environ, PYTHONIOENCODING="utf-8"),
    )
    assert result.returncode != 0
    assert "只允许执行 tests/" in result.stderr.decode("utf-8", errors="replace")
