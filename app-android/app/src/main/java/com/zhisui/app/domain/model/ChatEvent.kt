package com.zhisui.app.domain.model

/**
 * 线协议 → 领域事件的翻译结果。
 *
 * 后端 `POST /api/chat/stream` 实际下发的事件有：status / token / sources / grounding /
 * title / tool / hybrid_start / mode_switch / approval / done / error。
 * 首版只保留与阅读体验相关的六种，其余在接入真实 API 时按需扩展，
 * 扩展点是这个 sealed 接口——UI 不需要知道 SSE 的存在。
 */
sealed interface ChatEvent {
    data class Status(val text: String) : ChatEvent

    /** 增量文本，不是累计全文。客户端负责累加。 */
    data class Token(val delta: String) : ChatEvent

    data class Sources(val items: List<Source>) : ChatEvent

    data class Grounding(val value: com.zhisui.app.domain.model.Grounding) : ChatEvent

    data class Done(
        val messageId: MessageId,
        val answer: String,
        val title: String,
        val mode: RouteMode,
    ) : ChatEvent

    data class Failed(val detail: String) : ChatEvent
}
