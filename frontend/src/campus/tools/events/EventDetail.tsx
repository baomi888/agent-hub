"use client";

/**
 * 条目详情弹层。
 *
 * 一条赛事要说清五件事，学生才敢报：这是什么、什么时候、适不适合我、要准备什么、去哪看原文。
 * 前四条在数据里，第五条是官网链接 —— 没有官网的条目不摆假链接，改说「以本校通知为准」。
 *
 * 弹层用板块已有的 `.campus-modal` 三段式（遮罩 / 卡 / 头），不新造样式；
 * 滚动锁走 `lockPageScroll()`，因为页面真正的滚动容器是 `.campus-scroll` 而不是 body。
 */

import { useEffect } from "react";
import type { EventMark, EventItem } from "../../types";
import { lockPageScroll } from "../../logic/scrollLock";
import { CATEGORY_MAP, cycleText, fitsGrade, isAllYear } from "../../logic/events";
import { syncedDay, type SyncedNotice } from "./notices";

interface EventDetailProps {
  item: EventItem;
  grade: number | null;
  mark?: EventMark;
  /** 是否已置顶 */
  pinned: boolean;
  /** 官网最新公告（构建前抓的），没有就是空数组 */
  notices?: SyncedNotice[];
  /** 公告抓取时间，用来标「同步于 X」 */
  syncedAt?: string | null;
  onClose: () => void;
  onMark: (mark: EventMark | null) => void;
  onTogglePin: () => void;
}

/** 窗口文案：`9 月` / `9–11 月` / `10 月 – 次年 3 月` */
function windowText(win: { from: number; to: number; note?: string }): string {
  if (win.from === 1 && win.to === 12) return "全年";
  if (win.from <= win.to) {
    return win.from === win.to ? `${win.from} 月` : `${win.from}–${win.to} 月`;
  }
  return `${win.from} 月 – 次年 ${win.to} 月`;
}

/** 「已处置」按钮的动词：赛事是参加过，证书是通过，升学考试是上岸 */
function doneLabel(item: EventItem): string {
  if (item.kind === "contest") return "参加过";
  if (item.kind === "cert") return "已通过";
  return "已上岸";
}

