"use client";

// hybrid 执行轨迹时间轴：工作流段（蓝）与 Agent 段（金）异色，
// 切换点用菱形标记展示决策依据（规则名 + 理由）。
// 流式进行中实时展开；结束后收成一条可展开的 summary（与 tool-trace 同模式）。
import type { TraceItem } from "@/lib/types";

const MODE_TEXT: Record<string, string> = { workflow: "工作流", agent: "智能体" };
const NODE_TYPE_TEXT: Record<string, string> = {
  llm: "模型",
  tool: "工具",
  agent: "自主",
  condition: "分支",
  human: "人工",
};

function TraceRow({ t }: { t: TraceItem }) {
  if (t.kind === "start") {
    return (
      <li className={`trace-item ${t.mode ?? ""}`}>
        <span className="trace-dot" aria-hidden="true" />
        <span className="trace-text">
          以<em>{MODE_TEXT[t.mode ?? ""] ?? t.mode}</em>模式起跑
        </span>
      </li>
    );
  }
  if (t.kind === "node_enter") {
    return (
      <li className="trace-item workflow">
        <span className="trace-dot" aria-hidden="true" />
        <span className="trace-text">
          进入节点 <em>{t.nodeName ?? t.nodeId}</em>
          {t.nodeType && <span className="trace-tag">{NODE_TYPE_TEXT[t.nodeType] ?? t.nodeType}</span>}
        </span>
      </li>
    );
  }
  if (t.kind === "node_exit") {
    const failed = t.status === "failed";
    return (
      <li className={`trace-item workflow ${failed ? "failed" : ""}`}>
        <span className="trace-dot" aria-hidden="true" />
        <span className="trace-text">
          节点 <em>{t.nodeName ?? t.nodeId}</em> {failed ? "失败" : "完成"}
          {failed && t.error && <span className="trace-err">{t.error}</span>}
        </span>
      </li>
    );
  }
  // switch：切换点——菱形标记 + 决策依据
  return (
    <li className="trace-item switch">
      <span className="trace-diamond" aria-hidden="true" />
      <span className="trace-text">
        {MODE_TEXT[t.from ?? ""] ?? t.from} → {MODE_TEXT[t.to ?? ""] ?? t.to}
        {t.reason && <span className="trace-reason">{t.reason}</span>}
        {t.rule && <span className="trace-tag">规则 {t.rule}</span>}
      </span>
    </li>
  );
}

export default function TraceTimeline({
  trace,
  streaming,
}: {
  trace: TraceItem[];
  streaming?: boolean;
}) {
  if (trace.length === 0) return null;
  const switches = trace.filter((t) => t.kind === "switch").length;
  const nodes = trace.filter((t) => t.kind === "node_exit").length;

  if (streaming) {
    return (
      <div className="trace-panel" role="status" aria-label="混合执行轨迹">
        <div className="trace-head">
          <span className="tool-spin" aria-hidden="true" />
          <span>混合执行进行中 · {nodes} 个节点 · {switches} 次切换</span>
        </div>
        <ol className="trace-list">
          {trace.map((t, i) => (
            <TraceRow key={i} t={t} />
          ))}
        </ol>
      </div>
    );
  }

  return (
    <details className="trace-panel done">
      <summary>
        执行轨迹：{nodes} 个工作流节点 · {switches} 次模式切换
      </summary>
      <ol className="trace-list">
        {trace.map((t, i) => (
          <TraceRow key={i} t={t} />
        ))}
      </ol>
    </details>
  );
}
