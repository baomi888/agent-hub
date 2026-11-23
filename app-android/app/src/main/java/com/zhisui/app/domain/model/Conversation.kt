package com.zhisui.app.domain.model

import java.util.UUID

/** 会话标识。值对象：包装后不会与其它 String 型 ID 混用。 */
@JvmInline
value class ConversationId(val value: String) {
    companion object {
        fun new(): ConversationId = ConversationId(UUID.randomUUID().toString())
    }
}

@JvmInline
value class MessageId(val value: Long)

/** 执行模式。与后端 `/api/chat/stream` 的 route 对齐：workflow | agent。 */
enum class RouteMode(val label: String, val hint: String) {
    WORKFLOW("工作流", "按预设步骤执行，稳定但固定"),
    AGENT("Agent", "自主规划并调用工具"),
}

enum class Role { USER, ASSISTANT }

/** 一轮对话的生命周期。ABORTED 是用户主动停止，与 FAILED 区分开。 */
enum class TurnState { STREAMING, DONE, ABORTED, FAILED }

/** 知识库来源。对应 SSE 的 `sources` 事件。 */
data class Source(
    val title: String,
    val snippet: String,
    val score: Double,
    val kind: String = "笔记",
)

/** 依据强度。对应 SSE 的 `grounding` 事件。 */
data class Grounding(
    val level: String,
    val note: String,
    val hitCount: Int,
)

data class Message(
    val id: MessageId,
    val role: Role,
    val content: String,
    val createdAt: Long = System.currentTimeMillis(),
)

/**
 * 一轮问答。answer 在流式期间是半成品，只有 Done 时才具备 messageId。
 */
data class Turn(
    val question: Message,
    val answer: Message?,
    val state: TurnState,
    val status: String? = null,
    val route: RouteMode = RouteMode.AGENT,
    val sources: List<Source> = emptyList(),
    val grounding: Grounding? = null,
    val error: String? = null,
) {
    val isStreaming: Boolean get() = state == TurnState.STREAMING
}

/**
 * 会话聚合根。
 *
 * 不变式（由 init 与各方法共同守护）：
 *  1. 同一时刻最多只有一路 STREAMING；
 *  2. 已结束（DONE / ABORTED / FAILED）的轮次不再接受 token —— 移动网络乱序与重连是常态，
 *     迟到的 delta 必须被安静丢弃，而不是抛异常interrupt UI；
 *  3. 只有 DONE 的轮次才持有服务端 messageId。
 *
 * 这是客户端 DDD 与服务端最不同的一处：服务端拒绝非法状态，客户端要消化非法时序。
 */
class Conversation private constructor(
    val id: ConversationId,
    val title: String,
    val createdAt: Long,
    val updatedAt: Long,
    val route: RouteMode,
    val turns: List<Turn>,
) {
    init {
        require(turns.count { it.isStreaming } <= 1) { "同一会话不允许并发两路流" }
    }

    val preview: String
        get() = turns.lastOrNull()?.let { turn ->
            turn.answer?.content?.takeIf { it.isNotBlank() }
                ?: turn.question.content
        } ?: "还没有开始对话"

    val isStreaming: Boolean get() = turns.any { it.isStreaming }

    val totalSources: Int get() = turns.sumOf { it.sources.size }

    fun copy(
        title: String = this.title,
        updatedAt: Long = this.updatedAt,
        route: RouteMode = this.route,
        turns: List<Turn> = this.turns,
    ): Conversation = Conversation(id, title, createdAt, updatedAt, route, turns)

    fun rename(newTitle: String): Conversation = copy(title = newTitle)

    fun withRoute(mode: RouteMode): Conversation = copy(route = mode)

    /** 追加一轮提问。若存在未收尾的流，先按“中断”归档，再开新的一轮。 */
    fun startTurn(question: Message, mode: RouteMode = route): Conversation {
        val settled = turns.map { turn ->
            if (turn.isStreaming) turn.copy(state = TurnState.ABORTED, status = null) else turn
        }
        val fresh = Turn(
            question = question,
            answer = null,
            state = TurnState.STREAMING,
            status = "正在规划任务…",
            route = mode,
        )
        return copy(turns = settled + fresh, route = mode, updatedAt = question.createdAt)
    }

    /** SSE `status`：只更新最新一轮的提示行。 */
    fun onStatus(text: String): Conversation = updateLast { turn ->
        if (turn.isStreaming) turn.copy(status = text) else turn
    }

    /** SSE `sources`：来源可能先于 token 到达，所以挂在 turn 上而不是 answer 上。 */
    fun onSources(items: List<Source>): Conversation = updateLast { turn ->
        if (turn.isStreaming) turn.copy(sources = items) else turn
    }

    fun onGrounding(value: Grounding): Conversation = updateLast { turn ->
        if (turn.isStreaming) turn.copy(grounding = value) else turn
    }

    /** SSE `token`：增量。迟到或越界的 delta 被丢弃（不变式 2）。 */
    fun onToken(delta: String): Conversation = updateLast { turn ->
        if (!turn.isStreaming) {
            turn
        } else {
            val base = turn.answer ?: Message(MessageId(0L), Role.ASSISTANT, "", System.currentTimeMillis())
            turn.copy(answer = base.copy(content = base.content + delta), status = null)
        }
    }

    /** SSE `done`：落最终文本与服务端 messageId，标题只在首轮时采纳。 */
    fun onDone(messageId: MessageId, finalText: String?, generatedTitle: String?): Conversation {
        val settled = updateLast { turn ->
            if (!turn.isStreaming) {
                turn
            } else {
                val text = finalText?.takeIf { it.isNotBlank() }
                    ?: turn.answer?.content.orEmpty()
                val answer = (turn.answer ?: Message(messageId, Role.ASSISTANT, text)).copy(
                    id = messageId,
                    content = text,
                )
                turn.copy(answer = answer, state = TurnState.DONE, status = null)
            }
        }
        val shouldAdoptTitle = turns.size <= 1 && generatedTitle != null &&
            generatedTitle.isNotBlank() && title == DEFAULT_TITLE
        return if (shouldAdoptTitle) settled.copy(title = generatedTitle!!) else settled
    }

    fun onFailed(detail: String): Conversation = updateLast { turn ->
        if (turn.isStreaming) {
            turn.copy(state = TurnState.FAILED, status = null, error = detail)
        } else {
            turn
        }
    }

    fun onAborted(): Conversation = updateLast { turn ->
        if (turn.isStreaming) turn.copy(state = TurnState.ABORTED, status = null) else turn
    }

    private fun updateLast(transform: (Turn) -> Turn): Conversation {
        if (turns.isEmpty()) return this
        return copy(turns = turns.dropLast(1) + transform(turns.last()), updatedAt = System.currentTimeMillis())
    }

    companion object {
        const val DEFAULT_TITLE = "新对话"

        fun create(title: String = DEFAULT_TITLE, route: RouteMode = RouteMode.AGENT): Conversation {
            val now = System.currentTimeMillis()
            return Conversation(
                id = ConversationId.new(),
                title = title,
                createdAt = now,
                updatedAt = now,
                route = route,
                turns = emptyList(),
            )
        }

        /** 用于从持久化或预置数据还原，不做时序校验。 */
        fun restore(
            id: ConversationId,
            title: String,
            createdAt: Long,
            updatedAt: Long,
            route: RouteMode,
            turns: List<Turn>,
        ): Conversation = Conversation(id, title, createdAt, updatedAt, route, turns)
    }
}
