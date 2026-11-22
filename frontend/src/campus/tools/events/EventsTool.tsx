"use client";

/**
 * 大学年历表 · 主控
 *
 * 这个工具的定位是「破除信息差」，不是「帮你报名」。所以它只做三件事：
 *  1. 把全年该关注的事摆在日历 / 时间轴上，让人知道「原来还有这个」；
 *  2. 每条都说清性质、适合谁、要准备什么；
 *  3. 把「我现在该准备什么」压成一条 60 天清单。
 *
 * 时间一律标「往年规律」，页面底部与每个详情都写明「以本校通知为准」——
 * 这不是免责声明套话：院赛校赛时间各校不同，把这条藏起来才是骗人。
 *
 * 存储只有用户自己的状态（标记 / 置顶 / 年级 / 视图），赛事数据是静态文件。
 * 数据每年更新，存进浏览器反而会让老用户永远看不到新赛季。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import type { EventCategoryId, EventItem, EventMark, EventsState } from "../../types";
import { readJson, writeJson } from "../../storage";
import { CAMPUS_KEYS } from "../../storage/keys";
import {
  ALL_ITEMS,
  CATEGORIES,
  buildPins,
  emptyEventsState,
  filterItems,
  monthKey,
  monthOccurrences,
  normalizeEventsState,
  parseMonthKey,
  schoolYearLabel,
  schoolYearOf,
  phaseText,
  daysText,
  CATEGORY_MAP,
} from "../../logic/events";
import CalendarView from "./CalendarView";
import TimelineView from "./TimelineView";
import MineView from "./MineView";
import EventDetail from "./EventDetail";
import IntroSheet from "./IntroSheet";
import { loadNotices, type NoticeBundle } from "./notices";

/** 每分钟对一次表：跨过零点时「今天」的标记要跟着走 */
const CLOCK_MS = 60_000;

/** 快捷收走后的回执停留时长。要够读完整句（「已把「XX」从日历上收走 · 想找回就去「我的」」） */
const TOAST_MS = 3_600;

const VIEW_LABELS: { id: EventsState["view"]; label: string }[] = [
  { id: "calendar", label: "日历" },
  { id: "timeline", label: "时间轴" },
  { id: "mine", label: "我的" },
];

const GRADE_LABELS = ["大一", "大二", "大三", "大四"];

