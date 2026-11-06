"use client";

/**
 * 课程表单与课程详情弹窗（B1 · 2026-11-06）。
 *
 * 拆出来的原因和 `ScheduleDialogs.tsx` 一样：这两块都有独立草稿状态，塞进主组件会让
 *「课表本体」和「一次性表单」的状态搅在一起。区别是这里被**两处复用**：
 *  - 页面里的「添加课程」页内展开面板；
 *  - 点课块弹出的详情弹窗里的「编辑」。
 * 两边字段完全一样，抽成一个 `CourseForm` 才不可能出现「加课能填周次、改课不能」这种偏差。
 *
 * 排版规则（对应需求 3.1「各输入栏对齐、间距统一」）：6 个字段进 CSS Grid，
 * 手机上 2 列、桌面 6 列，跨列数由 `.is-name` / `.is-weeks` / `.campus-course-form-actions`
 * 三个类控制 —— 不再用 flex 的 `flex: 1.4 1 140px` 硬凑，那正是原来「不整齐」的来源。
 */

import { useState } from "react";
import { DAY_LABELS, formatSections, formatWeeks } from "../../logic/schedule";
import type { Course } from "../../types";

export interface CourseDraft {
  name: string;
  /** 1–7，和表单里的选项值一致；落库时再减 1 */
  day: string;
  /** 用户输入的节次原文，如 `1,2` / `1-3` */
  sections: string;
  room: string;
  teacher: string;
  weeks: string;
}

export const EMPTY_DRAFT: CourseDraft = {
  name: "",
  day: "1",
  sections: "",
  room: "",
  teacher: "",
  weeks: "",
};

/** 已有课程 → 表单草稿。周次回填成 `1-16` 这种紧凑写法，用户看着比「第1-16周」好改 */
export function draftFromCourse(course: Course): CourseDraft {
  return {
    name: course.name,
    day: String(course.day + 1),
    sections: course.sections.join(","),
    room: course.classroom,
    teacher: course.teacher,
    weeks:
      course.weeks && course.weeks.length > 0
        ? formatWeeks(course.weeks).replace(/[第周]/g, "")
        : "",
  };
}

interface CourseFormProps {
  initial: CourseDraft;
  submitLabel: string;
  /** 校验失败的提示，由父级决定文案 */
  message?: string;
  /** 每天几节，只用来给「节次」一栏写上限，避免用户填了第 13 节却看不见 */
  maxSection?: number;
  onSubmit: (draft: CourseDraft) => void;
  onCancel: () => void;
}

export function CourseForm({
  initial,
  submitLabel,
  message = "",
  maxSection,
  onSubmit,
  onCancel,
}: CourseFormProps) {
  const [draft, setDraft] = useState<CourseDraft>(initial);

  function patch(next: Partial<CourseDraft>) {
    setDraft((prev) => ({ ...prev, ...next }));
  }

  return (
    <form
      className="campus-course-form"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit(draft);
      }}
    >
      <label className="campus-field is-name">
        <span>课程名</span>
        <input
          className="campus-input"
          type="text"
          placeholder="如：高等数学"
          value={draft.name}
          onChange={(event) => patch({ name: event.target.value })}
        />
      </label>

      <label className="campus-field is-day">
        <span>星期</span>
        <select
          className="campus-select"
          value={draft.day}
          onChange={(event) => patch({ day: event.target.value })}
        >
          {DAY_LABELS.map((name, index) => (
            <option key={name} value={String(index + 1)}>
              {name}
            </option>
          ))}
        </select>
      </label>

      <label className="campus-field is-sections">
        <span>节次{maxSection ? `（1–${maxSection}）` : ""}</span>
        <input
          className="campus-input"
          type="text"
          placeholder={maxSection ? `如 1,2 或 1-${Math.min(4, maxSection)}` : "1,2 或 1-3"}
          value={draft.sections}
          onChange={(event) => patch({ sections: event.target.value })}
        />
      </label>

      <label className="campus-field is-room">
        <span>教室</span>
        <input
          className="campus-input"
          type="text"
          placeholder="可选"
          value={draft.room}
          onChange={(event) => patch({ room: event.target.value })}
        />
      </label>

      <label className="campus-field is-teacher">
        <span>老师</span>
        <input
          className="campus-input"
          type="text"
          placeholder="可选"
          value={draft.teacher}
          onChange={(event) => patch({ teacher: event.target.value })}
        />
      </label>

      <label className="campus-field is-weeks">
        <span>周次</span>
        <input
          className="campus-input"
          type="text"
          placeholder="留空＝每周都上，可写 1-16 或 1,3,5"
          value={draft.weeks}
          onChange={(event) => patch({ weeks: event.target.value })}
        />
      </label>

      <div className="campus-course-form-actions">
        <button type="submit" className="campus-btn is-primary">
          {submitLabel}
        </button>
        <button type="button" className="campus-btn" onClick={onCancel}>
          取消
        </button>
        {message !== "" && (
          <span className="campus-form-msg" role="alert">
            {message}
          </span>
        )}
      </div>
    </form>
  );
}

