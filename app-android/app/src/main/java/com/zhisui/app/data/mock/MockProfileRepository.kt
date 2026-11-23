package com.zhisui.app.data.mock

import com.zhisui.app.domain.model.RouteMode
import com.zhisui.app.domain.model.UsageQuota
import com.zhisui.app.domain.repository.ProfileRepository
import com.zhisui.app.domain.repository.ProfileSnapshot
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

class MockProfileRepository : ProfileRepository {

    private val _state = MutableStateFlow(
        ProfileSnapshot(
            displayName = "刘佳林",
            email = "liujialin@zhisui.app",
            joinedDays = 128,
            quota = UsageQuota(
                usedTokens = 486_300L,
                limitTokens = 2_000_000L,
                resetLabel = "12 月 1 日重置",
                plan = "内测版",
            ),
            useKnowledgeBase = true,
            useWebSearch = false,
            defaultRoute = RouteMode.AGENT,
        ),
    )

    override fun observe(): StateFlow<ProfileSnapshot> = _state

    override fun setKnowledgeBase(enabled: Boolean) {
        _state.value = _state.value.copy(useKnowledgeBase = enabled)
    }

    override fun setWebSearch(enabled: Boolean) {
        _state.value = _state.value.copy(useWebSearch = enabled)
    }

    override fun setDefaultRoute(mode: RouteMode) {
        _state.value = _state.value.copy(defaultRoute = mode)
    }
}
