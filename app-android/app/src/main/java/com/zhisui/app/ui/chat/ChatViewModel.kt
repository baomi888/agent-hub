package com.zhisui.app.ui.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import com.zhisui.app.domain.model.Conversation
import com.zhisui.app.domain.model.ConversationId
import com.zhisui.app.domain.model.RouteMode
import com.zhisui.app.domain.repository.ConversationRepository
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

class ChatViewModel(
    private val repository: ConversationRepository,
    private val conversationId: ConversationId,
) : ViewModel() {

    val conversation: StateFlow<Conversation?> = repository.observe(conversationId)
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), null)

    private var streamJob: Job? = null

    /**
     * 发送一轮。事件本身由仓储 reduce 进会话状态，这里只负责持有 Job 以便取消。
     */
    fun send(text: String) {
        val trimmed = text.trim()
        if (trimmed.isEmpty() || streamJob?.isActive == true) return
        streamJob = viewModelScope.launch {
            repository.send(conversationId, trimmed).collect { }
        }
    }

    /** 停止生成：取消协程即断开上游，随后把轮次标记为中断。幂等。 */
    fun stop() {
        val job = streamJob ?: return
        streamJob = null
        job.cancel()
        repository.abort(conversationId)
    }

    fun setRoute(mode: RouteMode) {
        if (streamJob?.isActive == true) return
        repository.setRoute(conversationId, mode)
    }

    override fun onCleared() {
        streamJob?.cancel()
        super.onCleared()
    }

    companion object {
        fun factory(
            repository: ConversationRepository,
            id: ConversationId,
        ): ViewModelProvider.Factory = viewModelFactory {
            initializer { ChatViewModel(repository, id) }
        }
    }
}
