"use client";

/**
 * 待办日历视图（2026-11-19，按 outputs/待办日历视图设计稿_v1.html 实现）。
 *
 * 形态：月历概览 + 点选某天看当天清单。与列表视图并列、数据同源
 * （`api.tasks.list` 一次拉全的那份 `tasks.all`，不发新请求）。
 *
 * 设计稿钉死的几条，谁改了测试会红（tests/test_reminders_ui.py::TestCalendarView）：
 *   · 日期标记是**短横条**（16×3 玉米金），不是圆点 —— 用户原话「原点不好看」；
 *   · 当天只要有一条重要/紧急（priority ≥ 2），整条横线转红；
 *   · 今天 ≠ 选中：今天金色描边胶囊，选中日才是实底金；两者叠加时描边让位；
 *   · 周一开头，与课程表、倒计时的口径一致；
 *   · 一期不做拖拽改期 —— 我们的任务多数只有截止日，没有时段块可拖，
 *     做了反而是误导。
 *
 * 纯度：`now` 由页面每分钟推下来（useNowMinute），渲染期不读钟；
 * 选中的日期在 `now` 到位后由 effect 补上（首帧 null，不猜今天）。
 */

import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import type { Task } from "@/lib/types";
import { dateFromDueAt } from "@/campus/logic/deadlineCloud";
import { startOfDay } from "@/campus/logic/deadline";

/** 月历表头：周一开头（与课程表/倒计时同口径） */
const WEEKDAY_HEADS = ["一", "二", "三", "四", "五", "六", "日"] as const;

/** 当日清单标题里的星期（getDay 口径：0=周日） */
const AGENDA_DOW = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"] as const;

/** 「含高优先」的门槛：重要(2)与紧急(3)都算硬仗，横线转红 */
const HI_PRIORITY = 2;

/** 月历网格：当月每天的数字，开头按周一补 null 留白 */
function monthCells(year: number, month: number): (number | null)[] {
  const lead = (new Date(year, month, 1).getDay() + 6) % 7;
  const total = new Date(year, month + 1, 0).getDate();
  return [
    ...Array.from({ length: lead }, () => null),
    ...Array.from({ length: total }, (_, i) => i + 1),
  ];
}

