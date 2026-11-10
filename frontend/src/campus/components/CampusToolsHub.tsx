"use client";

/**
 * 校园工具集 · 板块容器
 *
 * 职责只有三件：页头、工具切换、渲染当前工具。
 * 任何具体业务逻辑都不写在这里 —— 这一层要薄，薄到换工具时不需要动它。
 *
 * 六个工具已全部迁移到新实现，旧的静态页 /tools.html 仍然保留但不再被这里引用。
 * 以后新增工具：在 ToolNav 的 TOOL_LIST 里加一条、在下面的 PANELS 里加一行即可。
 */

import { useState } from "react";
import type { ComponentType } from "react";
import GpaTool from "../tools/gpa/GpaTool";
import PomodoroTool from "../tools/pomodoro/PomodoroTool";
import ScheduleTool from "../tools/schedule/ScheduleTool";
import WordsTool from "../tools/words/WordsTool";
import DeadlineTool from "../tools/deadline/DeadlineTool";
import DrawTool from "../tools/draw/DrawTool";
import ToolNav from "./ToolNav";
import type { ToolId } from "../types";

const PANELS: Record<ToolId, ComponentType> = {
  gpa: GpaTool,
  pomodoro: PomodoroTool,
  schedule: ScheduleTool,
  words: WordsTool,
  deadline: DeadlineTool,
  draw: DrawTool,
};

export default function CampusToolsHub() {
  const [active, setActive] = useState<ToolId>("gpa");
  const Panel = PANELS[active] ?? GpaTool;

  return (
    /*
     * 外层这一圈是滚动容器，不是装饰。
     * 全局 base.css 把 html/body 设成 height:100% + overflow:hidden（工作台是固定视口的外壳），
     * 所以这一页如果不自己滚，内容一变高下半截就永远够不到 —— 番茄钟加了统计与同步之后
     * 就撞上了这个坑。滚动条放在最外层，才不会卡在 880px 的栏目边上。
     */
    <div className="campus-scroll">
      <main className="campus-shell">
        <header className="campus-head">
          <h1 className="campus-title">校园工具集</h1>
          <p className="campus-sub">
            不用登录、不连教务，打开就能用。不登录时数据只存在这台设备的浏览器里；
            登录后番茄钟的记录可同步到云端，换设备也在。
          </p>
        </header>

        <ToolNav active={active} onChange={setActive} />

        <Panel />
      </main>
    </div>
  );
}
