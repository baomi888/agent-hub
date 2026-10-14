"use client";

// 工作流与切换规则管理弹层（hybrid 模式的配置面）。
// 两个页签：
//   工作流 —— 列表 + 结构化节点表单 + 自绘 SVG 拓扑（只读内置不可改删）
//   切换规则 —— 系统默认 + 用户覆盖规则的新增/编辑/启停/删除
// 用 portal 挂到 body（与 FilePreviewModal 同一理由：右栏抽屉的 transform 会裁 fixed 弹层）。
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { api } from "@/lib/api";
import type {
  NodeType,
  SwitchAction,
  SwitchRule,
  WorkflowDef,
  WorkflowEdge,
  WorkflowNode,
  WorkflowRow,
} from "@/lib/types";

const subscribe = () => () => {};
const mountedSnapshot = () => true;
const serverSnapshot = () => false;

const NODE_TYPES: { value: NodeType; label: string; hint: string }[] = [
  { value: "llm", label: "模型", hint: 'config: { "prompt": "…{question}…", "output_key": "x", "stream": false }' },
  { value: "tool", label: "工具", hint: 'config: { "tool": "web_search", "args": { … }, "output_key": "x" }' },
  { value: "agent", label: "自主", hint: 'config: { "goal": "…{shared.x}…", "output_key": "x" }' },
  { value: "condition", label: "分支", hint: 'config: { "expression": "shared.score > 0.8" }' },
  { value: "human", label: "人工", hint: 'config: { "prompt": "请确认后继续" }' },
];

const ACTIONS: { value: SwitchAction; label: string }[] = [
  { value: "to_agent", label: "转交智能体" },
  { value: "to_workflow", label: "转回工作流" },
  { value: "terminate", label: "终止任务" },
];

const nodeTypeLabel = (t: string) => NODE_TYPES.find((x) => x.value === t)?.label ?? t;

