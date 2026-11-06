"use client";

/**
 * 课表的三个弹窗：导入、学期设置、上课时间。
 *
 * 拆出来的原因很实际：这几块表单各自有独立的临时状态（粘贴的 JSON、开学日期、逐节时间…），
 * 塞进主组件会让「课表本体」和「一次性表单」的状态混在一起，改一个容易碰到另一个。
 *
 * 调用方式是「挂载即打开」—— 父级用 `{open && <ImportDialog />}` 控制，
 * 关闭时组件卸载，内部草稿自动丢弃，不需要额外的重置逻辑。
 */

import { useState } from "react";
import {
  DEFAULT_BREAK_MINUTES,
  DEFAULT_FIRST_START,
  DEFAULT_LESSON_MINUTES,
  MAX_PERIOD_COUNT,
  MIN_PERIOD_COUNT,
  generateStarts,
  resizeStarts,
  toMinutes,
} from "../../logic/schedule";
import type { PeriodPlan } from "../../types";

/* ------------------------------------------------------------------ *
 * 导入课表
 * ------------------------------------------------------------------ */

const SAMPLE_JSON = `{
  "courses": [
    {
      "name": "高等数学",
      "dayOfWeek": 1,
      "sections": [1, 2],
      "teacher": "张教授",
      "classroom": "教三-201",
      "weeks": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16],
      "notes": "单双周都上"
    },
    {
      "name": "大学英语",
      "dayOfWeek": 3,
      "sections": [3, 4],
      "classroom": "外语楼-305"
    }
  ]
}`;

/** 从 AI 那边整理课表用的提示词，旧版有，用户已经习惯这条路 */
const AI_PROMPT = `你是一个课程表整理助手。请把我给的课表整理成如下 JSON，只输出 JSON、不要解释：

{
  "courses": [
    {
      "name": "课程名",
      "dayOfWeek": 1,
      "sections": [1, 2],
      "teacher": "老师(可选)",
      "classroom": "教室(可选)",
      "weeks": [1, 2, 3],
      "notes": "备注(可选)"
    }
  ]
}

我的课表如下：
（把教务导出 / 截图转文字 / 手写的课表粘贴到这里）`;

export interface ImportFeedback {
  ok: boolean;
  message: string;
}

interface ImportDialogProps {
  onClose: () => void;
  /** 交给父级解析合并，返回结果文案 */
  onImport: (text: string) => ImportFeedback;
  /** 生成当前课表的 JSON 文本（下载由父级做） */
  onExport: () => string;
}

