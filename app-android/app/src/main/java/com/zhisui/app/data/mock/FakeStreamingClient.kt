package com.zhisui.app.data.mock

import com.zhisui.app.domain.model.ChatEvent
import com.zhisui.app.domain.model.MessageId
import com.zhisui.app.domain.model.RouteMode
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow

/**
 * 假的流式客户端：把 [Reply] 拆成与真实 SSE 同构的事件序列。
 *
 * 接入真实接口时，这个类会被 `RemoteChatClient`（OkHttp 逐行解析 text/event-stream）替换，
 * 两者都满足 `(String) -> Flow<ChatEvent>`，所以 ViewModel 无感知。
 */
class FakeStreamingClient(
    private val charDelayMs: Long = 18L,
    private val statusDelayMs: Long = 520L,
    private val chunkSize: Int = 2,
) {

    fun stream(reply: Reply, messageId: MessageId, mode: RouteMode): Flow<ChatEvent> = flow {
        reply.statusSteps.forEachIndexed { index, step ->
            if (index > 0) delay(statusDelayMs)
            emit(ChatEvent.Status(step))
        }

        if (reply.sources.isNotEmpty()) {
            delay(statusDelayMs / 2)
            emit(ChatEvent.Sources(reply.sources))
            reply.grounding?.let {
                delay(220)
                emit(ChatEvent.Grounding(it))
            }
        }

        delay(statusDelayMs / 2)

        var cursor = 0
        val text = reply.body
        while (cursor < text.length) {
            val end = minOf(cursor + chunkSize, text.length)
            emit(ChatEvent.Token(text.substring(cursor, end)))
            cursor = end
            delay(charDelayMs)
        }

        emit(ChatEvent.Done(messageId, text, reply.title, mode))
    }
}