// ---------- SVG 拓扑：按 BFS 深度分层布局 ----------
function Topology({ def }: { def: WorkflowDef }) {
  const W = 150;
  const H = 64;
  const NW = 118;
  const NH = 34;
  const depth = new Map<string, number>();
  if (def.entry) {
    depth.set(def.entry, 0);
    const queue = [def.entry];
    while (queue.length) {
      const cur = queue.shift()!;
      for (const e of def.edges.filter((x) => x.source === cur)) {
        const d = (depth.get(cur) ?? 0) + 1;
        if (!depth.has(e.target) || d < depth.get(e.target)!) {
          depth.set(e.target, d);
          queue.push(e.target);
        }
      }
    }
  }
  // 不可达节点（孤儿）排到最后一层，也得让人看见
  const maxReach = Math.max(0, ...Array.from(depth.values()));
  def.nodes.forEach((n) => {
    if (!depth.has(n.id)) depth.set(n.id, maxReach + 1);
  });
  const layers = new Map<number, WorkflowNode[]>();
  def.nodes.forEach((n) => {
    const d = depth.get(n.id) ?? 0;
    layers.set(d, [...(layers.get(d) ?? []), n]);
  });
  const pos = new Map<string, { x: number; y: number }>();
  layers.forEach((nodes, d) => {
    nodes.forEach((n, i) => pos.set(n.id, { x: d * W + 16, y: i * H + 16 }));
  });
  const width = (Math.max(...layers.keys(), 0) + 1) * W + 32;
  const height = Math.max(...Array.from(layers.values()).map((x) => x.length), 1) * H + 32;
  const center = (id: string, side: "l" | "r") => {
    const p = pos.get(id);
    if (!p) return { x: 0, y: 0 };
    return { x: p.x + (side === "r" ? NW : 0), y: p.y + NH / 2 };
  };
  return (
    <svg
      className="wf-topo"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="工作流拓扑图"
    >
      <defs>
        <marker id="wf-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0 0L8 4L0 8z" />
        </marker>
      </defs>
      {def.edges.map((e, i) => {
        const a = center(e.source, "r");
        const b = center(e.target, "l");
        const mx = (a.x + b.x) / 2;
        return (
          <g key={i}>
            <path
              className={`wf-edge ${e.condition ? "cond" : ""}`}
              d={`M${a.x} ${a.y} C ${mx} ${a.y}, ${mx} ${b.y}, ${b.x - 2} ${b.y}`}
              markerEnd="url(#wf-arrow)"
            />
            {e.condition && (
              <text className="wf-edge-label" x={mx} y={(a.y + b.y) / 2 - 5}>
                {e.condition.length > 18 ? e.condition.slice(0, 18) + "…" : e.condition}
              </text>
            )}
          </g>
        );
      })}
      {def.nodes.map((n) => {
        const p = pos.get(n.id)!;
        return (
          <g key={n.id} className={`wf-node t-${n.type}`}>
            <rect x={p.x} y={p.y} width={NW} height={NH} rx={8} />
            <text className="wf-node-name" x={p.x + 10} y={p.y + 15}>
              {(n.name || n.id).length > 9 ? (n.name || n.id).slice(0, 9) + "…" : n.name || n.id}
            </text>
            <text className="wf-node-type" x={p.x + 10} y={p.y + 28}>
              {nodeTypeLabel(n.type)}
              {n.requires_agent ? " · 需Agent" : ""}
              {n.id === def.entry ? " · 入口" : ""}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

// ---------- 空白模板 ----------
function blankDef(): WorkflowDef {
  return {
    id: "",
    name: "新工作流",
    version: 1,
    entry: "n1",
    nodes: [
      {
        id: "n1",
        type: "llm",
        name: "第一步",
        config: { prompt: "请处理：{question}", output_key: "result", stream: false },
      },
    ],
    edges: [],
    timeout_seconds: null,
  };
}

function blankRule(): SwitchRule {
  return {
    name: "",
    signal: "node_failed",
    condition: "",
    action: "to_agent",
    reason: "",
    priority: 100,
    enabled: true,
    cooldown_seconds: 30,
  };
}

interface Props {
  onClose: () => void;
  /** 列表内容变了（新建/改/删），让外面的选择器刷新 */
  onChanged?: () => void;
  onNotice?: (type: "success" | "error" | "info", msg: string) => void;
}

export default function WorkflowManager({ onClose, onChanged, onNotice }: Props) {
  const mounted = useSyncExternalStore(subscribe, mountedSnapshot, serverSnapshot);
  const [tab, setTab] = useState<"flows" | "rules">("flows");
  const cardRef = useRef<HTMLDivElement>(null);
  const [opener] = useState<Element | null>(() =>
    typeof document === "undefined" ? null : document.activeElement
  );
  useEffect(
    () => () => {
      if (opener instanceof HTMLElement) opener.focus?.();
    },
    [opener]
  );
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  // ---------- 工作流列表与编辑器 ----------
  const [flows, setFlows] = useState<WorkflowRow[] | null>(null);
  // undefined = 未选中；null = 正在新建；有值 = 选中已有工作流
  const [selId, setSelId] = useState<string | null | undefined>(undefined);
  const [draft, setDraft] = useState<WorkflowRow | null>(null);
  const [saving, setSaving] = useState(false);

  const loadFlows = useCallback(async () => {
    try {
      const r = await api.listWorkflows();
      setFlows(r.items);
      return r.items;
    } catch (e) {
      onNotice?.("error", e instanceof Error ? e.message : "工作流列表加载失败");
      setFlows([]);
      return [];
    }
  }, [onNotice]);

  useEffect(() => {
    let alive = true;
    api.listWorkflows().then(
      (r) => alive && setFlows(r.items),
      () => alive && setFlows([])
    );
    return () => {
      alive = false;
    };
  }, []);

  const selectFlow = (row: WorkflowRow | null) => {
    if (!row) {
      setSelId(null);
      const def = blankDef();
      setDraft({
        id: "",
        owner_id: "me",
        name: def.name,
        description: "",
        definition: def,
        version: 1,
        builtin: false,
        created_at: 0,
        updated_at: 0,
      });
      return;
    }
    setSelId(row.id);
    // 深拷贝一份再改：draft 是编辑缓冲，不直接动列表里的对象
    setDraft(JSON.parse(JSON.stringify(row)) as WorkflowRow);
  };

  const patchDef = (patch: Partial<WorkflowDef>) => {
    setDraft((d) => (d ? { ...d, definition: { ...d.definition, ...patch } } : d));
  };
  const patchNode = (idx: number, patch: Partial<WorkflowNode>) => {
    setDraft((d) => {
      if (!d) return d;
      const nodes = d.definition.nodes.slice();
      nodes[idx] = { ...nodes[idx], ...patch };
      // 节点 id 改了要同步入口与边，否则图立刻断掉
      const oldId = d.definition.nodes[idx].id;
      const next: WorkflowDef = { ...d.definition, nodes };
      if (patch.id && patch.id !== oldId) {
        if (next.entry === oldId) next.entry = patch.id;
        next.edges = next.edges.map((e) => ({
          ...e,
          source: e.source === oldId ? patch.id! : e.source,
          target: e.target === oldId ? patch.id! : e.target,
        }));
      }
      return { ...d, definition: next };
    });
  };
  const patchEdge = (idx: number, patch: Partial<WorkflowEdge>) => {
    setDraft((d) => {
      if (!d) return d;
      const edges = d.definition.edges.slice();
      edges[idx] = { ...edges[idx], ...patch };
      return { ...d, definition: { ...d.definition, edges } };
    });
  };

  const saveFlow = async () => {
    if (!draft || saving) return;
    const def = { ...draft.definition, name: draft.name };
    if (!def.entry || def.nodes.length === 0) {
      onNotice?.("error", "至少需要一个节点并指定入口");
      return;
    }
    setSaving(true);
    try {
      if (selId && !draft.builtin) {
        await api.updateWorkflow(selId, draft.name, draft.description, def);
        onNotice?.("success", "工作流已保存");
      } else {
        await api.createWorkflow(draft.name, draft.description, def);
        onNotice?.("success", "工作流已创建");
      }
      const items = await loadFlows();
      onChanged?.();
      // 保存后选中刚存的那条，别跳回空编辑态
      const hit = items.find((x) => x.id === selId) ?? items.find((x) => x.name === draft.name);
      if (hit) selectFlow(hit);
    } catch (e) {
      onNotice?.("error", e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const removeFlow = async () => {
    if (!draft || !selId || draft.builtin || saving) return;
    setSaving(true);
    try {
      await api.deleteWorkflow(selId);
      onNotice?.("success", "工作流已删除");
      setDraft(null);
      setSelId(undefined);
      await loadFlows();
      onChanged?.();
    } catch (e) {
      onNotice?.("error", e instanceof Error ? e.message : "删除失败");
    } finally {
      setSaving(false);
    }
  };

  // ---------- 规则 ----------
  const [rules, setRules] = useState<SwitchRule[] | null>(null);
  const [ruleDraft, setRuleDraft] = useState<SwitchRule | null>(null);
  const [ruleSaving, setRuleSaving] = useState(false);

  const loadRules = useCallback(async () => {
    try {
      const r = await api.listRules();
      setRules(r.items);
    } catch (e) {
      onNotice?.("error", e instanceof Error ? e.message : "规则加载失败");
      setRules([]);
    }
  }, [onNotice]);

  useEffect(() => {
    if (tab !== "rules" || rules !== null) return;
    let alive = true;
    api.listRules().then(
      (r) => alive && setRules(r.items),
      () => alive && setRules([])
    );
    return () => {
      alive = false;
    };
  }, [tab, rules]);

  const saveRule = async () => {
    if (!ruleDraft || ruleSaving) return;
    if (!ruleDraft.name.trim()) {
      onNotice?.("error", "规则名不能为空");
      return;
    }
    setRuleSaving(true);
    try {
      await api.upsertRule({ ...ruleDraft, name: ruleDraft.name.trim() });
      onNotice?.("success", "规则已保存");
      setRuleDraft(null);
      await loadRules();
    } catch (e) {
      onNotice?.("error", e instanceof Error ? e.message : "保存失败");
    } finally {
      setRuleSaving(false);
    }
  };

  const removeRule = async (name: string) => {
    try {
      await api.deleteRule(name);
      onNotice?.("success", "用户规则已删除，系统默认规则恢复生效");
      if (ruleDraft?.name === name) setRuleDraft(null);
      await loadRules();
    } catch (e) {
      onNotice?.("error", e instanceof Error ? e.message : "删除失败");
    }
  };

  if (!mounted) return null;

  const readOnly = !!draft?.builtin;

  return createPortal(
    <div
      className="pv-mask"
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div ref={cardRef} className="pv-card wfm-card" role="dialog" aria-modal="true" aria-label="工作流与切换规则管理">
        <header className="pv-head">
          <div className="min-w-0">
            <h2 className="pv-title">混合编排</h2>
            <p className="pv-meta">
              <button
                type="button"
                className={`wfm-tab ${tab === "flows" ? "active" : ""}`}
                onClick={() => setTab("flows")}
              >
                工作流
              </button>
              <button
                type="button"
                className={`wfm-tab ${tab === "rules" ? "active" : ""}`}
                onClick={() => setTab("rules")}
              >
                切换规则
              </button>
            </p>
          </div>
          <button autoFocus onClick={onClose} title="关闭（Esc）" aria-label="关闭" className="pv-close">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </header>

        {tab === "flows" ? (
          <div className="wfm-body">
            {/* 左：列表 */}
            <aside className="wfm-list">
              <button type="button" className="wfm-new" onClick={() => selectFlow(null)}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                  <path d="M12 5v14M5 12h14" />
                </svg>
                新建工作流
              </button>
              {(flows ?? []).map((f) => (
                <button
                  key={f.id}
                  type="button"
                  className={`wfm-row ${selId === f.id ? "active" : ""}`}
                  onClick={() => selectFlow(f)}
                >
                  <span className="wfm-row-name">{f.name}</span>
                  <span className="wfm-row-meta">
                    {f.builtin && <span className="wfm-badge">内置</span>}
                    {f.definition.nodes.length} 节点
                  </span>
                </button>
              ))}
              {flows === null && <p className="wfm-empty">加载中…</p>}
              {flows !== null && flows.length === 0 && <p className="wfm-empty">还没有工作流</p>}
            </aside>

            {/* 右：编辑器 */}
            <div className="wfm-editor">
              {!draft ? (
                <p className="wfm-empty">从左边选一个工作流查看，或新建一个。</p>
              ) : (
                <>
                  {readOnly && (
                    <p className="wfm-note">系统内置工作流只读；如需修改，请基于它新建一个。</p>
                  )}
                  <div className="wfm-grid2">
                    <label className="wfm-field">
                      <span>名称</span>
                      <input
                        value={draft.name}
                        disabled={readOnly}
                        onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                      />
                    </label>
                    <label className="wfm-field">
                      <span>总超时（秒，可空）</span>
                      <input
                        type="number"
                        min={0}
                        value={draft.definition.timeout_seconds ?? ""}
                        disabled={readOnly}
                        onChange={(e) =>
                          patchDef({ timeout_seconds: e.target.value ? Number(e.target.value) : null })
                        }
                      />
                    </label>
                  </div>
                  <label className="wfm-field">
                    <span>描述</span>
                    <input
                      value={draft.description}
                      disabled={readOnly}
                      onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                    />
                  </label>

                  <div className="wfm-sec-head">
                    <span>节点（{draft.definition.nodes.length}）</span>
                    {!readOnly && (
                      <button
                        type="button"
                        className="wfm-mini"
                        onClick={() => {
                          const id = `n${Date.now().toString(36).slice(-4)}`;
                          patchDef({
                            nodes: [
                              ...draft.definition.nodes,
                              { id, type: "llm", name: "", config: {} },
                            ],
                          });
                        }}
                      >
                        + 加节点
                      </button>
                    )}
                  </div>
                  {draft.definition.nodes.map((n, i) => (
                    <div key={i} className="wfm-node">
                      <div className="wfm-grid4">
                        <label className="wfm-field">
                          <span>ID</span>
                          <input value={n.id} disabled={readOnly} onChange={(e) => patchNode(i, { id: e.target.value.trim() })} />
                        </label>
                        <label className="wfm-field">
                          <span>类型</span>
                          <select
                            value={n.type}
                            disabled={readOnly}
                            onChange={(e) => patchNode(i, { type: e.target.value as NodeType })}
                          >
                            {NODE_TYPES.map((t) => (
                              <option key={t.value} value={t.value}>
                                {t.label}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label className="wfm-field">
                          <span>显示名</span>
                          <input value={n.name ?? ""} disabled={readOnly} onChange={(e) => patchNode(i, { name: e.target.value })} />
                        </label>
                        <label className="wfm-field">
                          <span>超时(秒)</span>
                          <input
                            type="number"
                            min={0}
                            value={n.timeout_seconds ?? ""}
                            disabled={readOnly}
                            onChange={(e) => patchNode(i, { timeout_seconds: e.target.value ? Number(e.target.value) : null })}
                          />
                        </label>
                      </div>
                      <div className="wfm-grid4">
                        <label className="wfm-field">
                          <span>重试次数</span>
                          <input
                            type="number"
                            min={0}
                            max={5}
                            value={n.retry ?? 0}
                            disabled={readOnly}
                            onChange={(e) => patchNode(i, { retry: Number(e.target.value) || 0 })}
                          />
                        </label>
                        <label className="wfm-field">
                          <span>失败跳转</span>
                          <select
                            value={n.on_fail ?? ""}
                            disabled={readOnly}
                            onChange={(e) => patchNode(i, { on_fail: e.target.value || null })}
                          >
                            <option value="">（走切换规则）</option>
                            {draft.definition.nodes
                              .filter((x) => x.id !== n.id)
                              .map((x) => (
                                <option key={x.id} value={x.id}>
                                  {x.name || x.id}
                                </option>
                              ))}
                          </select>
                        </label>
                        <label className="wfm-check">
                          <input
                            type="checkbox"
                            checked={!!n.requires_agent}
                            disabled={readOnly}
                            onChange={(e) => patchNode(i, { requires_agent: e.target.checked })}
                          />
                          <span>到此节点移交 Agent</span>
                        </label>
                        {!readOnly && (
                          <button
                            type="button"
                            className="wfm-mini danger"
                            onClick={() =>
                              patchDef({
                                nodes: draft.definition.nodes.filter((_, x) => x !== i),
                                edges: draft.definition.edges.filter((e) => e.source !== n.id && e.target !== n.id),
                              })
                            }
                          >
                            删除节点
                          </button>
                        )}
                      </div>
                      <label className="wfm-field">
                        <span>配置（JSON）— {NODE_TYPES.find((t) => t.value === n.type)?.hint}</span>
                        <textarea
                          rows={3}
                          // 非受控 + 失焦提交：输入到一半的 JSON 必然不合法，
                          // 受控写法会把用户卡死在半路
                          defaultValue={JSON.stringify(n.config ?? {}, null, 2)}
                          disabled={readOnly}
                          spellCheck={false}
                          onBlur={(e) => {
                            try {
                              patchNode(i, { config: JSON.parse(e.target.value || "{}") });
                              e.target.classList.remove("bad");
                            } catch {
                              e.target.classList.add("bad");
                              onNotice?.("error", `节点 ${n.id} 的配置不是合法 JSON，未保存这一段`);
                            }
                          }}
                        />
                      </label>
                    </div>
                  ))}

                  <div className="wfm-sec-head">
                    <span>边（{draft.definition.edges.length}）</span>
                    {!readOnly && draft.definition.nodes.length >= 2 && (
                      <button
                        type="button"
                        className="wfm-mini"
                        onClick={() =>
                          patchDef({
                            edges: [
                              ...draft.definition.edges,
                              { source: draft.definition.nodes[0].id, target: draft.definition.nodes[1].id },
                            ],
                          })
                        }
                      >
                        + 加边
                      </button>
                    )}
                  </div>
                  {draft.definition.edges.map((e, i) => (
                    <div key={i} className="wfm-edge">
                      <select value={e.source} disabled={readOnly} onChange={(ev) => patchEdge(i, { source: ev.target.value })}>
                        {draft.definition.nodes.map((n) => (
                          <option key={n.id} value={n.id}>
                            {n.name || n.id}
                          </option>
                        ))}
                      </select>
                      <span className="wfm-arrow">→</span>
                      <select value={e.target} disabled={readOnly} onChange={(ev) => patchEdge(i, { target: ev.target.value })}>
                        {draft.definition.nodes.map((n) => (
                          <option key={n.id} value={n.id}>
                            {n.name || n.id}
                          </option>
                        ))}
                      </select>
                      <input
                        placeholder="条件表达式（可空）"
                        value={e.condition ?? ""}
                        disabled={readOnly}
                        onChange={(ev) => patchEdge(i, { condition: ev.target.value || null })}
                      />
                      {!readOnly && (
                        <button
                          type="button"
                          className="wfm-mini danger"
                          onClick={() => patchDef({ edges: draft.definition.edges.filter((_, x) => x !== i) })}
                        >
                          删
                        </button>
                      )}
                    </div>
                  ))}

                  <label className="wfm-field">
                    <span>入口节点</span>
                    <select value={draft.definition.entry} disabled={readOnly} onChange={(e) => patchDef({ entry: e.target.value })}>
                      {draft.definition.nodes.map((n) => (
                        <option key={n.id} value={n.id}>
                          {n.name || n.id}
                        </option>
                      ))}
                    </select>
                  </label>

                  <div className="wfm-sec-head">
                    <span>拓扑预览</span>
                  </div>
                  <Topology def={draft.definition} />

                  <div className="wfm-foot">
                    {!readOnly && (
                      <button type="button" className="pv-btn primary" disabled={saving} onClick={saveFlow}>
                        {saving ? "保存中…" : selId ? "保存修改" : "创建工作流"}
                      </button>
                    )}
                    {selId && !readOnly && (
                      <button type="button" className="pv-btn danger" disabled={saving} onClick={removeFlow}>
                        删除
                      </button>
                    )}
                  </div>
                </>
              )}
            </div>
          </div>
        ) : (
          <div className="wfm-body">
            <aside className="wfm-list">
              <button type="button" className="wfm-new" onClick={() => setRuleDraft(blankRule())}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                  <path d="M12 5v14M5 12h14" />
                </svg>
                新建规则
              </button>
              {(rules ?? []).map((r) => (
                <button
                  key={r.name}
                  type="button"
                  className={`wfm-row ${ruleDraft?.name === r.name ? "active" : ""}`}
                  onClick={() => setRuleDraft({ ...r })}
                >
                  <span className="wfm-row-name">
                    {r.name}
                    {!r.enabled && <span className="wfm-badge off">停用</span>}
                  </span>
                  <span className="wfm-row-meta">
                    {r.system && <span className="wfm-badge">默认</span>}
                    {r.signal} → {ACTIONS.find((a) => a.value === r.action)?.label}
                  </span>
                </button>
              ))}
              {rules === null && <p className="wfm-empty">加载中…</p>}
            </aside>

            <div className="wfm-editor">
              {!ruleDraft ? (
                <p className="wfm-empty">从左边选一条规则查看或覆盖，或新建一条。</p>
              ) : (
                <>
                  <p className="wfm-note">
                    与系统默认规则同名即覆盖（删除后恢复默认）；不同名则新增。优先级数字小者先求值。
                  </p>
                  <div className="wfm-grid2">
                    <label className="wfm-field">
                      <span>规则名</span>
                      <input
                        value={ruleDraft.name}
                        placeholder="如 my_node_failure"
                        onChange={(e) => setRuleDraft({ ...ruleDraft, name: e.target.value })}
                      />
                    </label>
                    <label className="wfm-field">
                      <span>触发信号</span>
                      <select
                        value={ruleDraft.signal}
                        onChange={(e) => setRuleDraft({ ...ruleDraft, signal: e.target.value })}
                      >
                        <option value="requires_agent">requires_agent（节点声明移交）</option>
                        <option value="node_failed">node_failed（节点失败）</option>
                        <option value="budget_exhausted">budget_exhausted（预算耗尽）</option>
                        <option value="goal_satisfied">goal_satisfied（子目标完成）</option>
                      </select>
                    </label>
                  </div>
                  <div className="wfm-grid2">
                    <label className="wfm-field">
                      <span>动作</span>
                      <select
                        value={ruleDraft.action}
                        onChange={(e) => setRuleDraft({ ...ruleDraft, action: e.target.value as SwitchAction })}
                      >
                        {ACTIONS.map((a) => (
                          <option key={a.value} value={a.value}>
                            {a.label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="wfm-field">
                      <span>优先级（小者先判）</span>
                      <input
                        type="number"
                        min={0}
                        max={9999}
                        value={ruleDraft.priority}
                        onChange={(e) => setRuleDraft({ ...ruleDraft, priority: Number(e.target.value) || 0 })}
                      />
                    </label>
                  </div>
                  <label className="wfm-field">
                    <span>附加条件（表达式，可空）</span>
                    <input
                      value={ruleDraft.condition}
                      placeholder="如 agent_state.steps > 5"
                      spellCheck={false}
                      onChange={(e) => setRuleDraft({ ...ruleDraft, condition: e.target.value })}
                    />
                  </label>
                  <label className="wfm-field">
                    <span>决策理由（写进日志与前端轨迹）</span>
                    <input
                      value={ruleDraft.reason}
                      placeholder="如：节点连续失败，Agent 降级接管"
                      onChange={(e) => setRuleDraft({ ...ruleDraft, reason: e.target.value })}
                    />
                  </label>
                  <div className="wfm-grid2">
                    <label className="wfm-field">
                      <span>防抖（秒）</span>
                      <input
                        type="number"
                        min={0}
                        max={86400}
                        value={ruleDraft.cooldown_seconds}
                        onChange={(e) => setRuleDraft({ ...ruleDraft, cooldown_seconds: Number(e.target.value) || 0 })}
                      />
                    </label>
                    <label className="wfm-check">
                      <input
                        type="checkbox"
                        checked={ruleDraft.enabled}
                        onChange={(e) => setRuleDraft({ ...ruleDraft, enabled: e.target.checked })}
                      />
                      <span>启用</span>
                    </label>
                  </div>
                  <div className="wfm-foot">
                    <button type="button" className="pv-btn primary" disabled={ruleSaving} onClick={saveRule}>
                      {ruleSaving ? "保存中…" : "保存规则"}
                    </button>
                    {ruleDraft.name && (
                      <button type="button" className="pv-btn danger" onClick={() => void removeRule(ruleDraft.name)}>
                        删除用户规则
                      </button>
                    )}
                  </div>
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </div>,
    document.body
  );
}
