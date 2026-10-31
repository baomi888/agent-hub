"use client";

/**
 * Deadline 倒计时。
 *
 * 保留旧版的行为：剩余 ≤7 天标红、过期显示「已逾期 N 天」、临近的排前面、行内可反复编辑。
 * 允许编辑这件事是用户明确提过的（「编辑完还能再编辑」），所以没有做成一次性表单。
 *
 * 新增一处：页面挂着不动时，剩余天数每分钟重算一次。旧版只在渲染时算一次，
 * 跨过零点还显示昨天的天数。
 */

import { useEffect, useMemo, useState } from "react";
import {
  daysLeft,
  migrateLegacyDeadline,
  normalizeName,
  parseDateInput,
  sampleDeadlines,
  sortByDate,
  statusOf,
  statusText,
  toDateInput,
} from "../../logic/deadline";
import { readWithMigration, writeJson } from "../../storage";
import { CAMPUS_KEYS, LEGACY_KEYS } from "../../storage/keys";
import type { DeadlineItem } from "../../types";

/** 一分钟重算一次剩余天数：够用，也不会让页面一直重渲染 */
const CLOCK_MS = 60_000;

export default function DeadlineTool() {
  const [items, setItems] = useState<DeadlineItem[]>([]);
  const [ready, setReady] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  // 新增表单
  const [draftName, setDraftName] = useState("");
  const [draftDate, setDraftDate] = useState("");

  // 行内编辑
  const [editing, setEditing] = useState<number | null>(null);
  const [editName, setEditName] = useState("");
  const [editDate, setEditDate] = useState("");

  const [message, setMessage] = useState("");

  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    const restored = readWithMigration<DeadlineItem[]>(
      CAMPUS_KEYS.deadline,
      [LEGACY_KEYS.deadline],
      migrateLegacyDeadline,
    );

    // 迁移函数对空数组也返回 []（用户可能把所有事项删光了），所以用 null 判断而不是长度
    setItems(restored === null ? sampleDeadlines() : sortByDate(restored));
    setReady(true);
  }, []);
  /* eslint-enable react-hooks/set-state-in-effect */

  useEffect(() => {
    if (!ready) return;
    writeJson(CAMPUS_KEYS.deadline, items);
  }, [items, ready]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), CLOCK_MS);
    return () => window.clearInterval(timer);
  }, []);

  // items 本身保持有序，这里再排一次只是防御脏数据
  const ordered = useMemo(() => sortByDate(items), [items]);

  function addItem() {
    const name = normalizeName(draftName);
    const time = parseDateInput(draftDate);

    if (name === "" || time === null) {
      setMessage("事项和日期都要填好。");
      return;
    }

    setItems((prev) => sortByDate([...prev, { name, date: time }]));
    setDraftName("");
    setDraftDate("");
    setMessage("");
  }

  function removeItem(index: number) {
    setItems((prev) => prev.filter((_, i) => i !== index));
    // 删掉的行如果正在编辑，编辑态要一起退掉，否则下标指向别的行
    setEditing(null);
  }

  function beginEdit(index: number) {
    const target = ordered[index];
    if (!target) return;
    setEditing(index);
    setEditName(target.name);
    setEditDate(toDateInput(target.date));
  }

  function saveEdit(index: number) {
    const name = normalizeName(editName);
    const time = parseDateInput(editDate);

    if (name === "" || time === null) {
      setMessage("名称和日期都要填好。");
      return;
    }

    setItems((prev) =>
      sortByDate(prev.map((item, i) => (i === index ? { name, date: time } : item))),
    );
    setEditing(null);
    setMessage("");
  }

  return (
    <div id="campus-panel-deadline" role="tabpanel" aria-label="倒计时">
      <div className="campus-card">
        <div className="campus-card-head">
          <h2 className="campus-card-title">还有什么没交</h2>
          <span className="campus-card-note" style={{ margin: 0 }}>
            共 {ordered.length} 项
          </span>
        </div>

        <div className="campus-add-row">
          <input
            className="campus-input"
            type="text"
            value={draftName}
            maxLength={30}
            placeholder="事项，如：期末考试"
            aria-label="事项名称"
            style={{ flex: "1 1 160px" }}
            onChange={(event) => setDraftName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") addItem();
            }}
          />
          <input
            className="campus-input"
            type="date"
            value={draftDate}
            aria-label="截止日期"
            style={{ flex: "0 1 160px" }}
            onChange={(event) => setDraftDate(event.target.value)}
          />
          <button type="button" className="campus-btn is-primary" onClick={addItem}>
            添加
          </button>
        </div>

        {message !== "" && (
          <p className="campus-card-note" role="status" style={{ color: "var(--red-ink)" }}>
            {message}
          </p>
        )}
      </div>

      <div className="campus-card">
        {ordered.length === 0 ? (
          <p className="campus-empty">还没有事项，上面添加一个吧。</p>
        ) : (
          <ul className="campus-dl-list">
            {ordered.map((item, index) => {
              const days = daysLeft(item.date, now);
              const status = statusOf(days);

              if (editing === index) {
                return (
                  <li key={`edit-${index}`} className="campus-dl-item is-editing">
                    <input
                      className="campus-input"
                      type="text"
                      value={editName}
                      maxLength={30}
                      aria-label="编辑事项名称"
                      style={{ flex: "1 1 120px" }}
                      onChange={(event) => setEditName(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") saveEdit(index);
                      }}
                    />
                    <input
                      className="campus-input"
                      type="date"
                      value={editDate}
                      aria-label="编辑截止日期"
                      style={{ flex: "0 1 160px" }}
                      onChange={(event) => setEditDate(event.target.value)}
                    />
                    <button
                      type="button"
                      className="campus-btn is-primary"
                      onClick={() => saveEdit(index)}
                    >
                      保存
                    </button>
                    <button
                      type="button"
                      className="campus-btn"
                      onClick={() => {
                        setEditing(null);
                        setMessage("");
                      }}
                    >
                      取消
                    </button>
                  </li>
                );
              }

              return (
                <li
                  key={`${item.name}-${item.date}-${index}`}
                  className={`campus-dl-item is-${status}`}
                >
                  <span className="campus-dl-name">{item.name}</span>
                  <span className="campus-dl-date">{toDateInput(item.date)}</span>
                  <span className="campus-dl-days">{statusText(days)}</span>
                  <button
                    type="button"
                    className="campus-btn"
                    aria-label={`编辑「${item.name}」`}
                    onClick={() => beginEdit(index)}
                  >
                    改
                  </button>
                  <button
                    type="button"
                    className="campus-btn is-danger"
                    aria-label={`删除「${item.name}」`}
                    onClick={() => removeItem(index)}
                  >
                    删
                  </button>
                </li>
              );
            })}
          </ul>
        )}

        <p className="campus-card-note" style={{ marginTop: 12 }}>
          剩余 7 天内的会标红，过期的显示「已逾期」。数据保存在这台设备上，下次打开还在。
        </p>
      </div>
    </div>
  );
}
