"""会话置顶：存储、HTTP 契约与侧栏接线（2026-11-26）。

置顶看起来只是"把一行挪到最上面"，实际有三条容易破的约束：

  1. **归属隔离**：PATCH 必须由 get_current_user 决定，不能靠请求里带 uid；
  2. **不污染时间**：置顶不能改 updated_at，否则「今天」分组和最近排序全乱；
  3. **两端入口齐全**：桌面 hover 和手机左滑是两套独立 DOM，少接一个
     就是"手机上根本没法置顶"，而接口测试全绿也发现不了。
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from core import session
from core.auth import get_current_user

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"
SIDEBAR = SRC / "components" / "Sidebar.tsx"
COMPONENTS_CSS = SRC / "app" / "styles" / "components.css"
USE_SESSIONS = SRC / "lib" / "hooks" / "useSessions.ts"
API_TS = SRC / "lib" / "api.ts"
TYPES_TS = SRC / "lib" / "types.ts"
CHAT_PROVIDER = SRC / "components" / "shell" / "ChatProvider.tsx"
CONSOLE_LAYOUT = SRC / "components" / "console" / "ConsoleLayout.tsx"

_ORIG_DB_PATH = session._db_path


def _read(p: Path) -> str:
    assert p.exists(), f"文件不存在：{p}"
    return p.read_text(encoding="utf-8")


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "sessions.db"
    monkeypatch.setattr(session, "_db_path", lambda: str(path))
    session._init_db()
    yield str(path)
    monkeypatch.setattr(session, "_db_path", _ORIG_DB_PATH)


@pytest.fixture()
def client(db):
    main.app.dependency_overrides[get_current_user] = lambda: "u1"
    yield TestClient(main.app, raise_server_exceptions=False, follow_redirects=False)
    main.app.dependency_overrides.clear()


class TestStore:
    def test_pinned_is_bool_and_sorted_first(self, db):
        a = session.create_conversation("u1", "旧的")
        b = session.create_conversation("u1", "新的")
        assert session.list_conversations("u1")[0]["id"] == b["id"]
        assert session.set_pinned("u1", a["id"], True)
        rows = session.list_conversations("u1")
        assert [r["id"] for r in rows] == [a["id"], b["id"]]
        assert rows[0]["pinned"] is True and rows[1]["pinned"] is False
        assert session.get_conversation("u1", a["id"])["pinned"] is True

    def test_pin_does_not_change_updated_at(self, db):
        c = session.create_conversation("u1", "会话")
        assert session.set_pinned("u1", c["id"], True)
        assert session.get_conversation("u1", c["id"])["updated_at"] == c["updated_at"]

    def test_owner_isolation(self, db):
        c = session.create_conversation("u1", "别人的")
        assert session.set_pinned("u2", c["id"], True) is False
        assert session.get_conversation("u1", c["id"])["pinned"] is False

    def test_old_db_gets_pinned_column(self, tmp_path, monkeypatch):
        """老库建表时没有 pinned 列，`CREATE TABLE IF NOT EXISTS` 不会补。"""
        path = tmp_path / "old.db"
        conn = sqlite3.connect(str(path))
        conn.execute(
            "CREATE TABLE conversations (id TEXT PRIMARY KEY, owner_id TEXT, "
            "title TEXT NOT NULL, kb_id TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)"
        )
        conn.commit()
        conn.close()
        monkeypatch.setattr(session, "_db_path", lambda: str(path))
        session._init_db()
        conn = sqlite3.connect(str(path))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(conversations)")}
        conn.close()
        assert "pinned" in cols, "老库没补 pinned 列，上线后 GET /api/sessions 会 500"


class TestApi:
    def test_patch_pin_then_list(self, client):
        a = client.post("/api/sessions/", json={"title": "A"}).json()
        b = client.post("/api/sessions/", json={"title": "B"}).json()
        r = client.patch(f"/api/sessions/{a['id']}/", json={"pinned": True})
        assert r.status_code == 200
        assert r.json()["pinned"] is True
        rows = client.get("/api/sessions/").json()["sessions"]
        assert [s["id"] for s in rows] == [a["id"], b["id"]]
        assert isinstance(rows[0]["pinned"], bool)
        r = client.patch(f"/api/sessions/{a['id']}/", json={"pinned": False})
        assert r.json()["pinned"] is False
        assert client.get("/api/sessions/").json()["sessions"][0]["id"] == b["id"]

    def test_requires_login(self, db):
        c = TestClient(main.app, raise_server_exceptions=False, follow_redirects=False)
        assert c.patch("/api/sessions/whatever/", json={"pinned": True}).status_code == 401

    def test_cannot_pin_others_session(self, client, db):
        other = session.create_conversation("u2", "不属于 u1")
        r = client.patch(f"/api/sessions/{other['id']}/", json={"pinned": True})
        assert r.status_code == 404


class TestFrontendWiring:
    def test_group_has_pinned_first(self):
        src = _read(SIDEBAR)
        assert re.search(r'\{\s*label:\s*"置顶"', src), "侧栏没有置顶分组"
        assert re.search(r'if \(c\.pinned\)\s*\{\s*buckets\[0\]', src), (
            "置顶会话没有单独进第一组，会同时出现在「今天」里"
        )
        assert "label: \"今天\"" in src and "label: \"更早\"" in src, "原有时间分组被删掉了"

    def test_both_pin_entries_wired(self):
        src = _read(SIDEBAR)
        assert "className=\"session-swipe-btn is-pin\"" in src, "手机左滑没有置顶按钮"
        assert "onTogglePin?.(c.id, !c.pinned)" in src, "两个入口没有接到置顶回调"
        assert src.count("onTogglePin?.(c.id, !c.pinned)") >= 2, (
            "桌面 hover / 手机左滑必须各有一个置顶入口，只接了一处"
        )
        assert "已置顶" in src, "置顶标题没有视觉标识"

    def test_swipe_width_matches_buttons(self):
        src = _read(SIDEBAR)
        css = _read(COMPONENTS_CSS)
        m = re.search(r"const SWIPE_W = (\d+);", src)
        assert m, "SWIPE_W 找不到"
        width = re.search(r"\.session-swipe-wrap \.session-swipe-acts\s*\{[^}]*?width:\s*(\d+)px", css, re.S)
        assert width, "操作区宽度没定义"
        assert int(m.group(1)) == int(width.group(1)) == 144, (
            "三格操作区宽度必须 144（48×3，手机抽屉里不遮标题），且 JS 与 CSS 一致"
        )
        assert ".session-swipe-btn.is-pin" in css, "置顶按钮没有配色"

    def test_swipe_gesture_on_wrap_and_title_yields(self):
        """2026-11-26 用户反馈：左滑后列表被遮、右滑收不回去。三条都要钉住：
        ① 手势挂在 wrap（含操作区）而不是文字层；② 展开时标题主动避让；
        ③ 每格 48px 仍满足 44px 触控基准。"""
        src = _read(SIDEBAR)
        css = _read(COMPONENTS_CSS)
        assert 'className={`session-swipe-wrap${swiped ? " is-swiped" : ""}`}' in src, (
            "wrap 没有展开态类名，标题无法避让"
        )
        assert 'e.currentTarget.querySelector(".session-swipe-acts")' in src, (
            "手势要从 wrap 直接查操作区；从 .session 的 parentElement 取说明手势还挂在文字层"
        )
        assert "parentElement?.querySelector" not in src, "旧的手势挂载写法还在"
        # 文字层（role=button 那一行）不应再挂 touch：操作区展开后那片区域事件进不来
        btn = re.search(r'role="button"[\s\S]{0,1400}?className=\{`session group', src)
        assert btn and "onTouchStart" not in btn.group(0), (
            "手势还挂在文字层，从展开的操作区上右滑收不到事件"
        )
        for h in ("onTouchStart", "onTouchMove", "onTouchEnd", "onTouchCancel"):
            assert h in src, f"缺手势 {h}"
        yield_rule = re.search(r"\.session-swipe-wrap\.is-swiped \.session\s*\{([^}]*)\}", css)
        assert yield_rule, "展开态没有给标题留位置，按钮会盖住标题"
        assert re.search(r"padding-right:\s*148px", yield_rule.group(1)), (
            "标题避让必须覆盖 144px 操作区 + 4px 缝隙"
        )
        assert re.search(r"\.session-swipe-wrap\.is-swiped \.session-hover-acts\s*\{[^}]*display:\s*none", css), (
            "桌面 hover 按钮组在展开态没隐藏：它透明但仍占宽，会把标题挤成空白"
        )
        assert 'className="session-hover-acts pointer-events-none' in src, (
            "悬停按钮组没有语义类名，展开态无法只针对它隐藏"
        )
        w = re.search(r"\.session-swipe-wrap \.session-swipe-acts\s*\{[^}]*?width:\s*(\d+)px", css, re.S)
        assert w and int(w.group(1)) / 3 >= 44, "单格宽度低于 44px 触控基准"

    def test_hook_api_and_provider_wired(self):
        assert "pinSession" in _read(API_TS), "API 客户端没有置顶请求"
        hook = _read(USE_SESSIONS)
        assert "const togglePin" in hook and "togglePin," in hook, "hook 没导出 togglePin"
        assert "pinned: boolean" in _read(TYPES_TS), "Conversation 类型缺 pinned"
        provider = _read(CHAT_PROVIDER)
        assert "togglePin: SessionsStore[\"togglePin\"]" in provider, "RailStore 没暴露 togglePin"
        assert "onTogglePin={rail.togglePin}" in _read(CONSOLE_LAYOUT), "侧栏没拿到置顶回调"
