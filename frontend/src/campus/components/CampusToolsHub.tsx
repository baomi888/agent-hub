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
import Link from "next/link";
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
        {/*
         * 返回入口为什么必须有（2026-11-07）：
         * 这一页不套控制台外壳，从左导航点进来等于换成一个"没有侧栏的页面"。
         * 浏览器里还能按后退键，但从桌面快捷方式打开时是 PWA 独立窗口
         * （manifest display: standalone），窗口里**根本没有后退按钮**，
         * 用户想回去只剩标题栏那个 × —— 一点整个应用就没了，再开还得重新找。
         *
         * 目标写死 "/" 且用 replace，不调 router.back()：
         *   · "/" 已登录是工作台、未登录是介绍页，两种情况都回得到"应用里"；
         *   · back() 在独立窗口里历史为空时会直接关窗口，正是要避免的行为；
         *   · replace 不新增历史，从设置页进来的人按后退仍是回设置页，
         *     不会在 "/" 和这一页之间来回弹。
         * 文案只写「回首页」，不按登录态分「回工作台 / 回首页」：
         * "/" 对两种身份都是首页，这么说永远不错；也省掉一次 /auth/me 请求
         * —— 这一页本来是免登录的，不该为一句文案把它绑上登录态。
         */}
        <div className="campus-bar">
          <Link href="/" replace className="campus-back" aria-label="回首页（工具集是独立页面）">
            <span aria-hidden="true">←</span> 回首页
          </Link>
          <span className="campus-brand">知穗 AGENT</span>
        </div>

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
