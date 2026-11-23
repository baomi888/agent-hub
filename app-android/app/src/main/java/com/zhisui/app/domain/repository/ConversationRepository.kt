package com.zhisui.app.domain.repository

import com.zhisui.app.domain.model.ChatEvent
import com.zhisui.app.domain.model.Conversation
import com.zhisui.app.domain.model.ConversationId
import com.zhisui.app.domain.model.RouteMode
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.StateFlow

/**
 * 会话仓储。接口定义在 domain，实现放在 data。
 *
 * 之所以返回 Flow 而不是实体：客户端的“数据”是持续变化的本地状态 + 网络流，
 * 调用方订阅状态而不是查询快照，这是与服务端仓储最大的语义差别。
 */
interface ConversationRepository {

    fun observeAll(): StateFlow<List<Conversation>>

    fun observe(id: ConversationId): Flow<Conversation?>

    fun create(title: String = Conversation.DEFAULT_TITLE): ConversationId

    fun delete(id: ConversationId)

    fun setRoute(id: ConversationId, mode: RouteMode)

    /**
     * 发送一轮消息。事件流既驱动 UI，也由实现方同步落进会话状态。
     * 取消该 Flow 等价于用户点“停止生成”。
     */
    fun send(id: ConversationId, text: String): Flow<ChatEvent>

    /** 用户主动停止。幂等。 */
    fun abort(id: ConversationId)
}
