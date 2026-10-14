// 混合架构执行轨迹：把 workflow_node / mode_switch 事件渲染成一条时间轴
//
// 设计要点（对着「苞米地笔记本」那套暖米黄 + 手写感）：
//  · 节点用左侧竖线串起来，切换点用金色药丸标出 —— 一眼看出「什么时候换了执行者」
//  · 失败节点用警示色，且**把失败原因显示出来**（藏起来的失败等于没失败）
//  · 进行中的节点用呼吸点表示，不用旋转图标（与全局「不做高动画」一致）
//
// ⚠️ 不用 emoji —— 全项目统一用手绘风 CornIcon。

import CornIcon from "@/components/CornIcon";
import type { TraceItem } from "@/lib/types";

const NODE_LABEL: Record<string, string> = {
  llm: "模型",
  tool: "工具",
  agent: "智能体",
  condition: "分支",
  human: "人工",
};

/** rule 的中文说明。系统默认四条 + 人工 + 上限收工，其余回显原名。 */
function ruleLabel(rule: string): string {
  switch (rule) {
    case "manual":
      return "人工接管";
    case "__max_switches__":
      return "切换次数达上限，收工";
    case "wf_requires_agent":
      return "节点请求交给智能体";
    case "wf_node_failure":
      return "节点失败，降级接管";
    case "agent_budget_exhausted":
      return "预算耗尽，收编回流程";
    case "agent_goal_satisfied":
      return "子目标完成，继续流程";
    default:
      return rule;
  }
}

export function HybridTraceView({
  items,
  running,
  mode,
  fallback,
}: {
  items: TraceItem[];
  running?: boolean;
  mode?: string | null;
  /** 用户选了 hybrid 但后端静默回退了（没走混合） */
  fallback?: boolean;
}) {
  if (items.length === 0 && !running && !fallback) return null;

  return (
    <div className="hybrid-trace" aria-live="polite">
      <div className="hybrid-trace-head">
        <CornIcon name="bolt" />
        <span>执行轨迹</span>
        {mode && <em className="hybrid-mode">{mode === "workflow" ? "工作流" : "智能体"}</em>}
        {running && <em className="hybrid-running">进行中</em>}
      </div>

      {fallback && (
        <p className="hybrid-fallback">
          混合模式当前不可用，本轮已按「智能体」执行。
        </p>
      )}

      {items.length === 0 ? (
        <p className="hybrid-empty">本轮没有节点轨迹（任务较短时后端不会拆节点）。</p>
      ) : (
        <ol className="hybrid-steps">
          {items.map((it, i) =>
            it.kind === "switch" ? (
              <li key={`sw-${i}`} className="hybrid-switch">
                <CornIcon name="refresh" />
                <span className="hybrid-switch-text">
                  {it.from === "workflow" ? "工作流" : "智能体"} → {it.to === "workflow" ? "工作流" : "智能体"}
                </span>
                <span className="hybrid-switch-reason">{it.reason || ruleLabel(it.rule)}</span>
              </li>
            ) : (
              <li key={`nd-${i}`} className={`hybrid-node is-${it.status}`}>
                <span className="hybrid-dot" aria-hidden="true">
                  {it.status === "ok" ? (
                    <CornIcon name="check" />
                  ) : it.status === "failed" ? (
                    <CornIcon name="warn" />
                  ) : null}
                </span>
                <span className="hybrid-node-name">{it.name}</span>
                <span className="hybrid-node-type">{NODE_LABEL[it.type] ?? it.type}</span>
                {it.error && <span className="hybrid-node-error">{it.error}</span>}
              </li>
            )
          )}
        </ol>
      )}
    </div>
  );
}

/**
 * 人工接管按钮组。
 *
 * ⚠️ 展示条件严格按后端文档：只有「收到过 hybrid_start 且还没收到 done」才显示 ——
 * 因为指令不会立即中断，引擎要到安全点才让出；任务已经结束的话点了只会拿到 409。
 */
export function HybridSwitchBar({
  onSwitch,
  busy,
}: {
  onSwitch: (action: "to_agent" | "to_workflow" | "terminate") => void;
  busy?: boolean;
}) {
  return (
    <div className="hybrid-switchbar">
      <button type="button" className="btn-ghost" disabled={busy} onClick={() => onSwitch("to_agent")}>
        交给智能体
      </button>
      <button type="button" className="btn-ghost" disabled={busy} onClick={() => onSwitch("to_workflow")}>
        收回工作流
      </button>
      <button type="button" className="btn-ghost danger" disabled={busy} onClick={() => onSwitch("terminate")}>
        终止
      </button>
    </div>
  );
}
