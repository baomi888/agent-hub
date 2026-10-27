"""混合执行架构：工作流模式 × 自主 Agent 模式。

模块划分：
  context.py       统一任务上下文（TaskContext）与事件/切换记录模型
  expr.py          受限表达式求值器（条件分支与切换规则共用，拒绝任意 eval）
  engine.py        执行引擎契约：EngineResult + ExecutionEngine 协议
  store.py         SQLite 持久化：workflow_defs / workflow_runs / switch_rules / exec_logs
  workflow.py      工作流引擎：节点定义、条件分支、超时控制、节点内重试
  agent_engine.py  Agent 引擎：包装 agent.builder，步数/时长/token 三重预算护栏
  switcher.py      切换引擎：规则链求值、防抖、入口任务分类
  orchestrator.py  混合编排器：引擎注册表 + 执行循环 + 人工切换通道

设计契约：
  - 两种模式之间只通过 TaskContext.shared 交换数据，杜绝私有状态隔空引用；
  - 每次切换整体快照落库（workflow_runs.context），崩溃可从最近快照续跑；
  - 新增执行模式只需实现 ExecutionEngine 协议并注册进 ENGINE_REGISTRY，
    编排器无感知（可扩展性要求的落点）。
"""
