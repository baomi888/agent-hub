"""倒计时 ↔ 待办的结构契约（Phase 3，实施单 §7.2）。

为什么这里只钉「结构」而不钉「算法结果」：日期换算的正确性在
`tools/ddl_cloud_acceptance.js` 里用真后端 + 真页面验过（真实时区、
真实 JSON 序列化）。那套断言跑一趟要起服务，不能放进每次 pytest。
这里钉的是**那些验收脚本抓不到、但改一行代码就会失效的约定**：

  · `due_at` 必须是当天 23:59:59 —— 改成 00:00 时「还剩几天」全对，
    只有跨时区/按天展示的地方会错一天，界面上看不出来；
  · 未登录分支不许出现任何 `/api/tasks` 调用 —— 这一页的承诺是免登录可用；
  · 迁移必须「成功一条少一条」，否则重试会造出两份一样的 DDL；
  · 迁完清本地那份时不能 `silent` —— 否则云端快照留着旧数据，换设备会复活。

按项目惯例：断言只读代码，注释先剥掉（解释性的注释会把自己判红）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAMPUS = ROOT / "frontend" / "src" / "campus"
LOGIC = CAMPUS / "logic" / "deadlineCloud.ts"
TOOL = CAMPUS / "tools" / "deadline" / "DeadlineTool.tsx"


def _strip_comments(src: str) -> str:
    src = re.sub(r"\{/\*.*?\*/\}", "", src, flags=re.S)
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"^\s*//.*$", "", src, flags=re.M)
    return src


def _logic() -> str:
    return _strip_comments(LOGIC.read_text(encoding="utf-8"))


def _tool() -> str:
    return _strip_comments(TOOL.read_text(encoding="utf-8"))


def _component(name: str) -> str:
    """取某个组件函数体：从 `function X(` 到下一个顶层 `function ` / 文件末。"""
    src = _tool()
    start = src.index(f"function {name}(")
    rest = src[start + 1 :]
    nxt = rest.find("\nfunction ")
    return rest if nxt < 0 else rest[:nxt]


class TestDueAtPrecision:
    """`due_at` 只能是当天最后一秒，不能是零点。"""

    def test_end_of_day_not_midnight(self):
        logic = _logic()
        assert "23, 59, 59, 0" in logic, "due_at 又改回当天 00:00 了 —— 会差一天"
        # 零点的话这三个参数会是 0, 0, 0；出现在 dueAtFromDate 里就是退回去了
        body = logic[
            logic.index("export function dueAtFromDate") : logic.index("export function dateFromDueAt")
        ]
        assert "0, 0, 0, 0" not in body, "dueAtFromDate 里出现了零点"

    def test_reads_back_through_start_of_day(self):
        """读回来必须收敛到本地零点，与「还剩几天」同一口径。"""
        assert "return startOfDay(sec * 1000);" in _logic()


class TestRowsAreAView:
    """倒计时是 `tasks` 的视图：只留未完成、有截止日的。"""

    def test_skips_completed(self):
        assert 'if (t.status !== "open") continue;' in _logic()

    def test_skips_undated(self):
        assert "if (t.due_at === null || t.due_at === undefined) continue;" in _logic()

    def test_sorted_by_date(self):
        """排序与 `sortByDate` 同序：日期升序、同日按名称。"""
        assert "a.date === b.date ? a.name.localeCompare(b.name)" in _logic()


class TestMigrationIsResumable:
    """迁移必须是「成功一条少一条」，否则重试会重复。"""

    def test_stops_at_first_failure_and_keeps_the_rest(self):
        logic = _logic()
        body = logic[logic.index("export async function migrateLocalDeadlines") :]
        # 失败后剩余项直接进 left 并 continue（不再继续试）
        assert "if (error !== null) {" in body
        assert "left.push(item);" in body
        assert "moved += 1;" in body

    def test_does_not_add_origin_column(self):
        """刻意不给 tasks 加 `origin` 列：那会把「两条同名同日 DDL」也堵掉。"""
        assert "origin" not in _logic()


class TestLoginModes:
    """未登录照旧能用；只有登录后才碰 tasks。"""

    def test_未登录走本地(self):
        assert 'return me ? <CloudDeadline uid={me.user.id} /> : <LocalDeadline />;' in _tool()

    def test_loading_does_not_flash_local_data(self):
        """loading 期间渲染占位：先画本地再跳云端，列表会闪一下。"""
        tool = _tool()
        body = tool[tool.index("export default function DeadlineTool") : tool.index("function DeadlinePanel")]
        assert "if (loading) {" in body
        assert "return me ?" in body
        # 占位分支里不许出现本地数据读取
        assert "readWithMigration" not in body

    def test_local_mode_never_calls_tasks_api(self):
        """本地分支里出现 api.tasks 就等于把免登录承诺弄丢了。"""
        local = _component("LocalDeadline")
        assert "api.tasks" not in local, "未登录分支调了 tasks 接口，免登录就废了"

    def test_cloud_mode_uses_inbox_list(self):
        cloud = _component("CloudDeadline")
        assert cloud.count('list_key: "inbox"') == 2, (
            "新建与迁移各要写一次 list_key: inbox（少一处会掉进默认清单）"
        )


class TestLocalSnapshotHandling:
    def test_clearing_local_snapshot_is_not_silent(self):
        """迁完清本地那份时不能 silent：要让云端快照跟着空，否则换设备会复活旧数据。"""
        cloud = _component("CloudDeadline")
        assert "writeJson(CAMPUS_KEYS.deadline, []);" in cloud
        assert "writeJson(CAMPUS_KEYS.deadline, result.left);" in cloud

    def test_migration_flag_is_per_account(self):
        """换个人登录不该继承上一个人的「已迁过」。"""
        tool = _tool()
        assert "function migratedKey(uid: string)" in tool
        assert "${CAMPUS_KEYS.deadline}.migrated.${uid}" in tool

    def test_partial_failure_writes_back_the_remainder(self):
        cloud = _component("CloudDeadline")
        assert "result.error === null" in cloud
        assert "result.left" in cloud


class TestTouchTarget:
    def test_row_buttons_are_44(self):
        css = (CAMPUS / "styles" / "campus.css").read_text(encoding="utf-8")
        block = css[css.index(".campus-dl-item .campus-btn {") :]
        block = block[: block.index("}")]
        assert "min-height: 44px;" in block, "行内「改 / 交了」又缩回 28px 了"
