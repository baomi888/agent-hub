package com.zhisui.app

import com.zhisui.app.data.mock.MockConversationRepository
import com.zhisui.app.data.mock.MockProfileRepository
import com.zhisui.app.data.mock.MockStudyRepository
import com.zhisui.app.domain.repository.ConversationRepository
import com.zhisui.app.domain.repository.ProfileRepository
import com.zhisui.app.domain.repository.StudyRepository

/**
 * 手写依赖装配。没有引 Hilt：这里只有三个单例，注解处理器带来的构建成本
 * （KSP + 增量）远大于收益。
 *
 * 接线点只有一处 —— 把 Mock 换成真实实现，App 其余部分不动。
 */
class AppContainer {

    val conversationRepository: ConversationRepository = MockConversationRepository()

    val studyRepository: StudyRepository = MockStudyRepository()

    val profileRepository: ProfileRepository = MockProfileRepository()
}
