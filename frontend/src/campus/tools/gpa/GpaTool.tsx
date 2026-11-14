"use client";

/**
 * GPA 分析工具（板块内第一个完成迁移的工具，作为其余工具的参考实现）。
 *
 * 迁移原则：**行为与旧版一致，结构比旧版干净**。
 *  - 计算口径逐条对齐 logic/gpa.ts 的注释（含空成绩不计学分这条）；
 *  - 存储走 campus/storage 统一封装，自动兼容旧键 baomi_tools_gpa_v1；
 *  - 首次打开铺示例数据，和旧版「没有存档就载入示例」的行为一致。
 *
 * 这是板块的样板件：后续工具照这个结构写（state → 恢复 → 写回 → 渲染），
 * 不要在组件里直接碰 localStorage。
 */

import { useEffect, useMemo, useState } from "react";
import { computeGpa, gpaScale, parseLegacyGpa, SAMPLE_ROWS } from "../../logic/gpa";
import type { GpaScaleRow } from "../../logic/gpa";
import { CAMPUS_KEYS, LEGACY_KEYS } from "../../storage/keys";
import { readWithMigration, writeJson } from "../../storage";
import type { GpaAlgo, GpaRow } from "../../types";

/**
 * 换算方式。
 *
 * `scale` 与 `note` 二选一：
 *  - 有换算表的（标准 / 北大）走 `<details>` 收起 —— 十行分数档常显在页面上，
 *    既没人逐行读，又会在窄屏把卡片撑高（实测 390 宽切换算法整块高度差 48px）；
 *  - 「加权均分」没有表，只有一句口径，且这句是**必须常显**的：
 *    切过去以后总分含义变了，不说等于让人看错数。
 *
 * 表本身从 logic/gpa.ts 的 TABLES 派生，不在这里手写第二份。
 */
type AlgoOption = {
  value: GpaAlgo;
  label: string;
  scale?: GpaScaleRow[];
  note?: string;
};

const ALGO_OPTIONS: readonly AlgoOption[] = [
  { value: "std4", label: "标准 4.0", scale: gpaScale("std4") },
  { value: "pku", label: "北大 4.0", scale: gpaScale("pku") },
  { value: "wavg", label: "加权均分", note: "直接用原始成绩按学分加权，不折算绩点。" },
];

interface GpaSnapshot {
  algo: GpaAlgo;
  rows: GpaRow[];
}

