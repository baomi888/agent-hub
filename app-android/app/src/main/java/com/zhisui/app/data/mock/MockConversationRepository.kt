package com.zhisui.app.data.mock

import com.zhisui.app.domain.model.ChatEvent
import com.zhisui.app.domain.model.Conversation
import com.zhisui.app.domain.model.ConversationId
import com.zhisui.app.domain.model.Message
import com.zhisui.app.domain.model.MessageId
import com.zhisui.app.domain.model.Role
import com.zhisui.app.domain.model.RouteMode
import com.zhisui.app.domain.repository.ConversationRepository
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.onCompletion
import java.util.concurrent.atomic.AtomicLong

/**
 * 内存版会话仓储。所有状态都在进程内，杀进程即重置——演示够用。
 *
 * 接真实后端时新增 `RemoteConversationRepository`：
 *  - send() 里换成 OkHttp 的 POST + text/event-stream 逐行解析；
 *  - observeAll() 换成 Room 的 Flow；
 *  - 其余契约不变，因此 ViewModel 与 UI 一行都不用改。
 */
class MockConversationRepository(
    private val client: FakeStreamingClient = FakeStreamingClient(),
) : ConversationRepository {

    private val lock = Any()
    private val _store = MutableStateFlow(SeedData.conversations())
    private val messageIdSeq = AtomicLong(100L)

    override fun observeAll(): StateFlow<List<Conversation>> = _store

    override fun observe(id: ConversationId): Flow<Conversation?> =
        _store.map { list -> list.firstOrNull { it.id == id } }

    override fun create(title: String): ConversationId {
        val conversation = Conversation.create(title)
        synchronized(lock) {
            _store.value = listOf(conversation) + _store.value
        }
        return conversation.id
    }

    override fun delete(id: ConversationId) {
        synchronized(lock) {
            _store.value = _store.value.filterNot { it.id == id }
        }
    }

    override fun setRoute(id: ConversationId, mode: RouteMode) {
        update(id) { it.withRoute(mode) }
    }

    override fun send(id: ConversationId, text: String): Flow<ChatEvent> = flow {
        val question = Message(
            id = MessageId(messageIdSeq.incrementAndGet()),
            role = Role.USER,
            content = text,
            createdAt = System.currentTimeMillis(),
        )
        update(id) { it.startTurn(question) }

        val reply = ReplyLibrary.pick(text)
        val mode = _store.value.firstOrNull { it.id == id }?.route ?: RouteMode.AGENT
        val answerId = MessageId(messageIdSeq.incrementAndGet())

        client.stream(reply, answerId, mode).collect { event ->
            reduce(id, event)
            emit(event)
        }
    }
        .onCompletion { cause ->
            // 取消即视为用户点了「停止生成」：把最新一轮标成 ABORTED，UI 才不会一直转圈。
            if (cause is CancellationException) {
                update(id) { it.onAborted() }
            }
        }
        .flowOn(Dispatchers.Default)

    override fun abort(id: ConversationId) {
        update(id) { it.onAborted() }
    }

    private fun reduce(id: ConversationId, event: ChatEvent) {
        when (event) {
            is ChatEvent.Status -> update(id) { it.onStatus(event.text) }
            is ChatEvent.Token -> update(id) { it.onToken(event.delta) }
            is ChatEvent.Sources -> update(id) { it.onSources(event.items) }
            is ChatEvent.Grounding -> update(id) { it.onGrounding(event.value) }
            is ChatEvent.Done -> update(id) { it.onDone(event.messageId, event.answer, event.title) }
            is ChatEvent.Failed -> update(id) { it.onFailed(event.detail) }
        }
    }

    private fun update(id: ConversationId, transform: (Conversation) -> Conversation) {
        synchronized(lock) {
            val current = _store.value
            val index = current.indexOfFirst { it.id == id }
            if (index < 0) return
            val next = current.toMutableList()
            next[index] = transform(next[index])
            _store.value = next.sortedByDescending { it.updatedAt }
        }
    }
}
