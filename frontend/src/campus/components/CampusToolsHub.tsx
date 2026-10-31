"use client";

/**
 * 校园工具集 · 板块容器
 *
 * 职责只有三件：页头、工具切换、渲染当前工具。
 * 任何具体业务逻辑都不写在这里 —— 这一层要薄，薄到换工具时不需要动它。
 */

import { useState } from "react";
import ToolNav, { TOOL_LIST } from "./ToolNav";
import GpaTool from "../tools/gpa/GpaTool";
import type { ToolId, ToolMeta } from "../types";

/** 尚未迁移的工具：如实说明，并给出旧版入口，不假装能用 */
function PendingTool({ tool }: { tool: ToolMeta }) {
  return (
    <div id={`campus-panel-${tool.id}`} role="tabpanel" aria-label={tool.label}>
      <div className="campus-pending">
        <h2 className="campus-pending-title">{tool.label} · 迁移中</h2>
        <p className="campus-pending-text">
          {tool.hint}。这个工具还在从旧版单页迁移，新实现尚未接上，
          <strong>当前请先用旧版页面</strong>，功能完全正常。
        </p>
        <p className="campus-pending-text">
          <a href="/tools.html">打开旧版校园工具集 →</a>
        </p>
      </div>
    </div>
  );
}

export default function CampusToolsHub() {
  const [active, setActive] = useState<ToolId>("gpa");
  const currentTool = TOOL_LIST.find((tool) => tool.id === active) ?? TOOL_LIST[0];

  return (
    <main className="campus-shell">
      <header className="campus-head">
        <h1 className="campus-title">校园工具集</h1>
        <p className="campus-sub">
          不用登录、不连教务，打开就能用。所有数据只存在这台设备的浏览器里。
        </p>
      </header>

      <ToolNav active={active} onChange={setActive} />

      {active === "gpa" ? (
        <GpaTool />
      ) : (
        <PendingTool tool={currentTool} />
      )}
    </main>
  );
}