export default function GpaTool() {
  const [rows, setRows] = useState<GpaRow[]>([]);
  const [algo, setAlgo] = useState<GpaAlgo>("std4");
  /** 恢复完成前不写回，避免用空数组把用户的存档覆盖掉 */
  const [ready, setReady] = useState(false);

  // 首屏恢复：新键 → 旧键 → 迁移；都没有就铺示例
  //
  // 为什么必须放在 effect 里而不是 useState 惰性初始化：
  // localStorage 在 SSR 阶段不存在，服务端只能渲染空态。若用惰性初始化，
  // 服务端与客户端首帧取值不同，必然触发 hydration 不一致警告（整页重渲染）。
  // 本条规则针对的是「用 effect 派生 state」，这里是「从外部存储同步一次快照」，
  // 属于 React 文档承认的合法例外，故局部关闭该规则。
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    const restored = readWithMigration<GpaSnapshot>(
      CAMPUS_KEYS.gpa,
      [LEGACY_KEYS.gpa],
      parseLegacyGpa,
    );

    if (restored && restored.rows.length > 0) {
      setRows(restored.rows);
      setAlgo(restored.algo);
    } else {
      setRows(SAMPLE_ROWS);
    }
    setReady(true);
  }, []);
  /* eslint-enable react-hooks/set-state-in-effect */

  // 变更即存。ready 为 false 时跳过，防止首帧空数组覆盖存档
  useEffect(() => {
    if (!ready) return;
    writeJson(CAMPUS_KEYS.gpa, { algo, rows });
  }, [algo, rows, ready]);

  const summary = useMemo(() => computeGpa(rows, algo), [rows, algo]);
  const activeAlgo = ALGO_OPTIONS.find((item) => item.value === algo) ?? ALGO_OPTIONS[0];

  function updateRow(index: number, patch: Partial<GpaRow>) {
    setRows((prev) => prev.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  }

  function addRow() {
    setRows((prev) => [...prev, { name: "", credit: 0, score: null }]);
  }

  function removeRow(index: number) {
    setRows((prev) => prev.filter((_, i) => i !== index));
  }

  /** 数字输入：空串一律落到 null / 0，避免把 NaN 写进存储 */
  function toNumber(text: string): number | null {
    if (text.trim() === "") return null;
    const value = Number.parseFloat(text);
    return Number.isFinite(value) ? value : null;
  }

  return (
    <div id="campus-panel-gpa" role="tabpanel" aria-label="GPA 分析">
      {/* 读数 */}
      <div className="campus-stats" style={{ marginBottom: 12 }}>
        <div className="campus-stat">
          <span className="campus-stat-label">
            {algo === "wavg" ? "加权均分" : "总 GPA"}
          </span>
          <span className="campus-stat-value is-accent">
            {summary.gpa === null ? "—" : summary.gpa.toFixed(2)}
          </span>
        </div>
        <div className="campus-stat">
          <span className="campus-stat-label">加权均分</span>
          <span className="campus-stat-value">
            {summary.weightedAvg === null ? "—" : summary.weightedAvg.toFixed(1)}
          </span>
        </div>
        <div className="campus-stat">
          <span className="campus-stat-label">总学分</span>
          <span className="campus-stat-value">{summary.totalCredits.toFixed(1)}</span>
        </div>
      </div>

      {/* 换算标准 */}
      <div className="campus-card">
        <div className="campus-card-head">
          <h2 className="campus-card-title">绩点换算标准</h2>
        </div>

        <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center" }}>
          <label className="campus-field" style={{ minWidth: 160 }}>
            <span>换算方式</span>
            <select
              className="campus-select"
              value={algo}
              onChange={(event) => setAlgo(event.target.value as GpaAlgo)}
            >
              {ALGO_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
        </div>

        {activeAlgo.scale ? (
          // key={algo}：换算法时重挂一次，展开过的表自动收回 —— 否则切到北大
          // 会直接露出「标准 4.0」那次展开的状态，看起来像是没换成功
          <details className="campus-details" key={algo}>
            <summary>{activeAlgo.label} · 换算表</summary>
            <table className="campus-scale">
              <caption className="sr-only">
                {activeAlgo.label}：成绩下限与对应绩点，低于 60 记 0
              </caption>
              <thead>
                <tr>
                  <th scope="col">成绩</th>
                  <th scope="col">绩点</th>
                </tr>
              </thead>
              <tbody>
                {activeAlgo.scale.map((row) => (
                  <tr key={row.from === null ? "below" : row.from}>
                    <td>{row.from === null ? "不足 60" : `≥ ${row.from}`}</td>
                    <td className="is-num">{row.point.toFixed(1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        ) : (
          <p className="campus-card-note" style={{ marginTop: 10, marginBottom: 0 }}>
            {activeAlgo.note}
          </p>
        )}
      </div>

      {/* 成绩单 */}
      <div className="campus-card">
        <div className="campus-card-head">
          <h2 className="campus-card-title">我的成绩单</h2>
          <div style={{ display: "flex", gap: 8 }}>
            <button type="button" className="campus-btn" onClick={() => setRows(SAMPLE_ROWS)}>
              载入示例
            </button>
            <button type="button" className="campus-btn is-primary" onClick={addRow}>
              添加课程
            </button>
          </div>
        </div>

        {/* 空态不另起一个虚线框：框里那句话在念上方两个按钮的名字（「点添加课程、
            或载入示例」），而按钮就在上方 20px 处。表格空着 + 表头在场，本身就说明
            「这里会列课程」，缺的是数据不是说明。 */}
        <table className="campus-table">
          <thead>
            <tr>
              <th scope="col">课程</th>
              <th scope="col" style={{ width: 88 }}>
                学分
              </th>
              <th scope="col" style={{ width: 88 }}>
                成绩
              </th>
              <th scope="col" style={{ width: 72, textAlign: "center" }}>
                绩点
              </th>
              <th scope="col" style={{ width: 56 }}>
                <span className="sr-only">操作</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={5} className="campus-row-empty">
                  还没有课程
                </td>
              </tr>
            )}
            {rows.map((row, index) => {
              const detail = summary.rows[index];
              return (
                <tr key={index}>
                  <td>
                    <input
                      className="campus-input"
                      type="text"
                      value={row.name}
                      placeholder="如：高等数学"
                      aria-label={`第 ${index + 1} 门课程名称`}
                      onChange={(event) => updateRow(index, { name: event.target.value })}
                    />
                  </td>
                  <td>
                    <input
                      className="campus-input"
                      type="number"
                      min={0}
                      step={0.5}
                      value={row.credit === 0 ? "" : String(row.credit)}
                      placeholder="3"
                      aria-label={`第 ${index + 1} 门课程学分`}
                      onChange={(event) =>
                        updateRow(index, { credit: toNumber(event.target.value) ?? 0 })
                      }
                    />
                  </td>
                  <td>
                    <input
                      className="campus-input"
                      type="number"
                      min={0}
                      max={100}
                      step={1}
                      value={row.score === null ? "" : String(row.score)}
                      placeholder="85"
                      aria-label={`第 ${index + 1} 门课程成绩`}
                      onChange={(event) => updateRow(index, { score: toNumber(event.target.value) })}
                    />
                  </td>
                  <td className="is-num">
                    {detail?.point == null ? "—" : detail.point.toFixed(1)}
                  </td>
                  <td>
                    <button
                      type="button"
                      className="campus-btn is-danger"
                      aria-label={`删除第 ${index + 1} 门课程`}
                      title="删除该行"
                      onClick={() => removeRow(index)}
                    >
                      删
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

        <p className="campus-card-note" style={{ marginTop: 12 }}>
          数据先存在这台设备的浏览器里，登录后会同步到云端，换设备也在。清理浏览器数据会一并清掉本地那份。
        </p>
      </div>
    </div>
  );
}
