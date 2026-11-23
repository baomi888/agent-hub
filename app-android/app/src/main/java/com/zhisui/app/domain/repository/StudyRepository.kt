package com.zhisui.app.domain.repository

import com.zhisui.app.domain.model.RouteMode
import com.zhisui.app.domain.model.UsageQuota
import kotlinx.coroutines.flow.StateFlow

data class LevelProgress(
    val id: String,
    val title: String,
    val subtitle: String,
    val done: Int,
    val total: Int,
) {
    val ratio: Float get() = if (total <= 0) 0f else (done.toFloat() / total).coerceIn(0f, 1f)
    val finished: Boolean get() = done >= total
    val unlocked: Boolean get() = true
}

data class StudySnapshot(
    val levels: List<LevelProgress>,
    val checkedInToday: Boolean,
    val streakDays: Int,
    val weeklyMinutes: List<Int>,
    val todayGoal: Int,
    val todayDone: Int,
) {
    val totalDone: Int get() = levels.sumOf { it.done }
    val totalAll: Int get() = levels.sumOf { it.total }
    val overallRatio: Float get() = if (totalAll == 0) 0f else totalDone.toFloat() / totalAll
    val todayRatio: Float get() = if (todayGoal <= 0) 0f else (todayDone.toFloat() / todayGoal).coerceIn(0f, 1f)
}

interface StudyRepository {
    fun observe(): StateFlow<StudySnapshot>
    fun toggleCheckIn()
    fun advance(levelId: String)
}

data class ProfileSnapshot(
    val displayName: String,
    val email: String,
    val joinedDays: Int,
    val quota: UsageQuota,
    val useKnowledgeBase: Boolean,
    val useWebSearch: Boolean,
    val defaultRoute: RouteMode,
)

interface ProfileRepository {
    fun observe(): StateFlow<ProfileSnapshot>
    fun setKnowledgeBase(enabled: Boolean)
    fun setWebSearch(enabled: Boolean)
    fun setDefaultRoute(mode: RouteMode)
}