export function ImportDialog({ onClose, onImport, onExport }: ImportDialogProps) {
  const [text, setText] = useState("");
  const [feedback, setFeedback] = useState<ImportFeedback | null>(null);

  function handleImport() {
    const result = onImport(text);
    setFeedback(result);
    // 导入成功就收工：结果已经写到页面上的提示行里，弹窗继续留着会挡住下一步
    //（D2：首次导入会紧接着弹出学期设置）
    if (result.ok) onClose();
  }

  function handleFile(file: File | undefined) {
    if (!file) return;

    const reader = new FileReader();
    reader.onload = () => {
      const content = String(reader.result ?? "");
      setText(content);
      // 选完文件直接导，少一步点击 —— 旧版就是这个行为
      const result = onImport(content);
      setFeedback(result);
      if (result.ok) onClose();
    };
    reader.onerror = () => setFeedback({ ok: false, message: "读不了这个文件，试试把内容直接粘进来。" });
    reader.readAsText(file, "utf-8");
  }

  return (
    <div className="campus-modal" role="dialog" aria-modal="true" aria-labelledby="campus-import-title">
      <button
        type="button"
        className="campus-modal-mask"
        aria-label="关闭导入"
        onClick={onClose}
      />

      <div className="campus-modal-card">
        <div className="campus-modal-head">
          <h2 id="campus-import-title" className="campus-card-title">
            导入课表
          </h2>
          <button type="button" className="campus-btn" onClick={onClose}>
            关闭
          </button>
        </div>

        <p className="campus-card-note" style={{ marginTop: 0 }}>
          把整理好的 JSON 粘进来，或者选一个 <b>.json</b> 文件。课名 + 星期 + 节次 + 周次都相同的课
          不会重复导入；带了 <b>weeks</b> 的课只在对应周次出现，没带的当作每周都上。
        </p>

        <textarea
          className="campus-textarea"
          rows={10}
          value={text}
          spellCheck={false}
          placeholder='粘贴形如 {"courses":[{"name":"高等数学","dayOfWeek":1,"sections":[1,2]}]} 的 JSON'
          onChange={(event) => setText(event.target.value)}
        />

        <div className="campus-add-row" style={{ marginTop: 12 }}>
          <button type="button" className="campus-btn is-primary" onClick={handleImport}>
            导入
          </button>

          <label className="campus-btn" style={{ cursor: "pointer" }}>
            选文件…
            <input
              type="file"
              accept=".json,.txt,application/json,text/plain"
              hidden
              onChange={(event) => {
                handleFile(event.target.files?.[0]);
                // 清空 value，否则同一个文件选第二次不触发 change
                event.target.value = "";
              }}
            />
          </label>

          <button type="button" className="campus-btn" onClick={() => setText(SAMPLE_JSON)}>
            填示例
          </button>

          <button
            type="button"
            className="campus-btn"
            onClick={() => {
              const json = onExport();
              setText(json);
              try {
                const blob = new Blob([json], { type: "application/json" });
                const url = URL.createObjectURL(blob);
                const link = document.createElement("a");
                link.href = url;
                link.download = "我的课表.json";
                document.body.appendChild(link);
                link.click();
                link.remove();
                // 立刻 revoke 会让部分浏览器下载失败，等下一轮事件循环
                window.setTimeout(() => URL.revokeObjectURL(url), 0);
                setFeedback({ ok: true, message: "已导出为「我的课表.json」，内容也填在上面的框里了。" });
              } catch {
                setFeedback({ ok: true, message: "内容已填在框里，浏览器不允许自动下载，请手动复制。" });
              }
            }}
          >
            导出我的课表
          </button>
        </div>

        {feedback && (
          <p
            className="campus-card-note"
            role="status"
            style={{ color: feedback.ok ? "var(--success-ink)" : "var(--red-ink)" }}
          >
            {feedback.message}
          </p>
        )}

        <details className="campus-details">
          <summary>手上还没有课表草稿？点开复制这段提示词去问知穗 AI</summary>
          <div className="campus-add-row" style={{ marginTop: 8 }}>
            <button
              type="button"
              className="campus-btn"
              onClick={() => {
                // 剪贴板 API 在非安全上下文不可用，失败时不打断流程
                void navigator.clipboard?.writeText(AI_PROMPT).then(
                  () => setFeedback({ ok: true, message: "提示词已复制。" }),
                  () => setFeedback({ ok: false, message: "复制失败，请手动选中下面的文字。" }),
                );
              }}
            >
              复制提示词
            </button>
          </div>
          <pre className="campus-prompt">{AI_PROMPT}</pre>
        </details>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * 学期设置
 * ------------------------------------------------------------------ */

interface TermDialogProps {
  /** 已保存的开学日期，YYYY-MM-DD；没有则空串 */
  defaultStart: string;
  /** 已保存的总周数 */
  defaultWeeks: number;
  /** 日历算出来的当前教学周；0 表示还没法算 */
  currentWeekNo: number;
  onClose: () => void;
  /**
   * 保存。
   * `nowWeek` 有值时表示用户选了「直接告诉我现在是第几周」，由父级反推开学日期。
   */
  onSave: (payload: { start: string; nowWeek: number | null; weeks: number }) => void;
}

export function TermDialog({
  defaultStart,
  defaultWeeks,
  currentWeekNo,
  onClose,
  onSave,
}: TermDialogProps) {
  const [start, setStart] = useState(defaultStart);
  const [nowWeek, setNowWeek] = useState("");
  const [weeks, setWeeks] = useState(String(defaultWeeks));
  const [message, setMessage] = useState("");

  function handleSave() {
    const totalWeeks = Math.min(30, Math.max(1, Number.parseInt(weeks, 10) || 20));
    const parsedNow = Number.parseInt(nowWeek, 10);

    if (start === "" && !(parsedNow >= 1)) {
      setMessage("填一个开学日期，或者直接告诉我现在是第几周。");
      return;
    }

    onSave({
      start,
      nowWeek: start === "" && parsedNow >= 1 ? Math.min(30, parsedNow) : null,
      weeks: totalWeeks,
    });
  }

  return (
    <div className="campus-modal" role="dialog" aria-modal="true" aria-labelledby="campus-term-title">
      <button type="button" className="campus-modal-mask" aria-label="关闭学期设置" onClick={onClose} />

      <div className="campus-modal-card" style={{ maxWidth: 420 }}>
        <div className="campus-modal-head">
          <h2 id="campus-term-title" className="campus-card-title">
            本学期设置
          </h2>
          <button type="button" className="campus-btn" onClick={onClose}>
            关闭
          </button>
        </div>

        <p className="campus-card-note" style={{ marginTop: 0 }}>
          课表要配上日历，才知道「这周是第几周」，也才能在周几下面标上日期。
          {currentWeekNo > 0 && ` 按现在的设置，日历上是第 ${currentWeekNo} 周。`}
        </p>

        <div className="campus-term-grid">
          <label className="campus-field">
            <span>开学日期（第 1 周周一）</span>
            <input
              className="campus-input"
              type="date"
              value={start}
              onChange={(event) => {
                setStart(event.target.value);
                setMessage("");
              }}
            />
          </label>

          <label className="campus-field">
            <span>学期总周数</span>
            <input
              className="campus-input"
              type="number"
              min={6}
              max={30}
              value={weeks}
              onChange={(event) => setWeeks(event.target.value)}
            />
          </label>
        </div>

        <p className="campus-term-or">不知道开学日期？告诉我现在是第几周也一样</p>

        <div className="campus-add-row" style={{ alignItems: "flex-end" }}>
          <label className="campus-field" style={{ maxWidth: 170 }}>
            <span>今天是第几周</span>
            <input
              className="campus-input"
              type="number"
              min={1}
              max={30}
              value={nowWeek}
              placeholder="如 9"
              disabled={start !== ""}
              onChange={(event) => {
                setNowWeek(event.target.value);
                setMessage("");
              }}
            />
          </label>
          <p className="campus-card-note" style={{ flex: "1 1 150px", margin: 0 }}>
            填了开学日期就自动算，这一栏可以空着。
          </p>
        </div>

        {message !== "" && (
          <p className="campus-card-note" role="status" style={{ color: "var(--red-ink)" }}>
            {message}
          </p>
        )}

        <div className="campus-add-row" style={{ marginTop: 12 }}>
          <button type="button" className="campus-btn is-primary" onClick={handleSave}>
            保存
          </button>
          <button type="button" className="campus-btn" onClick={onClose}>
            稍后再说
          </button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * 上课时间（B2 · 需求 4.1 + A03）
 * ------------------------------------------------------------------ */

interface PeriodDialogProps {
  /** 当前作息；弹窗内的改动不改父级，点保存才写回 */
  plan: PeriodPlan;
  onClose: () => void;
  onSave: (plan: PeriodPlan) => void;
}

/**
 * 「每节几点开始」的设置面板。
 *
 * 两条路都给：
 *  ① 一键重排 —— 填「第一节几点、每节多长、课间多长」，整段覆盖，适合第一次设置；
 *  ② 逐节微调 —— 学校作息往往有零碎的例外（周四早操推迟十分钟），只能一节一节改。
 * 只给 ① 会让例外无从下手，只给 ② 要手填 12 个时间，都算没做完。
 */
export function PeriodDialog({ plan, onClose, onSave }: PeriodDialogProps) {
  const [count, setCount] = useState(plan.count);
  const [starts, setStarts] = useState<string[]>(plan.starts);
  const [first, setFirst] = useState(plan.starts[0] ?? DEFAULT_FIRST_START);
  const [duration, setDuration] = useState(String(plan.duration));
  const [gap, setGap] = useState(String(DEFAULT_BREAK_MINUTES));
  const [message, setMessage] = useState("");

  const minutes = Math.min(120, Math.max(20, Number.parseInt(duration, 10) || DEFAULT_LESSON_MINUTES));

  function changeCount(next: number) {
    setCount(next);
    // 保留已经调过的前几节，只补/砍尾部 —— 改节数不该把上午的时间清空
    setStarts(resizeStarts({ count: next, starts, duration: minutes }, next));
  }

  function regenerate() {
    const next = generateStarts({
      count,
      start: first,
      duration: minutes,
      gap: Number.parseInt(gap, 10),
    });
    setStarts(next);
    setFirst(next[0]);
    setMessage(`已按「${first} 开始、每节 ${minutes} 分钟」重排 ${count} 节。`);
  }

  function handleSave() {
    const blank = starts.findIndex((time) => toMinutes(time) === null);
    if (blank >= 0) {
      setMessage(`第 ${blank + 1} 节的时间没填或写得不对，补一下再保存。`);
      return;
    }
    onSave({ count, starts, duration: minutes });
  }

  return (
    <div className="campus-modal" role="dialog" aria-modal="true" aria-labelledby="campus-period-title">
      <button type="button" className="campus-modal-mask" aria-label="关闭上课时间" onClick={onClose} />

      <div className="campus-modal-card" style={{ maxWidth: 520 }}>
        <div className="campus-modal-head">
          <h2 id="campus-period-title" className="campus-card-title">
            上课时间
          </h2>
          <button type="button" className="campus-btn" onClick={onClose}>
            关闭
          </button>
        </div>

        <p className="campus-card-note" style={{ marginTop: 0 }}>
          每所学校打铃的时间都不一样，这里按你自己的作息填。
          改完之后，课表左侧的节次下面、课程详情里的钟点都会跟着变。
        </p>

        <div className="campus-period-gen">
          <label className="campus-field">
            <span>每天几节</span>
            <select
              className="campus-select"
              value={String(count)}
              onChange={(event) => changeCount(Number.parseInt(event.target.value, 10))}
            >
              {Array.from({ length: MAX_PERIOD_COUNT - MIN_PERIOD_COUNT + 1 }, (_, i) => {
                const n = MIN_PERIOD_COUNT + i;
                return (
                  <option key={n} value={n}>
                    {n} 节
                  </option>
                );
              })}
            </select>
          </label>

          <label className="campus-field">
            <span>第一节</span>
            <input
              className="campus-input"
              type="time"
              value={first}
              onChange={(event) => {
                setFirst(event.target.value);
                setMessage("");
              }}
            />
          </label>

          <label className="campus-field">
            <span>每节（分）</span>
            <input
              className="campus-input"
              type="number"
              min={20}
              max={120}
              step={5}
              value={duration}
              onChange={(event) => {
                setDuration(event.target.value);
                setMessage("");
              }}
            />
          </label>

          <label className="campus-field">
            <span>课间（分）</span>
            <input
              className="campus-input"
              type="number"
              min={0}
              max={60}
              step={5}
              value={gap}
              onChange={(event) => {
                setGap(event.target.value);
                setMessage("");
              }}
            />
          </label>
        </div>

        <div className="campus-add-row" style={{ marginTop: 10 }}>
          <button type="button" className="campus-btn is-primary" onClick={regenerate}>
            按上面的值重排
          </button>
          <span className="campus-card-note" style={{ margin: 0 }}>
            12:00–14:00 的空档会自动跳过。
          </span>
        </div>

        <p className="campus-term-or">下面是逐节微调，学校有零碎的例外就改这里</p>

        <div className="campus-period-grid">
          {starts.map((time, index) => (
            <label key={index} className="campus-field">
              <span>第 {index + 1} 节</span>
              <input
                className="campus-input"
                type="time"
                value={time}
                onChange={(event) => {
                  const next = starts.slice();
                  next[index] = event.target.value;
                  setStarts(next);
                  setMessage("");
                }}
              />
            </label>
          ))}
        </div>

        {message !== "" && (
          <p className="campus-card-note" role="status" style={{ color: "var(--accent-ink)" }}>
            {message}
          </p>
        )}

        {/* 手机上一屏放不下 14 节，保存按钮贴在底边跟着滚，不用摸黑往下找 */}
        <div className="campus-period-actions">
          <button type="button" className="campus-btn is-primary" onClick={handleSave}>
            保存
          </button>
          <button type="button" className="campus-btn" onClick={onClose}>
            取消
          </button>
        </div>
      </div>
    </div>
  );
}
