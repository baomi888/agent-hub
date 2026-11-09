"""校园同步的**接线**契约（前端源码 ↔ 后端白名单）。

为什么用源码断言而不是跑 JS：这里要钉的不是算法，而是「两份名单有没有走偏」。
`core/campus_store.py` 的 ALLOWED_TOOLS 与 `frontend/src/campus/sync.ts` 的
SYNC_KEYS 必须逐字相同 —— 只改一边的表现是**同步静默失效**（不报错、不同步），
比报错难查得多。

同一批断言还钉住两条禁止项：
  · 番茄钟任何一个键都不能进名单（它有自己的 focusSync，接两遍会丢记录）；
  · `writeJson` 的同步钩子必须能被 `silent` 关掉，否则同步层写回云端数据时会自激。
"""

from __future__ import annotations

import re
from pathlib import Path

from core import campus_store

ROOT = Path(__file__).resolve().parent.parent
CAMPUS = ROOT / "frontend" / "src" / "campus"


def _keys_ts() -> dict[str, str]:
    """keys.ts 里 CAMPUS_KEYS 的「变量名 → 实际键值」。"""
    src = (CAMPUS / "storage" / "keys.ts").read_text(encoding="utf-8")
    block = re.search(r"export const CAMPUS_KEYS = \{(.*?)\} as const;", src, re.S)
    assert block, "keys.ts 里找不到 CAMPUS_KEYS"
    return dict(re.findall(r'(\w+):\s*"([^"]+)"', block.group(1)))


def _sync_keys() -> list[str]:
    """sync.ts 的 SYNC_KEYS 数组实际引用了哪些键。"""
    src = (CAMPUS / "sync.ts").read_text(encoding="utf-8")
    names = re.findall(r"CAMPUS_KEYS\.(\w+)", src)
    # 只取 SYNC_KEYS 数组那一段，避免把文件里其它引用也算进来
    block = re.search(r"const SYNC_KEYS[^=]*=\s*\[(.*?)\];", src, re.S)
    assert block, "sync.ts 里找不到 SYNC_KEYS"
    names = re.findall(r"CAMPUS_KEYS\.(\w+)", block.group(1))
    mapping = _keys_ts()
    return [mapping[n] for n in names]


class TestWhitelistMatches:
    def test_same_five_keys(self):
        assert sorted(_sync_keys()) == sorted(campus_store.ALLOWED_TOOLS)

    def test_exactly_five(self):
        assert len(_sync_keys()) == 5

    def test_no_pomodoro_anywhere_in_sync(self):
        """番茄钟的键在同步文件里**代码**一次都不能出现（注释里解释"为什么不上报"是可以的）。

        第一版断言连注释一起查，被自己写的「番茄钟（campus.pomodoro.*）永不上报」判红 ——
        说明这条断言当时根本没在检查代码，只是在检查文字。
        """
        src = (CAMPUS / "sync.ts").read_text(encoding="utf-8")
        code = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
        code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
        assert "CAMPUS_KEYS.pomodoro" not in code
        assert "CAMPUS_KEYS.focusSessions" not in code
        assert "CAMPUS_KEYS.focusTasks" not in code
        assert "campus.pomodoro" not in code

    def test_backend_refuses_pomodoro_too(self):
        """前后端都要拒绝：只在一边挡，另一边的误报会静默写进库。"""
        for key in ("campus.pomodoro.sessions.v1", "campus.pomodoro.tasks.v1"):
            assert key not in campus_store.ALLOWED_TOOLS
            try:
                campus_store.put("u1", key, {"x": 1}, 1)
            except campus_store.CampusInvalid:
                continue
            raise AssertionError(f"{key} 居然被后端收下了")


class TestStorageHook:
    def test_write_json_calls_sync(self):
        src = (CAMPUS / "storage" / "index.ts").read_text(encoding="utf-8")
        assert "notifySync(key, value)" in src

    def test_silent_write_does_not_notify(self):
        """同步层写回云端数据必须能静默，否则「写入→上报→409→写入」会自激。"""
        src = (CAMPUS / "storage" / "index.ts").read_text(encoding="utf-8")
        assert re.search(r"if \(!opts\?\.silent\) notifySync", src), "silent 分支丢了"

    def test_sync_writes_cloud_back_silently(self):
        src = (CAMPUS / "sync.ts").read_text(encoding="utf-8")
        # 三处静默写：记住水位、采用云端、pull 时落地
        assert src.count("{ silent: true }") >= 3

    def test_pull_is_mounted_on_tools_page(self):
        hub = (CAMPUS / "components" / "CampusToolsHub.tsx").read_text(encoding="utf-8")
        assert "pullCampusState()" in hub

    def test_debounce_is_two_seconds(self):
        """规格写的是防抖 2 秒；改成 0 会让拖课表变成每帧一次 20KB 上传。"""
        src = (CAMPUS / "sync.ts").read_text(encoding="utf-8")
        assert re.search(r"DEBOUNCE_MS\s*=\s*2000", src)

    def test_conflict_uses_cloud_payload(self):
        src = (CAMPUS / "sync.ts").read_text(encoding="utf-8")
        assert "res.status === 409" in src
        assert "cloud.payload" in src


class TestAccountIsolation:
    """同一台浏览器换账号时，不能让上一个人的课表写进下一个人的账号。"""

    def test_pull_reads_owner_from_backend(self):
        src = (CAMPUS / "sync.ts").read_text(encoding="utf-8")
        assert 'body.owner' in src
        assert "campus.sync.owner" in src

    def test_owner_mismatch_clears_local_snapshots(self):
        src = (CAMPUS / "sync.ts").read_text(encoding="utf-8")
        assert "known !== owner" in src
        assert "clearLocalSnapshots()" in src

    def test_logout_clears_campus_local(self):
        auth = (ROOT / "frontend" / "src" / "lib" / "authStore.ts").read_text(encoding="utf-8")
        assert "resetCampusLocal" in auth
        # 必须真的调用，不能只 import
        assert "resetCampusLocal();" in auth


class TestOfflineDurability:
    """离线改动不能被云端旧快照覆盖，也不能卡在 2 秒防抖窗口里丢掉。"""

    def test_local_write_marks_dirty_before_debounce(self):
        src = (CAMPUS / "sync.ts").read_text(encoding="utf-8")
        assert "campus.sync.dirty." in src
        assert "markDirty(key, stamp)" in src
        # dirty 必须在 pending.set 之前落盘
        assert src.index("markDirty(key, stamp)") < src.index("pending.set(key, { value, stamp })")

    def test_pull_uploads_dirty_local_instead_of_overwriting(self):
        src = (CAMPUS / "sync.ts").read_text(encoding="utf-8")
        assert "dirty >= cloudStamp" in src
        assert "putOne(key, local, dirty)" in src

    def test_flush_on_pagehide_and_background(self):
        src = (CAMPUS / "sync.ts").read_text(encoding="utf-8")
        assert 'addEventListener("pagehide"' in src
        assert 'addEventListener("visibilitychange"' in src
        assert "visibilityState" in src
