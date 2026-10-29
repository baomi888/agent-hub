"""重现 pytest 收集顺序下的 DB 指向，诊断 no such table: users。"""
import os
import sys
import tempfile

sys.path.insert(0, r"C:\biancheng\agent-hub")

# 模拟 pytest 字母序收集：cost_guards -> daily -> compress_v2
import tests.test_cost_guards  # noqa: F401,E402
import tests.test_daily  # noqa: F402,E402  其模块级代码会永久替换 _db_path

os.environ["AGENT_HUB_DB"] = os.path.join(
    tempfile.gettempdir(), "agent_hub_compress_v2_test.db"
)
import tests.test_compress_v2  # noqa: F401,E402

import core.account as acc  # noqa: E402
import core.session as session  # noqa: E402

print("session._db_path() ->", session._db_path())
acc._ensure_tables()
with session._get_conn() as conn:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('users','conversations')"
    ).fetchall()
    print("tables in current db:", sorted(r["name"] for r in row))
print("main-thread conn path attr:", getattr(session._LOCAL, "path", None))
