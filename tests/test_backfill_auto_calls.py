"""历史回填工具的回归（2026-11-15 第 22 批）。

这个工具会**改历史数据**：把调用从「真实用户」挪进「内部」。所以它的判据错了，
不会有任何报错，只会让某个真人的使用记录凭空消失 —— 和误标内部账号一样严重。
四条不变量：

  1. **路径形状要能对上**：库里是路由模板（`/api/sessions/{sid}`），日志里是真实路径
     （`/api/sessions/9f3c…`），两者必须归一成同一个键，否则一条都匹配不上（静默 0 命中）。
  2. **同一秒里有真人也出现过的，一律不标**：分不清哪条是谁的，宁可数字偏高。
  3. **只认自动化 UA**：浏览器行不能引来标记，否则真人会被自己的正常访问抹掉。
  4. **默认 dry-run**：不加 `--apply` 不许写库。
"""

from __future__ import annotations

import sqlite3

import pytest

from core import account, apilog, session, timeutil, verify_tag
from tools.backfill_auto_calls import backfill, shape

HEADLESS_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    " (KHTML, like Gecko) HeadlessChrome/157.0.0.0 Safari/537.36 Edg/157.0.0.0"
)
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    " (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)
CURL_UA = "curl/8.4.0"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    path = str(tmp_path / "bf.db")
    monkeypatch.setattr(session, "_db_path", lambda: path)
    session._init_db()
    # 工具会按 uid 打一张「按账号」报表，需要 users 表（生产库当然有）
    account._ensure_tables(force=True)
    apilog._READY.clear()
    apilog._BUF.clear()
    apilog.ensure()
    verify_tag.invalidate()
    yield path
    apilog._BUF.clear()
    apilog._READY.clear()


def _insert(db: str, rows: list[tuple]) -> None:
    """rows: (ts, path, uid) —— 只填回填关心的列。"""
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO api_call_logs (ts, path, method, status, dur_ms, uid, internal, client)"
        " VALUES (?, ?, 'GET', 200, 10, ?, 0, '')",
        [(ts, p, u) for ts, p, u in rows],
    )
    conn.commit()
    conn.close()


def _row(db: str, path: str):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    r = conn.execute(
        "SELECT internal, client FROM api_call_logs WHERE path = ? ORDER BY id DESC LIMIT 1", (path,)
    ).fetchone()
    conn.close()
    return r


def _write_log(tmp_path, day: str, lines: list[tuple[str, str]]) -> str:
    """lines: (路径, UA)。时间用该天的 01:00 起逐秒递增，与 _ts 对得上。"""
    f = tmp_path / "access.log"
    body = []
    for i, (path, ua) in enumerate(lines):
        body.append(
            '%s - - [%s:01:00:%02d +0800] "GET %s HTTP/1.1" 200 12 "-" "%s"'
            % ("1.2.3.4", day, i, path, ua)
        )
    f.write_text("\n".join(body) + "\n", encoding="utf-8")
    return str(f)


def test_shape_matches_template_and_real_path():
    """★ 静默 0 命中的根因：模板与真实路径必须归一成同一个形状。

    这条如果不成立，工具会打印"可回填 0 条"、看起来一切正常，而历史数据一条没修。
    """
    assert shape("/api/sessions/9f3c1a2b3c4d5e6f7a8b9c0d1e2f3a4b") == shape("/api/sessions/{sid}")
    assert shape("/api/sessions/9f3c1a2b3c4d5e6f7a8b9c0d1e2f3a4b") == "/api/sessions/*"
    # 尾斜杠两侧不一致（main.normalize_api_slash 会把请求改成注册的那一种）
    assert shape("/api/kb/") == shape("/api/kb")
    # 固定段不能被当成长 id 抹掉，否则所有接口会并成一条
    assert shape("/api/chat/stream") == "/api/chat/stream"
    assert shape("/api/auth/me") == "/api/auth/me"


