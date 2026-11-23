package com.zhisui.app.data.mock

import com.zhisui.app.domain.model.Grounding
import com.zhisui.app.domain.model.Source

/**
 * 一次"假回答"的完整剧本。
 *
 * 字段顺序刻意与真实 SSE 的到达顺序一致：status → sources → grounding → token… → done。
 * 接入真实后端时，这个类整体消失，UI 与状态机完全不用改。
 */
data class Reply(
    val title: String,
    val statusSteps: List<String>,
    val sources: List<Source>,
    val grounding: Grounding?,
    val body: String,
)

/**
 * 离线语料库。按关键词挑一段，覆盖知识库检索、纯知识、学习规划三类路径，
 * 让演示能看出"不同问题走不同处理链"。
 */
object ReplyLibrary {

    private val dddSources = listOf(
        Source("DDD 四层架构落地笔记.md", "接口层只做协议转换与参数校验，应用层负责用例编排与事务边界，领域层承载不变式，基础设施层实现仓储与外部适配。", 0.91),
        Source("Kotlin 架构约束实践.md", "用 internal 可见性配合 Konsist 断言，可以在构建期阻止 domain 层引用框架与基础设施类型。", 0.87),
        Source("agent-hub 分层改造记录.md", "先按限界上下文重切包边界，再决定是否更换语言或框架，避免把同一坨泥球搬进新工程。", 0.79),
    )

    private val studySources = listOf(
        Source("学习路径 · 数据结构基础", "第一关共 8 个知识点：数组与链表、栈与队列、哈希表、二叉树遍历、堆、并查集、排序、二分。", 0.88),
        Source("每日委托模板 · 复习安排", "三轮推进法：第一轮铺面，第二轮补漏，第三轮只做错题回顾，每轮之间留一天缓冲。", 0.83, "模板"),
    )

    private val indexSources = listOf(
        Source("MySQL 索引原理笔记.md", "InnoDB 以 16KB 页为单位读写，非叶子节点只存键值与页号，三层 B+ 树可容纳约两千万行。", 0.93),
        Source("SQL 优化实战清单.md", "explain 出现 Using filesort 或 Using temporary 时再考虑加索引，避免索引膨胀拖慢写入。", 0.81),
    )

    private val ragSources = listOf(
        Source("RAG 全链路设计.md", "混合检索采用 BM25 字符 bigram 与向量双通道，通过 RRF 融合，k 取 60，无需分数归一化。", 0.9),
        Source("检索层性能优化.md", "同一 query 的 embedding 做 single-flight，可避免并发同题重复计费；检索缓存按库维度精确失效。", 0.85),
    )

