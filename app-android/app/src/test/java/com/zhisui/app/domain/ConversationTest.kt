package com.zhisui.app.domain

import com.zhisui.app.domain.model.Conversation
import com.zhisui.app.domain.model.Message
import com.zhisui.app.domain.model.MessageId
import com.zhisui.app.domain.model.Role
import com.zhisui.app.domain.model.TurnState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 聚合状态机的契约测试。纯 JVM，不依赖 Android。
 *
 * 这些用例守的是客户端特有的时序问题：服务端不会出现"先 done 再收 token"，
 * 但移动网络会。所以第 2 条是这个文件里最重要的一个。
 */
class ConversationTest {

    private fun userMessage(id: Long, text: String) =
        Message(id = MessageId(id), role = Role.USER, content = text)

    @Test
    fun `token 增量会累加成完整回答`() {
        var conversation = Conversation.create()
        conversation = conversation.startTurn(userMessage(1, "在吗"))
        conversation = conversation.onToken("你")
        conversation = conversation.onToken("好")

        val turn = conversation.turns.last()
        assertEquals("你好", turn.answer?.content)
        assertEquals(TurnState.STREAMING, turn.state)
        assertTrue(conversation.isStreaming)
    }

    @Test
    fun `已完成轮次丢弃迟到增量`() {
        var conversation = Conversation.create()
        conversation = conversation.startTurn(userMessage(1, "解释索引"))
        conversation = conversation.onToken("B+ 树")
        conversation = conversation.onDone(MessageId(99), "B+ 树胜出", "索引原理")
        val settled = conversation.turns.last().answer?.content

        conversation = conversation.onToken("这段是乱序到达的")

        val turn = conversation.turns.last()
        assertEquals(settled, turn.answer?.content)
        assertEquals(TurnState.DONE, turn.state)
    }

    @Test
    fun `已中断轮次丢弃迟到增量`() {
        var conversation = Conversation.create()
        conversation = conversation.startTurn(userMessage(1, "写个长回答"))
        conversation = conversation.onToken("开头")
        conversation = conversation.onAborted()

        conversation = conversation.onToken("不应该出现")

        val turn = conversation.turns.last()
        assertEquals("开头", turn.answer?.content)
        assertEquals(TurnState.ABORTED, turn.state)
    }

    @Test
    fun `开始新一轮会把未完成的流归档`() {
        var conversation = Conversation.create()
        conversation = conversation.startTurn(userMessage(1, "第一个问题"))
        conversation = conversation.onToken("答了一半")
        conversation = conversation.startTurn(userMessage(2, "第二个问题"))

        assertEquals(2, conversation.turns.size)
        assertEquals(TurnState.ABORTED, conversation.turns[0].state)
        assertEquals(TurnState.STREAMING, conversation.turns[1].state)
        assertEquals(1, conversation.turns.count { it.isStreaming })
    }

    @Test
    fun `done 之后才持有服务端 messageId`() {
        var conversation = Conversation.create()
        conversation = conversation.startTurn(userMessage(1, "问题"))
        conversation = conversation.onToken("部分")

        assertEquals(MessageId(0L), conversation.turns.last().answer?.id)

        conversation = conversation.onDone(MessageId(777), null, null)
        assertEquals(MessageId(777L), conversation.turns.last().answer?.id)
    }

    @Test
    fun `首轮 done 会采纳生成标题`() {
        var conversation = Conversation.create()
        conversation = conversation.startTurn(userMessage(1, "ddd 分层怎么落地"))
        conversation = conversation.onDone(MessageId(2), "回答正文", "DDD 分层落地")

        assertEquals("DDD 分层落地", conversation.title)
    }

    @Test
    fun `非首轮不覆盖已有标题`() {
        var conversation = Conversation.create()
        conversation = conversation.startTurn(userMessage(1, "第一问"))
        conversation = conversation.onDone(MessageId(2), "答", "首个标题")
        conversation = conversation.startTurn(userMessage(3, "第二问"))
        conversation = conversation.onDone(MessageId(4), "答", "第二个标题")

        assertEquals("首个标题", conversation.title)
    }

    @Test
    fun `status 只影响进行中的轮次`() {
        var conversation = Conversation.create()
        conversation = conversation.startTurn(userMessage(1, "问题"))
        conversation = conversation.onStatus("正在检索知识库…")
        assertEquals("正在检索知识库…", conversation.turns.last().status)

        conversation = conversation.onDone(MessageId(2), "答", null)
        assertNull(conversation.turns.last().status)
    }

    @Test
    fun `失败会记录原因并结束流`() {
        var conversation = Conversation.create()
        conversation = conversation.startTurn(userMessage(1, "问题"))
        conversation = conversation.onFailed("连接超时")

        val turn = conversation.turns.last()
        assertEquals(TurnState.FAILED, turn.state)
        assertEquals("连接超时", turn.error)
        assertTrue(!conversation.isStreaming)
    }

    @Test
    fun `预览取最后一条回答的内容`() {
        var conversation = Conversation.create()
        conversation = conversation.startTurn(userMessage(1, "原始问题"))
        conversation = conversation.onToken("生成的回答")

        assertEquals("生成的回答", conversation.preview)
    }
}
