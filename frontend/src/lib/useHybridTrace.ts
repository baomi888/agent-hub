// 混合架构执行轨迹的状态管理：把 hybrid_start / workflow_node / mode_switch
// 三类 SSE 事件归并成一条可渲染的时间轴。
//
// 为什么单独抽一个 hook：这三个事件的处理有**跨事件状态**（见下面 nodeNames），
// 塞进 page.tsx 的 switch 里会让那个已经很长的函数继续膨胀，且没法单测。
//
// 契约来源：docs/前端对接接口文档_混合架构_2026-10-14.md §3.3 / §六

import { useCallback, useRef, useState } from "react";
import type {
  HybridLogRow,
  HybridRuntimeMode,
  ModeSwitchEvent,
  TraceItem,
  WorkflowNodeEvent,
} from "@/lib/types";

/** 一条消息上的混合执行轨迹 */
export interface HybridTrace {
  run_id: string | null;
  /** 当前/最终模式；静默回退 agent 时这里会是 agent 而没收到过 hybrid_start */
  mode: HybridRuntimeMode | null;
  items: TraceItem[];
  /** true = 还在跑（收到 hybrid_start 但还没 done） */
  running: boolean;
}

export const EMPTY_TRACE: HybridTrace = {
  run_id: null,
  mode: null,
  items: [],
  running: false,
};

/**
 * 轨迹归并器。**一个消息一个实例**（在发送时创建，随消息一起持久化）。
 *
 * 为什么要 `nodeNames` 这个 Map：
 * 后端的 `workflow_node` **exit 帧只带 node_id，不带 name**（文档 §六.3 明确要求前端自己记）。
 * 所以进节点时要把名字存下来，退出时才能还原成「任务规划 ✓」而不是裸 node_id。
 */
export function useHybridTrace() {
  const [trace, setTrace] = useState<HybridTrace>(EMPTY_TRACE);
  // 跨事件记忆：node_id → { name, type }。刻意用 ref 而非 state ——
  // 它只在事件处理瞬间被读写，不该触发重渲染，也不需要出现在 UI 状态里。
  const nodeNames = useRef<Map<string, { name: string; type: string }>>(new Map());

  const reset = useCallback(() => {
    nodeNames.current.clear();
    setTrace(EMPTY_TRACE);
  }, []);

  /** hybrid_start：起跑即发 */
  const onStart = useCallback((runId: string, mode: HybridRuntimeMode) => {
    nodeNames.current.clear();
    setTrace({ run_id: runId, mode, items: [], running: true });
  }, []);

  /** workflow_node：进出两个形态用同一个入口 */
  const onNode = useCallback((ev: WorkflowNodeEvent) => {
    const nodeId = String(ev.node_id ?? "");
    if (!nodeId) return;

    if (ev.phase === "enter") {
      const type = String(ev.type ?? "llm");
      // 后端保证 name 空时回填为 node_id，但这里仍兜一层，避免空串进 UI
      const name = String(ev.name || nodeId);
      nodeNames.current.set(nodeId, { name, type });
      setTrace((prev) => ({
        ...prev,
        running: true,
        items: [
          ...prev.items,
          { kind: "node", node_id: nodeId, type: type as never, name, status: "running" },
        ],
      }));
      return;
    }

    // exit：用 Map 补回 name；同名节点重复进入时取**最后一次**的记录
    setTrace((prev) => {
      const idx = findLastIndex(prev.items, (it) => it.kind === "node" && it.node_id === nodeId);
      if (idx === -1) {
        // 只收到 exit 没收到 enter（理论不该发生，可能是日志回放时的乱序）。
        // 此时仍要把它记下来，否则这次执行在轨迹上是凭空消失的一段。
        const known = nodeNames.current.get(nodeId);
        return {
          ...prev,
          items: [
            ...prev.items,
            {
              kind: "node",
              node_id: nodeId,
              type: (known?.type ?? "llm") as never,
              name: known?.name ?? nodeId,
              status: ev.status === "failed" ? "failed" : "ok",
              on_fail: ev.on_fail ?? null,
              error: ev.error ?? null,
            },
          ],
        };
      }
      const items = [...prev.items];
      const target = items[idx];
      if (target.kind === "node") {
        items[idx] = {
          ...target,
          status: ev.status === "failed" ? "failed" : "ok",
          on_fail: ev.on_fail ?? null,
          error: ev.error ?? null,
        };
      }
      return { ...prev, items };
    });
  }, []);

  /** mode_switch：既是轨迹的一段，也更新当前模式 */
  const onSwitch = useCallback((ev: ModeSwitchEvent) => {
    setTrace((prev) => ({
      ...prev,
      mode: ev.to,
      items: [
        ...prev.items,
        { kind: "switch", rule: String(ev.rule ?? ""), reason: String(ev.reason ?? ""), from: ev.from, to: ev.to },
      ],
    }));
  }, []);

  /** done：收尾。最终模式以 done.mode 为准（静默回退时是 agent） */
  const onDone = useCallback((finalMode?: string | null) => {
    setTrace((prev) => ({
      ...prev,
      running: false,
      // 只认 workflow / agent 两个值；别把 "rag"/"agent" 之外的塞进来
      mode:
        finalMode === "workflow" || finalMode === "agent"
          ? (finalMode as HybridRuntimeMode)
          : prev.mode,
    }));
  }, []);

  /** 混合静默回退：选了 hybrid 却没收到 hybrid_start，且 done.mode 回了 agent */
  const isFallback = useCallback(
    (finalMode?: string | null) => finalMode === "agent" && trace.run_id === null,
    [trace.run_id]
  );

  return { trace, reset, onStart, onNode, onSwitch, onDone, isFallback };
}

