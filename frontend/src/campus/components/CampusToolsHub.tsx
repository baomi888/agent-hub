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

import { useCallback, useEffect, useRef, useState } from "react";
import type { ComponentType } from "react";
import Link from "next/link";
import GpaTool from "../tools/gpa/GpaTool";
import PomodoroTool from "../tools/pomodoro/PomodoroTool";
import ScheduleTool from "../tools/schedule/ScheduleTool";
import WordsTool from "../tools/words/WordsTool";
import DeadlineTool from "../tools/deadline/DeadlineTool";
import DrawTool from "../tools/draw/DrawTool";
import ToolNav, { TOOL_LIST } from "./ToolNav";
import { subscribeExternalWrite } from "../storage";
import { CAMPUS_KEYS } from "../storage/keys";
import { pullCampusState } from "../sync";
import type { ToolId } from "../types";

const PANELS: Record<ToolId, ComponentType> = {
  gpa: GpaTool,
  pomodoro: PomodoroTool,
  schedule: ScheduleTool,
  words: WordsTool,
  deadline: DeadlineTool,
  draw: DrawTool,
};

/**
 * `?tool=pomodoro` 里指定的工具。
 *
 * 存在的理由只有一个：提醒的「开始专注」要能**一步到位**。通知点开落到工具集
 * 首页，用户还得自己找到番茄钟再点一次 —— 那条通知就等于没起作用。
 *
 * 只在客户端读（`window` 不存在时返回 null）：SSR 期间没有 URL 查询串，
 * 若在这里就返回 pomodoro，服务端渲染 gpa、客户端渲染番茄钟，会触发
 * hydration mismatch。所以调用方放在 mount effect 里，不在渲染期调。
 */
function toolFromQuery(): ToolId | null {
  if (typeof window === "undefined") return null;
  const want = new URLSearchParams(window.location.search).get("tool");
  if (!want) return null;
  return Object.prototype.hasOwnProperty.call(PANELS, want) ? (want as ToolId) : null;
}

/**
 * 哪些 localStorage 键归哪个工具管。
 *
 * 与 `sync.ts` 的 SYNC_KEYS 逐项对应（那份名单是「能上云的」，这份是「刷新时该叫醒谁」）。
 * 没列在这里的工具（GPA / 番茄钟 / 抽签）不走云同步 —— 番茄钟有自己的 focusSync，
 * 也绝不能因为一条外部通知被重挂：计时中的番茄钟重挂就等于清零。
 */
const TOOL_KEYS: Partial<Record<ToolId, readonly string[]>> = {
  schedule: [CAMPUS_KEYS.schedule, CAMPUS_KEYS.week, CAMPUS_KEYS.term],
  words: [CAMPUS_KEYS.words],
  deadline: [CAMPUS_KEYS.deadline],
};

/**
 * 此刻能不能**静默**重挂。
 *
 * 工具只在 mount effect 里读一次 localStorage（ScheduleTool / DeadlineTool / WordsTool 都是），
 * 所以「重新读」这件事只能靠重挂实现；而重挂会丢掉对话框里没提交的草稿、输入框里没保存的文字。
 * 因此三个条件任一成立就不许静默刷新：
 *
 *  1. 面板里开着弹窗（三个工具的弹窗都是 `[role="dialog"]` + `.campus-modal`）；
 *  2. 焦点在可输入元素上（光标在，说明用户正在写）；
 *  3. 本次挂载后已经产生过 input/change 事件（手机上没有稳定焦点，靠这条兜住）。
 */
function canRefreshQuietly(root: HTMLElement | null, typed: boolean): boolean {
  if (typed) return false;
  if (!root) return true;
  if (root.querySelector('[role="dialog"], .campus-modal')) return false;
  const el = document.activeElement as HTMLElement | null;
  if (!el || !root.contains(el)) return true;
  const tag = el.tagName;
  return !(
    tag === "INPUT" ||
    tag === "TEXTAREA" ||
    tag === "SELECT" ||
    el.isContentEditable
  );
}