export default function CalendarView({
  tasks,
  now,
  renderCard,
}: {
  /** 已一次拉全的 open 待办（`data.tasks.all`） */
  tasks: Task[];
  /** 页面每分钟推下来的毫秒时刻；首帧可能是 null */
  now: number | null;
  /** 渲染单条待办：直接复用页面的 taskCard（勾选/编辑/删除同一套） */
  renderCard: (task: Task) => ReactNode;
}) {
  /** 选中的那天（本地零点毫秒）。null = 用户还没点过，跟随下面的默认值 */
  const [anchor, setAnchor] = useState<number | null>(null);
  /** 正在看的月份。null = 跟随选中日；点了 ‹ › 之后独立 */
  const [viewed, setViewed] = useState<{ y: number; m: number } | null>(null);
  /** 「未排期」折叠条展开过没有 */
  const [undatedOpen, setUndatedOpen] = useState(false);

  const today = now !== null ? startOfDay(now) : null;
  /**
   * 选中的那天：没点过就默认今天。**派生而不是 effect 落位** ——
   * 渲染期读 prop 不破坏纯度（`now` 是页面推下来的），也省掉一帧级联渲染；
   * 用户点过任何一天后 anchor 非 null，默认值自动让位。
   */
  const selected = anchor ?? today;

  /** 按天分组：dateFromDueAt 归一到本地零点，23:59 的任务不会错算前一天 */
  const byDay = useMemo(() => {
    const map = new Map<number, Task[]>();
    for (const t of tasks) {
      if (t.status !== "open" || t.due_at === null) continue;
      const key = dateFromDueAt(t.due_at as number);
      const list = map.get(key);
      if (list) list.push(t);
      else map.set(key, [t]);
    }
    return map;
  }, [tasks]);

  const undated = useMemo(
    () => tasks.filter((t) => t.status === "open" && t.due_at === null),
    [tasks],
  );

  // 月历网格数据：viewed 独立（翻页后），否则跟随选中日
  const ym = useMemo(
    () =>
      viewed ??
      (selected !== null
        ? { y: new Date(selected).getFullYear(), m: new Date(selected).getMonth() }
        : null),
    [viewed, selected],
  );
  const cells = useMemo(() => (ym ? monthCells(ym.y, ym.m) : []), [ym]);

  /** 当天的事，按提醒/截止时刻排（无时刻的垫底），重要的排前面 */
  const dayTasks = useMemo(() => {
    const list = selected !== null ? (byDay.get(selected) ?? []) : [];
    return [...list].sort((a, b) => {
      const ta = (a.remind_at ?? a.due_at ?? Number.MAX_SAFE_INTEGER) as number;
      const tb = (b.remind_at ?? b.due_at ?? Number.MAX_SAFE_INTEGER) as number;
      if (ta !== tb) return ta - tb;
      return (b.priority ?? 0) - (a.priority ?? 0);
    });
  }, [selected, byDay]);

  const stepMonth = (delta: number) => {
    const base = ym ?? { y: new Date().getFullYear(), m: new Date().getMonth() };
    const next = new Date(base.y, base.m + delta, 1);
    setViewed({ y: next.getFullYear(), m: next.getMonth() });
  };

  return (
    <div className="rem-cal-wrap">
      {/* ===== 月历 ===== */}
      <div className="rem-cal-card">
        <div className="rem-cal-nav">
          <button
            type="button"
            className="rem-cal-step"
            aria-label="上一个月"
            onClick={() => stepMonth(-1)}
          >
            ‹
          </button>
          <b>{ym ? `${ym.y} 年 ${ym.m + 1} 月` : "…"}</b>
          <div className="rem-cal-nav-r">
            <button
              type="button"
              className="rem-cal-todaybtn"
              disabled={today === null}
              onClick={() => {
                if (today === null) return;
                setAnchor(today);
                setViewed(null);
              }}
            >
              今天
            </button>
            <button
              type="button"
              className="rem-cal-step"
              aria-label="下一个月"
              onClick={() => stepMonth(1)}
            >
              ›
            </button>
          </div>
        </div>

        <div className="rem-cal-week" aria-hidden="true">
          {WEEKDAY_HEADS.map((w) => (
            <span key={w}>{w}</span>
          ))}
        </div>

        <div className="rem-cal-grid" role="grid" aria-label="月历">
          {cells.map((d, i) => {
            if (d === null) return <span key={`e${i}`} className="rem-cal-cell mut" aria-hidden="true" />;
            const key = ym ? new Date(ym.y, ym.m, d).getTime() : 0;
            const dayList = byDay.get(key);
            const has = Boolean(dayList?.length);
            const hi = has && dayList!.some((t) => (t.priority ?? 0) >= HI_PRIORITY);
            const isToday = today !== null && key === today;
            const isSel = selected !== null && key === selected;
            return (
              <button
                key={key}
                type="button"
                className={`rem-cal-cell${isToday ? " today" : ""}${isSel ? " sel" : ""}`}
                aria-pressed={isSel}
                aria-label={`${ym!.m + 1} 月 ${d} 日${has ? `，${dayList!.length} 项待办` : ""}`}
                onClick={() => {
                  setAnchor(key);
                  setViewed(null);
                }}
              >
                <span className="rem-cal-num">{d}</span>
                {has ? (
                  <>
                    {/* 桌面端格子里压一条标题（设计稿 §03）；手机上窄，由 CSS 隐藏 */}
                    <span className="rem-cal-title" aria-hidden="true">
                      {dayList![0].title}
                    </span>
                    <i className={`rem-cal-bar${hi ? " hi" : ""}`} aria-hidden="true" />
                  </>
                ) : null}
              </button>
            );
          })}
        </div>
      </div>

      {/* ===== 当日清单 ===== */}
      <div className="rem-cal-agenda">
        <div className="rem-cal-ag-hd">
          <b>
            {selected !== null
              ? `${new Date(selected).getMonth() + 1} 月 ${new Date(selected).getDate()} 日 · ${
                  AGENDA_DOW[new Date(selected).getDay()]
                }`
              : "…"}
          </b>
          <span>{dayTasks.length ? `${dayTasks.length} 项` : "无任务"}</span>
        </div>

        {dayTasks.length === 0 ? (
          <div className="rem-cal-empty">
            <p>这天还没有安排</p>
            <p className="rem-cal-empty-sub">在列表视图写一条，或点页头「新建待办」</p>
          </div>
        ) : (
          <div className="rem-cal-list">{dayTasks.map(renderCard)}</div>
        )}

        {/* 未排期兜底：due_at 必填之后新任务不会再没有日期，
            但迁移前的存量行可能还有 null —— 月历上没有格子，不能静默丢 */}
        {undated.length > 0 ? (
          <div className="rem-cal-undated">
            <button
              type="button"
              aria-expanded={undatedOpen}
              onClick={() => setUndatedOpen((v) => !v)}
            >
              未排期 {undated.length} 项{undatedOpen ? "（收起）" : ""}
            </button>
            {undatedOpen ? <div className="rem-cal-list">{undated.map(renderCard)}</div> : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
