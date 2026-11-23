package com.zhisui.app.data.mock

import com.zhisui.app.domain.repository.LevelProgress
import com.zhisui.app.domain.repository.StudyRepository
import com.zhisui.app.domain.repository.StudySnapshot
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

class MockStudyRepository : StudyRepository {

    private val _state = MutableStateFlow(
        StudySnapshot(
            levels = listOf(
                LevelProgress("ds", "数据结构基础", "数组 · 哈希 · 二叉树", 5, 8),
                LevelProgress("os", "操作系统", "调度 · 内存 · IO 模型", 3, 7),
                LevelProgress("net", "计算机网络", "TCP 状态机 · HTTP/2", 2, 6),
                LevelProgress("algo", "算法专题", "二分 · 双指针 · 动态规划", 4, 10),
                LevelProgress("mock", "模拟面试", "限时 45 分钟 · 项目追问", 0, 5),
            ),
            checkedInToday = false,
            streakDays = 12,
            weeklyMinutes = listOf(45, 70, 30, 95, 60, 20, 0),
            todayGoal = 3,
            todayDone = 2,
        ),
    )

    override fun observe(): StateFlow<StudySnapshot> = _state

    override fun toggleCheckIn() {
        val current = _state.value
        _state.value = current.copy(
            checkedInToday = !current.checkedInToday,
            streakDays = if (current.checkedInToday) current.streakDays - 1 else current.streakDays + 1,
        )
    }

    override fun advance(levelId: String) {
        val current = _state.value
        _state.value = current.copy(
            levels = current.levels.map { level ->
                if (level.id == levelId && level.done < level.total) {
                    level.copy(done = level.done + 1)
                } else {
                    level
                }
            },
            todayDone = (current.todayDone + 1).coerceAtMost(current.todayGoal),
        )
    }
}