export default function CampusToolsHub() {
  const [active, setActive] = useState<ToolId>("gpa");
  /** 自增即重挂当前工具：它自己的 mount effect 会重新读一次 localStorage */
  const [reload, setReload] = useState(0);
  /** 有外部更新但不适合静默刷新 —— 不动界面，挂一条提示等用户点 */
  const [stale, setStale] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);
  /** 本次挂载后用户在这个面板里输入过没有 */
  const typedRef = useRef(false);

  // 挂载时拉一次云端快照（规格 2.3）。未登录 / 接口没上线 / 断网都会被
  // `pullCampusState` 静默跳过 —— 这一页的核心承诺是"不登录也能用"，
  // 所以同步失败不能冒泡成任何可见错误。
  useEffect(() => {
    void pullCampusState();
  }, []);

  // 从「开始专注」那条通知点进来时带着 `?tool=pomodoro`，落地就在番茄钟上。
  // setState 推在 setTimeout(0) 里：本项目开了 react-hooks/set-state-in-effect，
  // 在 effect 体里同步 set 会判红（MailAccess 的倒计时是同一套处理）。
  useEffect(() => {
    const want = toolFromQuery();
    if (!want) return;
    const timer = window.setTimeout(() => setActive(want), 0);
    return () => window.clearTimeout(timer);
  }, []);

  const Panel = PANELS[active] ?? GpaTool;

  /*
   * 两个「会让工具重新读一次存档」的动作都从这里走。
   *
   * 不复位的事交给 effect 做会被 react-hooks/set-state-in-effect 判红（同步 setState
   * 在 effect 里等于级联渲染）；而这两个状态本来就和「重挂」是同一件事，
   * 放在触发点复位既更直接，也不会出现「先渲染一帧旧状态再纠正」。
   */
  const bumpReload = useCallback(() => {
    typedRef.current = false;
    setStale(false);
    setReload((n) => n + 1);
  }, []);

  const changeTool = useCallback((id: ToolId) => {
    typedRef.current = false;
    setStale(false);
    setActive(id);
  }, []);

  // 「用户正在用这个面板」的判定：面板内任何 input/change 都算（捕获阶段，React 拦不住）
  useEffect(() => {
    const el = panelRef.current;
    if (!el) return;
    const mark = () => {
      typedRef.current = true;
    };
    el.addEventListener("input", mark, true);
    el.addEventListener("change", mark, true);
    return () => {
      el.removeEventListener("input", mark, true);
      el.removeEventListener("change", mark, true);
    };
  }, [active, reload]);

  // 云端那份落地（或换账号被清空）时决定「怎么让眼前这份跟着变」。
  // 只处理当前挂着的工具：别的工具切过去时本来就重挂，会自己读到新值。
  useEffect(() => {
    const watched = new Set(TOOL_KEYS[active] ?? []);
    if (watched.size === 0) return;
    return subscribeExternalWrite((key) => {
      if (!watched.has(key)) return;
      if (canRefreshQuietly(panelRef.current, typedRef.current)) {
        bumpReload();
      } else {
        setStale(true);
      }
    });
  }, [active, bumpReload]);

  const label = TOOL_LIST.find((t) => t.id === active)?.label ?? "数据";

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
         * 这一行（返回 + 品牌字）现在只在宽屏存在，手机上由 CSS 整行隐掉
         * （campus.css 的 `@media (max-width: 900px)`）。
         *
         * 为什么手机能收了（2026-11-13）：这一页挂上了和控制台同一套底部 tab，
         * 换功能不用先回首页 —— 返回入口原本存在的理由（"唯一的出路口"）就没了。
         * 它原本的由来（2026-11-07）：这一页不套控制台外壳，桌面快捷方式打开的
         * 是 PWA 独立窗口（manifest display: standalone），窗口里没有后退按钮，
         * 想回去只剩标题栏那个 × —— 一点整个应用就没了。**宽屏至今如此**：
         * (campus) 路由组没有侧栏，900px 以上这一行仍是唯一的出路，所以 DOM 不能删。
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
            登录后课表、DDL、背单词和番茄钟的记录可同步到云端，换设备也在。
          </p>
        </header>

        <ToolNav active={active} onChange={changeTool} />

        {/*
         * 外面这层 div 是 `display: contents`（campus.css 的 .campus-panel-slot），
         * 不产生盒子，所以版式与「Panel 直接挂在 shell 下」完全一致；
         * 它存在的唯一目的是给宿主一个可以 `contains()` 的边界（判焦点、判弹窗）。
         * id 与 ToolNav 里按钮的 aria-controls 对应 —— 这一对原来悬着，没有落地元素。
         */}
        <div
          id={`campus-panel-${active}`}
          ref={panelRef}
          className="campus-panel-slot"
        >
          <Panel key={reload} />
        </div>

        {stale ? (
          <div className="campus-remote" role="status">
            <span className="campus-remote-text">
              {label}在别处（另一台设备或另一个账号）已经变了。你正在这里编辑，先没动它。
            </span>
            <button
              type="button"
              className="campus-btn is-ghost campus-remote-btn"
              onClick={bumpReload}
            >
              载入新的
            </button>
            <button
              type="button"
              className="campus-remote-dismiss"
              onClick={() => setStale(false)}
            >
              先不看
            </button>
          </div>
        ) : null}
      </main>
    </div>
  );
}