    fun pick(question: String): Reply {
        val q = question.lowercase()
        return when {
            listOf("ddd", "架构", "分层", "领域", "聚合", "kotlin").any { q.contains(it) } -> Reply(
                title = "DDD 分层如何落到 Kotlin",
                statusSteps = listOf("正在规划任务…", "正在检索知识库…"),
                sources = dddSources,
                grounding = Grounding("HIGH", "本次回答严格依据 3 篇资料，未引入资料之外的事实", 3),
                body = """
                    可以落，但有三处必须改，否则只是把 Java 的分层重新抄一遍。

                    一、依赖方向要能被构建期强制。
                    interfaces → application → domain ← infrastructure，domain 是唯一不带任何框架注解的层。Kotlin 里用 internal 可见性配合 Konsist 断言，能让 domain 里出现 androidx 或 spring 的 import 时直接构建失败。Java 时代这件事只能靠 code review，这是 Kotlin 的实质优势。

                    二、聚合必须有真不变式。
                    如果 Conversation 只是字段容器，它和 DTO 没有区别。它至少要守住两条：同一时刻只有一路流在跑；已完成的轮次不再接受迟到的增量。第二条尤其重要——移动网络乱序是常态，异常抛给 UI 只会让界面崩。

                    三、防腐层不能省。
                    模型供应商、向量库都属于易变外部资源。LlmGateway 这类接口应该由 domain 定义、infrastructure 实现，否则换一次供应商就要改业务代码。

                    顺序上建议先切边界再谈语言。边界在现在的代码里理顺了，换 Kotlin 才有可对照的验收标准。
                """.trimIndent(),
            )

            listOf("复习", "计划", "安排", "学习", "考研", "考试").any { q.contains(it) } -> Reply(
                title = "本周复习计划",
                statusSteps = listOf("正在规划任务…", "正在读取学习路径…", "正在检索知识库…"),
                sources = studySources,
                grounding = Grounding("MEDIUM", "计划框架来自资料，具体到每天的任务量由你的进度推算", 2),
                body = """
                    按三轮推进，每轮之间留一天缓冲，别排满。

                    第一轮，三天，铺数据结构。
                    数组与链表、栈与队列、哈希表、二叉树遍历，每天两个知识点加两道手写题。重点是手写，看题解会产生"我会了"的错觉。

                    第二轮，两天，补数据库。
                    B+ 树索引、事务隔离级别、慢查询分析。你项目里正在用 SQLite，可以对照执行计划看，比看八股文有效。

                    第三轮，一天，只做错题回顾。
                    把前两轮错的题重做一遍，不碰新题。这一步的收益比再刷二十道新题高。

                    每天留三十分钟复盘，写清楚"为什么错"，不要只记正确答案。你现在的关卡进度是数据结构 5/8，第二轮结束后应该能到 7/8。
                """.trimIndent(),
            )

            listOf("索引", "b+", "b+树", "mysql", "数据库", "explain").any { q.contains(it) } -> Reply(
                title = "B+ 树索引原理",
                statusSteps = listOf("正在检索知识库…"),
                sources = indexSources,
                grounding = Grounding("HIGH", "回答依据 2 篇笔记，含具体的页大小与层数", 2),
                body = """
                    胜出的原因不是"平衡"，而是三件事同时成立。

                    一、矮胖。
                    InnoDB 以 16KB 页为单位读写，非叶子节点只存键值和页号，一页能放几百个键。三层树就能覆盖约两千万行，磁盘 IO 被压到三次以内。

                    二、范围查询友好。
                    叶子节点之间用链表串起来，between、order by、limit 顺着链表扫就行。B 树要做中序遍历回跳，红黑树更做不到。

                    三、页对齐。
                    节点大小按页设计，一次预读就是一整页，不会出现读半页浪费 IO 的情况。

                    代价是写放大：插入可能触发页分裂，所以索引不是越多越好。判断标准很具体——explain 里出现 Using filesort 或 Using temporary 才值得加。
                """.trimIndent(),
            )

            listOf("rag", "检索", "知识库", "向量", "召回").any { q.contains(it) } -> Reply(
                title = "混合检索为什么更稳",
                statusSteps = listOf("正在检索知识库…", "正在融合多路召回…"),
                sources = ragSources,
                grounding = Grounding("HIGH", "回答来自检索链路设计文档", 2),
                body = """
                    混合检索比纯向量稳，原因是两者补的是不同的缺口。

                    向量擅长语义相近，但对专有名词和编号很弱。"CUDA 12.4"、"第 3 条"这类字面量，embedding 之后和周围文本几乎没区别。BM25 按词面命中，正好补这一块。

                    融合用 RRF 而不是加权求和，因为两路分数量纲不同，加权要先归一化，而归一化又引入一个拍脑袋的系数。RRF 只看排名，1/(k+rank)，k 取 60，天然对量纲不敏感。

                    工程上有两点容易漏：同一 query 的 embedding 要做 single-flight，否则并发同题会重复付费；检索结果按库、问题、topK 做缓存，库内容变更时精确失效，别整体清空。
                """.trimIndent(),
            )

            else -> Reply(
                title = question.take(14).ifBlank { "新对话" },
                statusSteps = listOf("正在规划任务…"),
                sources = emptyList(),
                grounding = null,
                body = """
                    我在。当前是离线演示模式，回答由本地语料生成，不产生任何网络请求，也不消耗额度。

                    试这几类问题能看到不同的处理路径：
                    「帮我安排这周复习」会走学习路径并带来源；
                    「DDD 分层怎么落地」会先检索知识库再作答；
                    「索引为什么用 B+ 树」是纯知识回答，不挂来源。

                    接入真实后端后，这里换成 POST /api/chat/stream 的 SSE 流，事件格式完全一致，切换只需要替换仓储实现——UI 和状态机一行都不用改。
                """.trimIndent(),
            )
        }
    }

    val suggestions = listOf(
        "帮我安排这周复习",
        "DDD 分层怎么落到 Kotlin",
        "索引为什么用 B+ 树",
        "混合检索为什么更稳",
    )
}