export default function EventDetail({
  item,
  grade,
  mark,
  pinned,
  notices,
  syncedAt,
  onClose,
  onMark,
  onTogglePin,
}: EventDetailProps) {
  useEffect(() => {
    const release = lockPageScroll();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      release();
    };
  }, [onClose]);

  const cat = CATEGORY_MAP[item.category];
  const fit = fitsGrade(item, grade);

  return (
    <div className="campus-modal" role="dialog" aria-modal="true" aria-labelledby="campus-ev-detail-title">
      <button type="button" className="campus-modal-mask" aria-label="关闭详情" onClick={onClose} />
      <div className="campus-modal-card campus-ev-detail">
        <div className="campus-modal-head">
          <div>
            <p className="campus-ev-detail-cat">
              <span className="campus-ev-dot" style={{ background: cat.color }} aria-hidden="true" />
              {cat.name}
              <span className="campus-ev-detail-kind">
                {item.kind === "contest" ? "赛事" : item.kind === "cert" ? "证书考试" : "升学就业"}
              </span>
            </p>
            <h2 id="campus-ev-detail-title" className="campus-ev-detail-title">
              {item.name}
            </h2>
          </div>
          <button type="button" className="campus-modal-close" aria-label="关闭" onClick={onClose}>
            ×
          </button>
        </div>

        <div className="campus-ev-detail-body">
          <dl className="campus-ev-facts">
            <div>
              <dt>时间</dt>
              <dd>
                {item.signup && item.signup.length > 0 && (
                  <span className="campus-ev-fact-row">
                    报名 {item.signup.map((w) => windowText(w)).join(" / ")}
                  </span>
                )}
                <span className="campus-ev-fact-row">
                  {item.kind === "contest" ? "比赛" : "考试"}{" "}
                  {item.runs.map((w) => windowText(w)).join(" / ")}
                </span>
                {item.verified && (
                  <span className="campus-ev-fact-note">
                    {item.verified.year} 年实际：{item.verified.text}
                  </span>
                )}
              </dd>
            </div>
            <div>
              <dt>频次</dt>
              <dd>{cycleText(item.cycle)}</dd>
            </div>
            {isAllYear(item) && (
              <div>
                <dt>备注</dt>
                <dd>全年可报，具体批次看考点安排</dd>
              </div>
            )}
            {item.grades && item.grades.length > 0 && (
              <div>
                <dt>适合年级</dt>
                <dd>{item.grades.map((g) => ["大一", "大二", "大三", "大四"][g - 1]).join(" / ")}</dd>
              </div>
            )}
            {item.tags && item.tags.length > 0 && (
              <div>
                <dt>标签</dt>
                <dd className="campus-ev-tags">
                  {item.tags.map((t) => (
                    <span key={t} className="campus-chip is-static">
                      {t}
                    </span>
                  ))}
                </dd>
              </div>
            )}
          </dl>

          <section className="campus-ev-section">
            <h3>这是什么</h3>
            <p>{item.intro}</p>
          </section>

          <section className="campus-ev-section">
            <h3>适合谁</h3>
            <p>
              {item.fitFor}
              {fit && grade !== null && (
                <span className="campus-ev-fit"> · 适合你现在这个年级</span>
              )}
            </p>
          </section>

          {item.require && (
            <section className="campus-ev-section">
              <h3>要准备什么</h3>
              <p>{item.require}</p>
            </section>
          )}

          {item.gdNote && (
            <section className="campus-ev-section">
              <h3>广东情况</h3>
              <p>{item.gdNote}</p>
            </section>
          )}

          <section className="campus-ev-section">
            <h3>去哪看原文</h3>
            {item.officialUrl ? (
              <p>
                <a
                  className="campus-ev-link"
                  href={item.officialUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  官网 {new URL(item.officialUrl).host}
                  <span aria-hidden="true"> ↗</span>
                </a>
                <br />
                <span className="campus-ev-fact-note">
                  官网给的是全国赛信息。院赛、校赛时间以本校教务处/团委通知为准。
                </span>
              </p>
            ) : (
              <p className="campus-ev-fact-note">
                这一类赛事没有统一的长期官网，通知一般由本校教务处/团委或承办学院发布。
                请以学校通知为准，不要按本页时间直接订票。
              </p>
            )}

            {notices && notices.length > 0 && (
              <div className="campus-ev-notices">
                <p className="campus-ev-notices-head">
                  官网最新公告
                  {syncedDay(syncedAt ?? null) && (
                    <span className="campus-ev-notices-sync">
                      自动同步 · {syncedDay(syncedAt ?? null)}
                    </span>
                  )}
                </p>
                <ul className="campus-ev-notices-list">
                  {notices.map((notice) => (
                    <li key={notice.url}>
                      <a href={notice.url} target="_blank" rel="noopener noreferrer">
                        {notice.title}
                        <span aria-hidden="true"> ↗</span>
                      </a>
                    </li>
                  ))}
                </ul>
                <p className="campus-ev-fact-note">
                  标题和链接自动取自从官网首页，日期未做自动解析 —— 请看原文确认。
                </p>
              </div>
            )}
          </section>

          <p className="campus-ev-disclaimer">
            本页时间来自往年规律与官方公告，不是官方承诺。每校院赛、校赛时间另定。
          </p>
        </div>

        <div className="campus-ev-detail-actions">
          <button
            type="button"
            className="campus-btn"
            aria-pressed={pinned}
            onClick={onTogglePin}
          >
            {pinned ? "取消置顶" : "置顶提醒"}
          </button>
          <button
            type="button"
            className="campus-btn is-primary"
            aria-pressed={mark === "attended" || mark === "passed"}
            onClick={() => onMark(mark === "attended" || mark === "passed" ? null : item.kind === "contest" ? "attended" : "passed")}
          >
            {doneLabel(item)}
          </button>
          <button
            type="button"
            className="campus-btn"
            aria-pressed={mark === "skip"}
            onClick={() => onMark(mark === "skip" ? null : "skip")}
          >
            不感兴趣
          </button>
        </div>
      </div>
    </div>
  );
}

/** 窗口文案对外复用，避免各视图各写一套 */
export { windowText };