function findLastIndex<T>(arr: T[], pred: (v: T) => boolean): number {
  for (let i = arr.length - 1; i >= 0; i--) if (pred(arr[i])) return i;
  return -1;
}

/**
 * 刷新页面后用 GET /logs 重建轨迹。
 *
 * 为什么需要：轨迹**不随聊天消息持久化**（文档 §七），`done.run_id` + `/logs`
 * 是回显历史轨迹的唯一通道。日志事件名与 SSE 不同名，要在这里翻译：
 *   node_enter → node(running) / node_exit → node(ok|failed) / switch → switch
 */
export function traceFromLogs(logs: HybridLogRow[]): HybridTrace {
  const items: TraceItem[] = [];
  const names = new Map<string, { name: string; type: string }>();
  let runId: string | null = null;

  for (const row of logs) {
    const nodeId = row.node_id ?? "";
    switch (row.event_type) {
      case "node_enter": {
        const name = nodeId || "节点";
        names.set(nodeId, { name, type: "llm" });
        items.push({ kind: "node", node_id: nodeId, type: "llm", name, status: "running" });
        break;
      }
      case "node_exit": {
        const payload = (row.payload ?? {}) as { status?: string; on_fail?: string | null; error?: string | null };
        const status = payload.status === "failed" ? "failed" : "ok";
        const known = names.get(nodeId);
        const idx = findLastIndex(items, (it) => it.kind === "node" && it.node_id === nodeId);
        const item: TraceItem = {
          kind: "node",
          node_id: nodeId,
          type: (known?.type ?? "llm") as never,
          name: known?.name ?? nodeId,
          status,
          on_fail: payload.on_fail ?? null,
          error: payload.error ?? null,
        };
        if (idx === -1) items.push(item);
        else {
          const next = [...items];
          next[idx] = item;
          items.splice(0, items.length, ...next);
        }
        break;
      }
      case "switch": {
        const d = row.decision;
        if (d) {
          items.push({ kind: "switch", rule: d.rule, reason: d.reason, from: d.from, to: d.to });
        }
        break;
      }
      default:
        break;
    }
    if (!runId && typeof row.payload?.run_id === "string") runId = row.payload.run_id;
  }

  return { run_id: runId, mode: null, items, running: false };
}