export default function EventsTool() {
  const [state, setState] = useState<EventsState>(() => emptyEventsState(new Date()));
  const [ready, setReady] = useState(false);
  const [today, setToday] = useState<Date | null>(null);

  /** 打开详情的条目 id */
  const [detailId, setDetailId] = useState<string | null>(null);
  /** 点某一天之后的展开层 */
  const [daySheet, setDaySheet] = useState<{ year: number; month: number; day: number } | null>(null);
  /** 分类筛选；空数组 = 全部 */
  const [categories, setCategories] = useState<EventCategoryId[]>([]);

  /** 使用说明：首次进入由 state.introSeen 决定，之后手动重开 */
  const [helpOpen, setHelpOpen] = useState(false);

  /** 快捷「不感兴趣」后的一句回执 —— 不给回执，用户不知道东西去哪了 */
  const [toast, setToast] = useState("");

  /** 置顶文案编辑中 */
  const [pinEditing, setPinEditing] = useState<string | null>(null);
  const [pinDraft, setPinDraft] = useState("");

  /** 官网公告（构建前抓的，详见 scripts/sync-events.mjs）；抓不到就是空 */
  const [notices, setNotices] = useState<NoticeBundle>({ generatedAt: null, notices: {} });

  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    const now = new Date();
    setState(normalizeEventsState(readJson(CAMPUS_KEYS.events), now));
    setToday(now);
    setReady(true);
  }, []);
  /* eslint-enable react-hooks/set-state-in-effect */

  /*
   * 公告是「锦上添花」：拉不到就不显示，绝不能因为一个 json 404 让整页白掉。
   * loadNotices 内部已经吞掉所有异常，这里只负责活着的时候别写进已卸载的组件。
   */
  useEffect(() => {
    let alive = true;
    void loadNotices().then((bundle) => {
      if (alive) setNotices(bundle);
    });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!ready) return;
    writeJson(CAMPUS_KEYS.events, state);
  }, [state, ready]);

  /* 回执自己退场：留着不动的提示会变成页面的一部分 */
  useEffect(() => {
    if (toast === "") return;
    const timer = window.setTimeout(() => setToast(""), TOAST_MS);
    return () => window.clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    const timer = window.setInterval(() => setToday(new Date()), CLOCK_MS);
    return () => window.clearInterval(timer);
  }, []);

  const patch = useCallback((next: Partial<EventsState>) => {
    setState((prev) => ({ ...prev, ...next }));
  }, []);

  /* ---------- 派生 ---------- */

  const filtered = useMemo(
    () =>
      filterItems(ALL_ITEMS, {
        marks: state.marks,
        categories,
        // 大四的「只看升学就业」只在设了大四时生效：其他年级即使开关还留着 true，
        // 也不该突然把整张年历表缩成一类 —— 那是用户换了年级、忘了关开关的情况
        careerOnly: state.careerOnly && state.grade === 4,
      }),
    [state.marks, state.careerOnly, state.grade, categories],
  );

  const pins = useMemo(
    () => buildPins(state.pins, ALL_ITEMS, today ?? new Date(), state.marks),
    [state.pins, state.marks, today],
  );

  const detailItem = detailId ? ALL_ITEMS.find((i) => i.id === detailId) ?? null : null;

  /* ---------- 操作 ---------- */

  const setMark = useCallback((id: string, mark: EventMark | null) => {
    setState((prev) => {
      const marks = { ...prev.marks };
      if (mark === null) delete marks[id];
      else marks[id] = mark;
      return { ...prev, marks };
    });
  }, []);

  /**
   * 日历上快捷「不感兴趣」。
   *
   * 走的是和详情页那颗「不感兴趣」完全相同的 mark 通道，不另开一套「隐藏」状态 ——
   * 两套状态会立刻长出两份对不上的清单：一处显示已收走，另一处还在。
   */
  const skipItem = useCallback((id: string) => {
    const item = ALL_ITEMS.find((i) => i.id === id);
    setState((prev) => ({ ...prev, marks: { ...prev.marks, [id]: "skip" } }));
    setToast(
      item
        ? `已把「${item.short}」从日历上收走 · 想找回就去「我的」`
        : "已从日历上收走 · 想找回就去「我的」",
    );
  }, []);

  const togglePin = useCallback((id: string) => {
    setState((prev) => {
      const has = prev.pins.some((p) => p.id === id);
      return {
        ...prev,
        pins: has ? prev.pins.filter((p) => p.id !== id) : [...prev.pins, { id }],
      };
    });
  }, []);

  const removePin = useCallback((id: string) => {
    setState((prev) => ({ ...prev, pins: prev.pins.filter((p) => p.id !== id) }));
    setPinEditing((cur) => (cur === id ? null : cur));
  }, []);

  const savePinText = useCallback(() => {
    if (!pinEditing) return;
    const id = pinEditing;
    const text = pinDraft.trim();
    setState((prev) => ({
      ...prev,
      pins: prev.pins.map((p) => (p.id === id ? { id, note: text === "" ? undefined : text } : p)),
    }));
    setPinEditing(null);
    setPinDraft("");
  }, [pinEditing, pinDraft]);

  /* ---------- 日历导航 ---------- */

  const cursor = useMemo(() => {
    const parsed = parseMonthKey(state.month);
    if (parsed) return parsed;
    const now = today ?? new Date();
    return { year: now.getFullYear(), month: now.getMonth() + 1 };
  }, [state.month, today]);

  const schoolYear = useMemo(() => schoolYearOf(new Date(cursor.year, cursor.month - 1, 15)), [cursor]);

  const shiftMonth = useCallback(
    (delta: number) => {
      const d = new Date(cursor.year, cursor.month - 1 + delta, 1);
      patch({ month: monthKey(d.getFullYear(), d.getMonth() + 1) });
    },
    [cursor, patch],
  );

  const shiftSchoolYear = useCallback(
    (delta: number) => {
      // 进学年统一落在 9 月：这是学年列表的第一格，比停在原来的月份更好理解
      patch({ month: monthKey(schoolYear + delta, 9) });
    },
    [schoolYear, patch],
  );

  const jumpMonth = useCallback(
    (year: number, month: number) => {
      patch({ month: monthKey(year, month), view: "calendar" });
    },
    [patch],
  );

  const toggleCategory = useCallback((id: EventCategoryId) => {
    setCategories((prev) =>
      prev.includes(id) ? prev.filter((c) => c !== id) : [...prev, id],
    );
  }, []);

  /* ---------- 渲染 ---------- */

  if (!ready || today === null) {
    return (
      <div id="campus-panel-events" role="tabpanel" aria-label="大学年历表">
        <div className="campus-card">
          <p className="campus-empty">正在读取本地记录…</p>
        </div>
      </div>
    );
  }

  const todayParts = {
    year: today.getFullYear(),
    month: today.getMonth() + 1,
    day: today.getDate(),
  };

  const showPins = state.view !== "mine" && pins.length > 0;

  return (
    <div id="campus-panel-events" role="tabpanel" aria-label="大学年历表">
      {/* ---------- 控制区 ---------- */}
      <div className="campus-card">
        <div className="campus-card-head">
          <h2 className="campus-card-title">大学年历表</h2>
          <div className="campus-ev-head-ops">
            <span className="campus-card-note" style={{ margin: 0 }}>
              {filtered.length === ALL_ITEMS.length
                ? `收录 ${ALL_ITEMS.length} 条`
                : `筛出 ${filtered.length} / ${ALL_ITEMS.length} 条`}
            </span>
            <button type="button" className="campus-ev-help" onClick={() => setHelpOpen(true)}>
              使用说明
            </button>
          </div>
        </div>

        <p className="campus-ev-lead">
          哪些比赛什么时候该报名、哪些证什么时候考。时间按往年规律整理，
          <strong>院赛校赛以本校教务处、团委通知为准</strong>。
        </p>

        <div className="campus-ev-bar">
          <div className="campus-ev-seg" role="group" aria-label="切换视图">
            {VIEW_LABELS.map((v) => (
              <button
                key={v.id}
                type="button"
                className={`campus-ev-seg-btn${state.view === v.id ? " is-active" : ""}`}
                aria-pressed={state.view === v.id}
                onClick={() => patch({ view: v.id })}
              >
                {v.label}
              </button>
            ))}
          </div>

          <div className="campus-ev-year">
            <button
              type="button"
              className="campus-ev-nav"
              aria-label="上一学年"
              onClick={() => shiftSchoolYear(-1)}
            >
              ‹
            </button>
            <span className="campus-ev-year-label">{schoolYearLabel(schoolYear)}</span>
            <button
              type="button"
              className="campus-ev-nav"
              aria-label="下一学年"
              onClick={() => shiftSchoolYear(1)}
            >
              ›
            </button>
          </div>

          <label className="campus-ev-grade">
            <span className="campus-ev-grade-label">我的年级</span>
            <select
              className="campus-input campus-ev-select"
              value={state.grade === null ? "" : String(state.grade)}
              onChange={(e) => {
                const v = e.target.value;
                patch({ grade: v === "" ? null : (Number(v) as 1 | 2 | 3 | 4) });
              }}
            >
              <option value="">不设置</option>
              {GRADE_LABELS.map((g, i) => (
                <option key={g} value={i + 1}>
                  {g}
                </option>
              ))}
            </select>
          </label>
        </div>

        {state.grade === 4 && (
          <label className="campus-ev-toggle">
            <input
              type="checkbox"
              checked={state.careerOnly}
              onChange={(e) => patch({ careerOnly: e.target.checked })}
            />
            <span>
              只看升学就业（考研 / 国考 / 省考）
              <em>关掉就看全部 —— 大四也有还能打的比赛</em>
            </span>
          </label>
        )}

        {showPins && (
          <div className="campus-ev-pins">
            <p className="campus-ev-pins-title">置顶提醒</p>
            <ul className="campus-ev-pins-chips">
              {pins.slice(0, 4).map((pin) => (
                <li key={pin.id}>
                  <button
                    type="button"
                    className="campus-ev-pin-chip"
                    onClick={() => pin.item && setDetailId(pin.item.id)}
                    disabled={!pin.item}
                  >
                    <span className="campus-ev-pin-chip-label">{pin.label}</span>
                    <span className="campus-ev-pin-chip-when">
                      {pin.next ? daysText(pin.next.daysLeft) : "今年不办"}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* ---------- 内容区 ---------- */}
      <div className="campus-card">
        {/* 分类筛选对「我的」不起作用：那一栏是按标记分组的，不按分类。
            摆着点了没反应的控件，比不摆更让人怀疑自己点错了。 */}
        {state.view !== "mine" && (
        <div className="campus-ev-filters" role="group" aria-label="按分类筛选">
          {/* 「全部」常驻会把九个分类挤到第二行；它本来也只在有筛选时才有意义。
              没筛选时这里是「列着九个方向」，选了之后才多出一个「清除筛选」。 */}
          {categories.length > 0 && (
            <button type="button" className="campus-chip" onClick={() => setCategories([])}>
              清除筛选
            </button>
          )}
          {CATEGORIES.map((cat) => (
            <button
              key={cat.id}
              type="button"
              className={`campus-chip campus-ev-chip${categories.includes(cat.id) ? " is-on" : ""}`}
              aria-pressed={categories.includes(cat.id)}
              onClick={() => toggleCategory(cat.id)}
            >
              <span className="campus-ev-dot" style={{ background: cat.color }} aria-hidden="true" />
              {cat.name}
            </button>
          ))}
        </div>
        )}

        {filtered.length === 0 && state.view !== "mine" ? (
          <p className="campus-empty">
            这些分类下没有未处理的条目。换个分类，或者去「我的」把标记过的放回年历表。
          </p>
        ) : state.view === "calendar" ? (
          <CalendarView
            items={filtered}
            year={cursor.year}
            month={cursor.month}
            today={todayParts}
            grade={state.grade}
            onShiftMonth={shiftMonth}
            onPickDay={(day) => setDaySheet({ year: cursor.year, month: cursor.month, day })}
            onPickItem={(id) => setDetailId(id)}
            onSkipItem={skipItem}
          />
        ) : state.view === "timeline" ? (
          <TimelineView
            items={filtered}
            schoolYear={schoolYear}
            today={today}
            grade={state.grade}
            onPickItem={(id) => setDetailId(id)}
            onPickMonth={jumpMonth}
          />
        ) : (
          <MineView
            items={ALL_ITEMS}
            state={state}
            pins={pins}
            editingPin={pinEditing}
            draftPinText={pinDraft}
            onBeginEditPin={(id) => {
              const pin = state.pins.find((p) => p.id === id);
              setPinEditing(id);
              setPinDraft(pin?.note ?? "");
            }}
            onChangePinText={setPinDraft}
            onSavePin={savePinText}
            onRemovePin={removePin}
            onPickItem={(id) => setDetailId(id)}
            onUnmark={(id) => setMark(id, null)}
          />
        )}
      </div>

      <p className="campus-ev-foot">
        数据最后核对 2026-11 · 收录中国高等教育学会竞赛目录中的本科赛事与常见考证 ·
        时间随年份变动，报名前请以本校教务处 / 团委与赛事官网为准
      </p>

      {/* ---------- 某日展开 ---------- */}
      {daySheet && (
        <DaySheet
          year={daySheet.year}
          month={daySheet.month}
          day={daySheet.day}
          items={filtered}
          onClose={() => setDaySheet(null)}
          onPickItem={(id) => {
            setDaySheet(null);
            setDetailId(id);
          }}
          onSkipItem={skipItem}
        />
      )}

      {/* ---------- 条目详情 ---------- */}
      {detailItem && (
        <EventDetail
          item={detailItem}
          grade={state.grade}
          mark={state.marks[detailItem.id]}
          pinned={state.pins.some((p) => p.id === detailItem.id)}
          notices={notices.notices[detailItem.id]}
          syncedAt={notices.generatedAt}
          onClose={() => setDetailId(null)}
          onMark={(mark) => setMark(detailItem.id, mark)}
          onTogglePin={() => togglePin(detailItem.id)}
        />
      )}

      {/* ---------- 使用说明 ---------- */}
      {(helpOpen || !state.introSeen) && (
        <IntroSheet
          onClose={() => {
            setHelpOpen(false);
            // 主动重开看过的不写也行，这里统一写：只留一个「看过了」的判定，少一个分支
            if (!state.introSeen) patch({ introSeen: true });
          }}
        />
      )}

      {/* ---------- 一次性回执 ---------- */}
      {toast !== "" && (
        <p className="campus-ev-toast" role="status">
          {toast}
        </p>
      )}
    </div>
  );
}

/**
 * 某一天延展出来的窗口。
 *
 * **只列这一天的事。** 之前这里还挂了「本月其余 N 件（只有月份精度）」，
 * 结果点 11 月的任意一天，都会弹出二十多件和这一天无关的东西 ——
 * 用户点的是「12 号」，看到的却是整个 11 月。那不叫回答问题，只是把日历又抄了一遍。
 * 月份精度的事归「本月清单」：日期都没核实到日，就不该出现在某一天的窗口里。
 *
 * 每条都能直接点进详情 —— 这是「先看有哪几件、再挑一件了解」的那一步；
 * 不想看的一条可以就地收走，不必为了点「不感兴趣」先进一次详情。
 */
function DaySheet({
  year,
  month,
  day,
  items,
  onClose,
  onPickItem,
  onSkipItem,
}: {
  year: number;
  month: number;
  day: number;
  items: readonly EventItem[];
  onClose: () => void;
  onPickItem: (id: string) => void;
  onSkipItem: (id: string) => void;
}) {
  const onDay = monthOccurrences(items, year, month).filter((occ) => occ.precise && occ.day === day);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="campus-modal"
      role="dialog"
      aria-modal="true"
      aria-label={`${year} 年 ${month} 月 ${day} 日`}
    >
      <button type="button" className="campus-modal-mask" aria-label="关闭" onClick={onClose} />
      <div className="campus-modal-card campus-ev-daysheet">
        <div className="campus-modal-head">
          <h2 className="campus-ev-detail-title">
            {year} 年 {month} 月 {day} 日
            <span className="campus-ev-daysheet-count">
              {onDay.length > 0 ? `当天 ${onDay.length} 件` : "没有安排"}
            </span>
          </h2>
          <button type="button" className="campus-modal-close" aria-label="关闭" onClick={onClose}>
            ×
          </button>
        </div>

        <div className="campus-ev-daysheet-body">
          {onDay.length === 0 ? (
            <div className="campus-ev-daysheet-empty">
              <p className="campus-ev-daysheet-empty-title">这一天没有已核实的安排</p>
              <p className="campus-ev-daysheet-empty-note">
                只有月份精度的事留在「本月清单」里，不按天拆 —— 日期年年浮动，
                硬摊到某一天是假精确。
              </p>
            </div>
          ) : (
            <ul className="campus-ev-rows">
              {onDay.map((occ, i) => {
                const cat = CATEGORY_MAP[occ.item.category];
                return (
                  <li key={`${occ.item.id}-${occ.phase}-${i}`} className="campus-ev-row-item">
                    <button
                      type="button"
                      className="campus-ev-row"
                      onClick={() => onPickItem(occ.item.id)}
                    >
                      <span
                        className="campus-ev-row-mark"
                        style={{ background: cat.color }}
                        aria-hidden="true"
                      />
                      <span className="campus-ev-row-when">
                        当天
                        <span className="campus-ev-row-phase">
                          {phaseText(occ.phase, occ.item.kind)}
                        </span>
                      </span>
                      <span className="campus-ev-row-name">{occ.item.name}</span>
                      <span className="campus-ev-row-cat">{cat.name}</span>
                    </button>
                    <button
                      type="button"
                      className="campus-ev-row-skip"
                      onClick={() => onSkipItem(occ.item.id)}
                      aria-label={`对「${occ.item.name}」不感兴趣，从日历上收走`}
                    >
                      不感兴趣
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
