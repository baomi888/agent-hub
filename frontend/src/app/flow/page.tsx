"use client";

// 混合架构管理面：工作流定义 + 切换规则
//
// 契约来源：docs/前端对接接口文档_混合架构_2026-10-14.md（林宏亮 v1）§5
// ⚠️ 后端截至 10-14 未推到任何远端分支（hybrid/ 与 api/hybrid.py 均 0 文件），
//    所以这一页带**三态降级**：端点 404 时显示演示数据 + 「接口待上线」标记，不是白屏。
//
// ★ 这个页面最容易踩的坑（文档 §六.6 也特意点了）：
// **编辑工作流定义必须用「非受控文本域 + 失焦提交」**。
// 用受控写法（value 绑定 state + onChange）的话，用户输入到一半的
// `{"nodes": [` 一定是**非法 JSON** ⇒ JSON.parse 抛错 ⇒ onChange 里 setState 失败
// ⇒ 字符打不进去 ⇒ 用户连一个括号都删不掉。
// 所以：编辑期间不解析，只在**失焦/点保存**时解析并标红。

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ConsoleShell from "@/components/console/ConsoleShell";
import { Seg, ToastHost } from "@/components/console/ui";
import CornIcon from "@/components/CornIcon";
import { api } from "@/lib/api";
import { useToast } from "@/lib/hooks/useToast";
import type { HybridRunRow, RuleRow, WorkflowRow } from "@/lib/types";
import { KNOWN_SIGNALS } from "@/lib/types";

type Tab = "wf" | "rule" | "run";

/** 演示数据：后端未上线时顶住版面，界面上挂「接口待上线」标记 */
const DEMO_WORKFLOWS: WorkflowRow[] = [
  {
    id: "builtin_plan_execute",
    owner_id: "-",
    name: "规划-执行（内置）",
    description: "先用模型拆步骤，再交给智能体按规划完成",
    version: 1,
    created_at: 0,
    updated_at: 0,
    builtin: true,
    definition: {
      id: "builtin_plan_execute",
      name: "规划-执行（内置）",
      version: 1,
      entry: "plan",
      nodes: [
        { id: "plan", type: "llm", name: "任务规划", config: { prompt: "…{question}", output_key: "plan" } },
        { id: "execute", type: "agent", name: "自主执行", config: { goal: "…{shared.plan}", output_key: "agent_result" } },
      ],
      edges: [{ source: "plan", target: "execute" }],
    },
  },
];

const DEMO_RULES: RuleRow[] = [
  { id: "1", owner_id: "-", name: "wf_requires_agent", signal: "requires_agent", condition: null, action: "to_agent", reason: "工作流节点声明需要自主执行，移交智能体", priority: 10, enabled: true, cooldown_seconds: 0, builtin: true },
  { id: "2", owner_id: "-", name: "wf_node_failure", signal: "node_failed", condition: null, action: "to_agent", reason: "工作流节点失败且无补偿分支，智能体降级接管", priority: 20, enabled: true, cooldown_seconds: 30, builtin: true },
  { id: "3", owner_id: "-", name: "agent_budget_exhausted", signal: "budget_exhausted", condition: null, action: "to_workflow", reason: "智能体预算耗尽，收编回工作流", priority: 20, enabled: true, cooldown_seconds: 30, builtin: true },
  { id: "4", owner_id: "-", name: "agent_goal_satisfied", signal: "goal_satisfied", condition: null, action: "to_workflow", reason: "智能体子目标完成，工作流从断点继续", priority: 30, enabled: true, cooldown_seconds: 0, builtin: true },
];

const DEMO_RUNS: HybridRunRow[] = [
  { run_id: "run-demo-001", task_id: "t1", workflow_id: "builtin_plan_execute", status: "completed", created_at: 0, updated_at: 0 },
];

const ACTION_LABEL: Record<string, string> = {
  to_agent: "交给智能体",
  to_workflow: "收回工作流",
  terminate: "终止",
};

const STATUS_LABEL: Record<string, string> = {
  running: "进行中",
  completed: "已完成",
  failed: "失败",
  timeout: "超时",
  stopped: "已停止",
};