@pytest.mark.parametrize(
    "ua, is_auto",
    [(HEADLESS_UA, True), (CURL_UA, True), (BROWSER_UA, False)],
)
def test_only_automated_ua_lines_can_mark(env, tmp_path, ua, is_auto):
    """只有无头/脚本的日志行能引来标记；浏览器行不许（否则真人被自己的访问抹掉）。"""
    today = timeutil.recent_days(1)[0]
    s, _ = timeutil.day_bounds(today)
    _insert(env, [(s + 3600, "/api/kb", "u1")])
    log = _write_log(tmp_path, today.replace("-", "/")[0:0] or _nginx_day(today), [("/api/kb", ua)])
    backfill(env, log, 1, apply=True)
    row = _row(env, "/api/kb")
    assert bool(row["internal"]) is is_auto, f"UA={ua!r} 的判定错了"
    if is_auto:
        assert row["client"] in ("headless", "script")


def test_mixed_second_is_left_alone(env, tmp_path):
    """★ 同一秒里脚本与真人并存时一律不标 —— 分不清是谁的，宁可数字偏高。

    误标的后果是「一个真人的调用凭空消失且没人发现」，比数字偏高严重得多。
    """
    today = timeutil.recent_days(1)[0]
    s, _ = timeutil.day_bounds(today)
    _insert(env, [(s + 3600, "/api/kb", "u1")])
    log = _write_log(
        tmp_path, _nginx_day(today), [("/api/kb", HEADLESS_UA), ("/api/kb", BROWSER_UA)]
    )
    backfill(env, log, 1, apply=True)
    assert _row(env, "/api/kb")["internal"] == 0, "同秒有真人时仍然标了自动化"


def test_second_off_by_one_is_tolerated(env, tmp_path):
    """nginx 与 apilog 的写库时刻会差 1 秒（响应完成先后），窗口必须容得下。"""
    today = timeutil.recent_days(1)[0]
    s, _ = timeutil.day_bounds(today)
    _insert(env, [(s + 3600, "/api/kb", "u1")])
    log = _write_log(tmp_path, _nginx_day(today), [("/x", BROWSER_UA), ("/api/kb", HEADLESS_UA)])
    backfill(env, log, 1, apply=True)
    assert _row(env, "/api/kb")["internal"] == 1


def test_dry_run_does_not_write(env, tmp_path):
    """默认不写。一条命令就可能改掉历史数据的东西，不许"跑了就等于改了"。"""
    today = timeutil.recent_days(1)[0]
    s, _ = timeutil.day_bounds(today)
    _insert(env, [(s + 3600, "/api/kb", "u1")])
    log = _write_log(tmp_path, _nginx_day(today), [("/api/kb", HEADLESS_UA)])
    backfill(env, log, 1, apply=False)
    assert _row(env, "/api/kb")["internal"] == 0


def test_rows_outside_the_window_are_untouched(env, tmp_path):
    """只动区间内的行：工具跑 --days 1 不该顺手改掉上周的数据。"""
    days = timeutil.recent_days(7)
    today, old = days[-1], days[0]
    s_old, _ = timeutil.day_bounds(old)
    s_today, _ = timeutil.day_bounds(today)
    _insert(env, [(s_old + 3600, "/api/kb", "u1"), (s_today + 3600, "/api/kb", "u1")])
    log = _write_log(tmp_path, _nginx_day(today), [("/api/kb", HEADLESS_UA)])
    backfill(env, log, 1, apply=True)
    conn = sqlite3.connect(env)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT ts, internal FROM api_call_logs ORDER BY ts").fetchall()
    conn.close()
    assert rows[0]["internal"] == 0, "区间外的旧数据被改了"
    assert rows[1]["internal"] == 1


def _nginx_day(iso_day: str) -> str:
    """`2026-11-15` → `15/Nov/2026`（nginx 日志的日期形态）。"""
    y, m, d = iso_day.split("-")
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return "%s/%s/%s" % (d, months[int(m) - 1], y)