/* ------------------------------------------------------------------ *
 * 课程详情 / 编辑弹窗
 * ------------------------------------------------------------------ */

interface CourseDialogProps {
  /** 当前打开的课程；由父级按 id 从快照里取，编辑后自动是最新值 */
  course: Course;
  /** 这条课在这台设备上是否只上某些周，用于文案 */
  weekText: string;
  /** 节次对应的钟点，如 `08:00–09:40`；由父级按当前作息算（用户可以改过） */
  timeText: string;
  /** 每天几节，传给表单写上限 */
  maxSection: number;
  /** 返回 null 表示保存成功（父级会关掉弹窗），返回字符串表示错误提示 */
  onSave: (draft: CourseDraft) => string | null;
  onDelete: () => void;
  onClose: () => void;
}

export function CourseDialog({
  course,
  weekText,
  timeText,
  maxSection,
  onSave,
  onDelete,
  onClose,
}: CourseDialogProps) {
  const [editing, setEditing] = useState(false);
  const [message, setMessage] = useState("");

  return (
    <div className="campus-modal" role="dialog" aria-modal="true" aria-labelledby="campus-course-title">
      <button
        type="button"
        className="campus-modal-mask"
        aria-label="关闭课程详情"
        onClick={onClose}
      />

      <div className="campus-modal-card" style={{ maxWidth: 480 }}>
        <div className="campus-modal-head">
          <h2 id="campus-course-title" className="campus-card-title">
            {editing ? "编辑课程" : course.name}
          </h2>
          <button type="button" className="campus-btn" onClick={onClose}>
            关闭
          </button>
        </div>

        {editing ? (
          <CourseForm
            initial={draftFromCourse(course)}
            submitLabel="保存"
            message={message}
            maxSection={maxSection}
            onSubmit={(draft) => {
              const error = onSave(draft);
              if (error) setMessage(error);
            }}
            onCancel={() => {
              setEditing(false);
              setMessage("");
            }}
          />
        ) : (
          <>
            <dl className="campus-course-facts">
              <div>
                <dt>时间</dt>
                <dd>
                  {DAY_LABELS[course.day]} · {formatSections(course.sections)}
                  {timeText && <span className="campus-fact-sub"> {timeText}</span>}
                </dd>
              </div>
              <div>
                <dt>周次</dt>
                <dd>{weekText}</dd>
              </div>
              {course.classroom && (
                <div>
                  <dt>教室</dt>
                  <dd>{course.classroom}</dd>
                </div>
              )}
              {course.teacher && (
                <div>
                  <dt>老师</dt>
                  <dd>{course.teacher}</dd>
                </div>
              )}
              {course.notes && (
                <div>
                  <dt>备注</dt>
                  <dd>{course.notes}</dd>
                </div>
              )}
            </dl>

            <div className="campus-add-row" style={{ marginTop: 14 }}>
              <button type="button" className="campus-btn is-primary" onClick={() => setEditing(true)}>
                编辑
              </button>
              <button type="button" className="campus-btn is-danger" onClick={onDelete}>
                删除这门课
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