export default function FlowPage() {
  const { toasts, showToast } = useToast();
  const [tab, setTab] = useState<Tab>("wf");
  const [wfs, setWfs] = useState<WorkflowRow[] | null>(null);
  const [rules, setRules] = useState<RuleRow[] | null>(null);
  const [runs, setRuns] = useState<HybridRunRow[] | null>(null);
  const [editing, setEditing] = useState<WorkflowRow | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.hybrid.workflows().then((d) => setWfs(d)).catch(() => setWfs(null));
    api.hybrid.rules().then((d) => setRules(d)).catch(() => setRules(null));
    api.hybrid.runs(20).then((d) => setRuns(d)).catch(() => setRuns(null));
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const isDemo = wfs === null;
  const wfItems = wfs ?? DEMO_WORKFLOWS;
  const ruleItems = rules ?? DEMO_RULES;
  const runItems = runs ?? DEMO_RUNS;

  // 演示模式下所有写操作都要明说没生效，不能假装成功（与四屏同一约定）
  const demoGuard = useCallback(
    (what: string) => {
      if (isDemo) {
        showToast("info", `接口待上线，${what}暂未生效`);
        return true;
      }
      return false;
    },
    [isDemo, showToast]
  );

  const onDeleteWf = useCallback(
    async (row: WorkflowRow) => {
      if (row.builtin) {
        showToast("info", "内置工作流只读，不能删除");
        return;
      }
      if (demoGuard("删除")) return;
      setBusy(true);
      try {
        await api.hybrid.deleteWorkflow(row.id);
        showToast("success", "已删除");
        load();
      } catch {
        showToast("error", "删除失败");
      } finally {
        setBusy(false);
      }
    },
    [demoGuard, isDemo, load, showToast]
  );

  const onSaveWf = useCallback(async () => {
    if (!editing) return;
    if (demoGuard("保存")) {
      setEditing(null);
      return;
    }
    setBusy(true);
    try {
      await api.hybrid.updateWorkflow(editing.id, {
        name: editing.name,
        description: editing.description,
        definition: editing.definition,
      });
      showToast("success", "已保存");
      setEditing(null);
      load();
    } catch {
      showToast("error", "保存失败：可能定义非法（边指向不存在的节点等）");
    } finally {
      setBusy(false);
    }
  }, [demoGuard, editing, err, load, showToast]);

  return (
    <ConsoleShell
      active="flow"
      eyebrow="P6 · 混合架构"
      title="工作流与切换规则"
      sub={
        <>
          工作流决定「按什么步骤走」，切换规则决定「什么时候换人执行」。
          <strong> 这两块目前由后端提供接口，界面这一侧由前端实现。</strong>
        </>
      }
      actions={
        <div className="flow-tabs">
          <Seg
            ariaLabel="管理视图切换"
            options={[
              { value: "wf", label: `工作流 ${wfItems.length}` },
              { value: "rule", label: `切换规则 ${ruleItems.length}` },
              { value: "run", label: `执行记录 ${runItems.length}` },
            ]}
            value={tab}
            onChange={(v) => setTab(v as Tab)}
          />
        </div>
      }
    >
      {isDemo && (
        <p className="flow-demo" role="status">
          <CornIcon name="info" /> 接口待上线，以下是演示数据。
        </p>
      )}

      {tab === "wf" && (
        <ul className="flow-list">
          {wfItems.map((w) => (
            <li key={w.id} className="flow-card">
              <div className="flow-card-main">
                <div className="flow-card-title">
                  {w.name}
                  {w.builtin && <em className="flow-tag">内置</em>}
                  <em className="flow-ver">v{w.version}</em>
                </div>
                {w.description && <p className="flow-desc">{w.description}</p>}
                <details className="flow-detail">
                  <summary>定义（{w.definition?.nodes?.length ?? 0} 个节点）</summary>
                  <pre className="flow-pre">{JSON.stringify(w.definition, null, 2)}</pre>
                </details>
              </div>
              <div className="flow-card-side">
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={busy || w.builtin}
                  title={w.builtin ? "内置工作流只读" : "编辑定义"}
                  onClick={() => {
                    setErr("");
                    setEditing(w);
                  }}
                >
                  编辑
                </button>
                <button
                  type="button"
                  className="btn-ghost danger"
                  disabled={busy || w.builtin}
                  onClick={() => onDeleteWf(w)}
                >
                  删除
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {tab === "rule" && (
        <>
          <p className="flow-hint">
            引擎只会上报 4 个 signal（下面这四个）。自定义 signal 能存下但不会有事件触发。
          </p>
          <ul className="flow-list">
            {ruleItems.map((r) => (
              <li key={r.name} className="flow-card">
                <div className="flow-card-main">
                  <div className="flow-card-title">
                    {r.name}
                    {r.builtin && <em className="flow-tag">内置</em>}
                    <em className="flow-ver">优先级 {r.priority}</em>
                  </div>
                  <p className="flow-desc">
                    <code>{r.signal}</code> → <strong>{ACTION_LABEL[r.action] ?? r.action}</strong>
                    {r.reason ? ` · ${r.reason}` : ""}
                    {r.cooldown_seconds ? ` · 冷却 ${r.cooldown_seconds}s` : " · 无防抖"}
                  </p>
                </div>
                {!r.builtin && (
                  <div className="flow-card-side">
                    <button
                      type="button"
                      className="btn-ghost danger"
                      disabled={busy}
                      onClick={async () => {
                        if (demoGuard("删除规则")) return;
                        setBusy(true);
                        try {
                          await api.hybrid.deleteRule(r.name);
                          showToast("success", "已删除用户覆盖，系统默认重新生效");
                          load();
                        } catch {
                          showToast("error", "删除失败");
                        } finally {
                          setBusy(false);
                        }
                      }}
                    >
                      删覆盖
                    </button>
                  </div>
                )}
              </li>
            ))}
          </ul>
          <p className="flow-hint">
            已知 signal：{KNOWN_SIGNALS.map((s) => (
              <code key={s} className="flow-signal">{s}</code>
            ))}
          </p>
        </>
      )}

      {tab === "run" && (
        <ul className="flow-list">
          {runItems.map((r) => (
            <li key={r.run_id} className="flow-card">
              <div className="flow-card-main">
                <div className="flow-card-title">
                  {r.run_id}
                  <em className={`flow-status is-${r.status}`}>{STATUS_LABEL[r.status] ?? r.status}</em>
                </div>
                <p className="flow-desc">
                  工作流 {r.workflow_id || "（裸跑智能体）"} · 任务 {r.task_id}
                </p>
              </div>
              <div className="flow-card-side">
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={busy}
                  onClick={async () => {
                    if (demoGuard("查看日志")) return;
                    setBusy(true);
                    try {
                      const logs = await api.hybrid.logs(r.run_id);
                      showToast("info", logs && logs.length ? `共 ${logs.length} 条日志` : "没有日志");
                    } catch {
                      showToast("error", "取日志失败");
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  看日志
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {editing && (
        <div className="flow-modal" role="dialog" aria-modal="true" aria-label="编辑工作流定义">
          <div className="flow-modal-box">
            <h3>编辑「{editing.name}」</h3>
            <p className="flow-hint">
              定义是 JSON。<strong>失焦或点保存时才校验</strong> ——
              输入过程中允许非法 JSON，否则你连括号都删不掉。
            </p>
            <textarea
              className="flow-editor"
              defaultValue={JSON.stringify(editing.definition, null, 2)}
              onBlur={(e) => {
                try {
                  JSON.parse(e.target.value);
                  setErr("");
                } catch (ex) {
                  setErr(String(ex));
                }
              }}
            />
            {err && <p className="flow-err">JSON 不合法：{err}</p>}
            <div className="flow-modal-actions">
              <button type="button" className="btn-ghost" onClick={() => setEditing(null)}>
                取消
              </button>
              <button type="button" className="btn-primary" disabled={busy || !!err} onClick={onSaveWf}>
                保存
              </button>
            </div>
          </div>
        </div>
      )}

      <ToastHost toasts={toasts} />
    </ConsoleShell>
  );
}
