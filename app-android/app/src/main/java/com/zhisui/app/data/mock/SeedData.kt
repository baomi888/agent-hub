package com.zhisui.app.data.mock

import com.zhisui.app.domain.model.Conversation
import com.zhisui.app.domain.model.ConversationId
import com.zhisui.app.domain.model.Grounding
import com.zhisui.app.domain.model.Message
import com.zhisui.app.domain.model.MessageId
import com.zhisui.app.domain.model.RouteMode
import com.zhisui.app.domain.model.Source
import com.zhisui.app.domain.model.Turn
import com.zhisui.app.domain.model.TurnState

/**
 * 预置会话。让 App 一打开就有内容可看，同时也充当"已有多轮历史"的形态样本。
 */
object SeedData {

    private const val MINUTE = 60_000L
    private const val HOUR = 60 * MINUTE
    private const val DAY = 24 * HOUR

    fun conversations(): List<Conversation> {
        val now = System.currentTimeMillis()
        return listOf(
            dddConversation(now),
            studyConversation(now),
            indexConversation(now),
        ).sortedByDescending { it.updatedAt }
    }

    private fun dddConversation(now: Long): Conversation {
        val created = now - 2 * DAY
        val question = Message(
            id = MessageId(1),
            role = com.zhisui.app.domain.model.Role.USER,
            content = "DDD 四层架构怎么落到 Kotlin 上？我们现在是 Python 后端，想给 App 端定一套分层，别照抄服务端那套。",
            createdAt = created,
        )
        val answer = Message(
            id = MessageId(2),
            role = com.zhisui.app.domain.model.Role.ASSISTANT,
            content = """
                客户端不能照抄服务端的分层，有三处语义要换。

                一、仓储的语义变了。
                服务端的 repository 背后是一张表，App 的 repository 背后是"网络 + 本地库"，它要决定离线时读谁、在线时怎么合并。所以接口返回 Flow 而不是实体——调用方订阅状态，不查询快照。

                二、聚合的职责变了。
                服务端聚合守事务不变式，客户端聚合守交互状态机。Conversation 只要守住"同一时刻一路流"和"已结束的轮次不再接受增量"这两条，就能消掉大部分 UI 抖动。

                三、防腐层的对象变了。
                客户端要防的是后端 JSON。DTO 直接当 UI 模型是最大的债，改一个字段名就是全 App 崩溃。ChatResponseDto → ChatEvent → UiState 要有两次映射，中间那层是领域。

                首期只接 6 个前缀就够：auth、sessions、chat、kb、path、profile。
            """.trimIndent(),
            createdAt = created + 40_000,
        )
        return Conversation.restore(
            id = ConversationId("seed-ddd"),
            title = "DDD 分层怎么落到 Kotlin",
            createdAt = created,
            updatedAt = created + 40_000,
            route = RouteMode.AGENT,
            turns = listOf(
                Turn(
                    question = question,
                    answer = answer,
                    state = TurnState.DONE,
                    route = RouteMode.AGENT,
                    sources = listOf(
                        Source("DDD 四层架构落地笔记.md", "接口层只做协议转换与参数校验，应用层负责用例编排与事务边界。", 0.91),
                        Source("Kotlin 架构约束实践.md", "用 internal 与 Konsist 断言在构建期阻止 domain 引用框架类型。", 0.87),
                    ),
                    grounding = Grounding("HIGH", "依据 2 篇资料，未引入外部事实", 2),
                ),
            ),
        )
    }

    private fun studyConversation(now: Long): Conversation {
        val created = now - 6 * HOUR
        val question = Message(
            id = MessageId(3),
            role = com.zhisui.app.domain.model.Role.USER,
            content = "这周只剩五天，帮我安排一下复习，数据结构和数据库都要顾到。",
            createdAt = created,
        )
        val answer = Message(
            id = MessageId(4),
            role = com.zhisui.app.domain.model.Role.ASSISTANT,
            content = """
                五天按 3 + 2 分，不要平均分。

                前三天铺数据结构：数组链表、栈队列、哈希表、二叉树遍历，每天两个知识点加两道手写题。看题解会产生"我会了"的错觉，必须手写。

                后两天补数据库：B+ 树索引、事务隔离级别、慢查询分析。你项目里在用 SQLite，可以对着执行计划看，比背八股有效。

                每天末尾留三十分钟做错题回顾，只记"为什么错"，不记正确答案。这样五天下来能覆盖 7 个知识点，比均匀推进多两个。
            """.trimIndent(),
            createdAt = created + 35_000,
        )
        return Conversation.restore(
            id = ConversationId("seed-study"),
            title = "五天复习怎么排",
            createdAt = created,
            updatedAt = created + 35_000,
            route = RouteMode.WORKFLOW,
            turns = listOf(
                Turn(
                    question = question,
                    answer = answer,
                    state = TurnState.DONE,
                    route = RouteMode.WORKFLOW,
                    sources = listOf(
                        Source("学习路径 · 数据结构基础", "第一关共 8 个知识点，当前进度 5/8。", 0.88),
                    ),
                    grounding = Grounding("MEDIUM", "框架来自模板，任务量按你的进度推算", 1),
                ),
            ),
        )
    }

    private fun indexConversation(now: Long): Conversation {
        val created = now - 26 * HOUR
        val question = Message(
            id = MessageId(5),
            role = com.zhisui.app.domain.model.Role.USER,
            content = "为什么 MySQL 索引用 B+ 树而不是红黑树或 B 树？",
            createdAt = created,
        )
        val answer = Message(
            id = MessageId(6),
            role = com.zhisui.app.domain.model.Role.ASSISTANT,
            content = """
                不是"平衡"决定的，是三件事同时成立。

                矮胖。InnoDB 按 16KB 页读写，非叶子节点只存键值和页号，一页放几百个键，三层就能覆盖两千万行，IO 压到三次以内。红黑树是二叉树，同样数据量要二十多层。

                范围查询。B+ 树叶子节点用链表串起来，between 和 order by 顺着链表扫。B 树要中序回跳，红黑树更做不到。

                页对齐。节点按页设计，一次预读一整页，不会读半页浪费 IO。

                代价是写放大，插入会触发页分裂。所以索引不是越多越好，explain 里出现 Using filesort 才值得加。
            """.trimIndent(),
            createdAt = created + 30_000,
        )
        return Conversation.restore(
            id = ConversationId("seed-index"),
            title = "索引为什么用 B+ 树",
            createdAt = created,
            updatedAt = created + 30_000,
            route = RouteMode.AGENT,
            turns = listOf(
                Turn(
                    question = question,
                    answer = answer,
                    state = TurnState.DONE,
                    route = RouteMode.AGENT,
                    sources = listOf(
                        Source("MySQL 索引原理笔记.md", "InnoDB 以 16KB 页为单位读写，三层 B+ 树可容纳约两千万行。", 0.93),
                    ),
                    grounding = Grounding("HIGH", "依据笔记原文，含页大小与层数", 1),
                ),
            ),
        )
    }
}
