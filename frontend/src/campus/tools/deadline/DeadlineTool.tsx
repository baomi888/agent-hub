"use client";

/**
 * Deadline 倒计时。
 *
 * 保留旧版的行为：剩余 ≤7 天标红、过期显示「已逾期 N 天」、临近的排前面、行内可反复编辑。
 * 允许编辑这件事是用户明确提过的（「编辑完还能再编辑」），所以没有做成一次性表单。
 *
 * ## 2026-11-10（Phase 3）：登录后改用 `tasks`，不再自己存一份
 *
 * 同一个 DDL 原先在三个地方各存一份（本机 localStorage、云端快照、
 * 提醒中心的 tasks），改一处另外两处不知道。现在登录后**只有一个真相**：
 * `tasks.due_at`，倒计时只是它的视图（用户原话：「倒计时不是独立对象」）。
 *
 * 这一页的承诺是「不登录也能用」（`/tools` 的 metadata 和页头都这么写），
 * 所以保留本地模式，不做成"必须登录"：
 *
 *   · 未登录 → `LocalDeadline`：原样读写 localStorage，行为与旧版逐字一致；
 *   · 已登录 → `CloudDeadline`：读写 `/api/tasks`，首次进入把本机旧数据逐条迁上去。
 *
 * ★ 两个模式渲染的是**同一个** `DeadlinePanel`，差别只在哪个模式多了「完成」——
 *   localStorage 没有完成态，硬加一个只存在这台设备上的"完成"会造成
 *   「工具集里完成了、提醒中心还在倒数」，那正是这一轮要消灭的问题。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import {
  dueAtFromDate,
  migrateLocalDeadlines,
  toDeadlineRows,
  type DeadlineRow,
} from "../../logic/deadlineCloud";
import { readJson, readWithMigration, writeJson } from "../../storage";
import { CAMPUS_KEYS, LEGACY_KEYS } from "../../storage/keys";
import type { DeadlineItem } from "../../types";
import { useAuth } from "@/lib/hooks/useAuth";
import { api } from "@/lib/api";

/** 一分钟重算一次剩余天数：够用，也不会让页面一直重渲染 */
const CLOCK_MS = 60_000;

/** 迁移标记按账号分：换个人登录不该继承上一个人的「已迁过」 */
function migratedKey(uid: string): string {
  return `${CAMPUS_KEYS.deadline}.migrated.${uid}`;
}

/**
 * 整页的两种数据来源，收成一个接口给 `DeadlinePanel` 用。
 * 不传 `complete` 就是本地模式（不显示「完成」）。
 */
interface DeadlineApi {
  add: (name: string, date: number) => Promise<void>;
  rename: (row: DeadlineRow, name: string, date: number) => Promise<void>;
  remove: (row: DeadlineRow) => Promise<void>;
  complete?: (row: DeadlineRow) => Promise<void>;
}

export default function DeadlineTool() {
  const { me, loading } = useAuth();

  /*
   * 首屏不猜：`loading` 期间渲染占位而不是先画一帧本地数据。
   * 先画本地再跳云端，用户会看到列表"闪一下变了"，比空白更难解释。
   */
  if (loading) {
    return (
      <div id="campus-panel-deadline" role="tabpanel" aria-label="倒计时">
        <div className="campus-card">
          <p className="campus-empty">正在读取…</p>
        </div>
      </div>
    );
  }

  return me ? <CloudDeadline uid={me.user.id} /> : <LocalDeadline />;
}

// ==================== 共用渲染 ====================

function DeadlinePanel({
  rows,
  now,
  mode,
  busy,
  message,
  api: actions,
  onClearMessage,
}: {
  rows: DeadlineRow[];
  now: number;
  mode: "local" | "cloud";
  busy: boolean;
  message: string;
  api: DeadlineApi;
  onClearMessage: () => void;
}) {
  const [draftName, setDraftName] = useState("");
  const [draftDate, setDraftDate] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editDate, setEditDate] = useState("");
  const [localMsg, setLocalMsg] = useState("");

  const shown = message || localMsg;

  function add() {
    const name = normalizeName(draftName);
    const time = parseDateInput(draftDate);
    if (name === "" || time === null) {
      setLocalMsg("事项和日期都要填好。");
      return;
    }
    setLocalMsg("");
    void actions.add(name, time);
    setDraftName("");
    setDraftDate("");
  }

  function beginEdit(row: DeadlineRow) {
    if (busy) return;
    setEditing(row.id);
    setEditName(row.name);
    setEditDate(toDateInput(row.date));
    setLocalMsg("");
  }

  function saveEdit(row: DeadlineRow) {
    const name = normalizeName(editName);
    const time = parseDateInput(editDate);
    if (name === "" || time === null) {
      setLocalMsg("名称和日期都要填好。");
      return;
    }
    void actions.rename(row, name, time);
    setEditing(null);
    setLocalMsg("");
  }

  function haltEdit() {
    setEditing(null);
    setLocalMsg("");
  }

  return (
    <div id="campus-panel-deadline" role="tabpanel" aria-label="倒计时">
      <div className="campus-card">
        <div className="campus-card-head">
          <h2 className="campus-card-title">还有什么没交</h2>
          <span className="campus-card-note" style={{ margin: 0 }}>
            共 {rows.length} 项
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
              if (event.key === "Enter") add();
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
          <button
            type="button"
            className="campus-btn is-primary"
            disabled={busy}
            onClick={add}
          >
            添加
          </button>
        </div>

        {shown !== "" && (
          <p className="campus-card-note" role="status" style={{ color: "var(--red-ink)" }}>
            {shown}
            <button
              type="button"
              className="campus-btn is-ghost"
              style={{ marginLeft: 8 }}
              onClick={() => {
                setLocalMsg("");
                onClearMessage();
              }}
            >
              知道了
            </button>
          </p>
        )}
      </div>

      <div className="campus-card">
        {rows.length === 0 ? (
          <p className="campus-empty">还没有事项，上面添加一个吧。</p>
        ) : (
          <ul className="campus-dl-list">
            {rows.map((row) => {
              const days = daysLeft(row.date, now);
              const status = statusOf(days);

              if (editing === row.id) {
                return (
                  <li key={row.id} className="campus-dl-item is-editing">
                    <input
                      className="campus-input"
                      type="text"
                      value={editName}
                      maxLength={30}
                      aria-label="编辑事项名称"
                      style={{ flex: "1 1 120px" }}
                      onChange={(event) => setEditName(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") saveEdit(row);
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
                      disabled={busy}
                      onClick={() => saveEdit(row)}
                    >
                      保存
                    </button>
                    <button type="button" className="campus-btn" onClick={haltEdit}>
                      取消
                    </button>
                    {/* 删除只在编辑态出现：倒计时的主动作是「我交了」（完成），
                        删除是危险且低频的，不该和它在同一排抢注意力 */}
                    <button
                      type="button"
                      className="campus-btn is-danger"
                      disabled={busy}
                      aria-label={`删除「${row.name}」`}
                      onClick={() => {
                        void actions.remove(row);
                        haltEdit();
                      }}
                    >
                      删
                    </button>
                  </li>
                );
              }

              return (
                <li key={row.id} className={`campus-dl-item is-${status}`}>
                  <span className="campus-dl-name">{row.name}</span>
                  <span className="campus-dl-date">{toDateInput(row.date)}</span>
                  <span className="campus-dl-days">{statusText(days)}</span>
                  <button
                    type="button"
                    className="campus-btn"
                    aria-label={`编辑「${row.name}」`}
                    onClick={() => beginEdit(row)}
                  >
                    改
                  </button>
                  {actions.complete ? (
                    <button
                      type="button"
                      className="campus-btn"
                      disabled={busy}
                      aria-label={`标记「${row.name}」已完成`}
                      onClick={() => void actions.complete?.(row)}
                    >
                      交了
                    </button>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}

        <p className="campus-card-note" style={{ marginTop: 12 }}>
          {mode === "cloud" ? (
            <>
              已登录：这里的每一项就是提醒中心里的一条待办（清单「收件箱」，
              截止时间是当天 23:59）。在任一处改了，另一边立刻同步；点「交了」
              两边都不再倒数。
            </>
          ) : (
            <>剩余 7 天内的会标红，过期的显示「已逾期」。数据保存在这台设备上，下次打开还在。</>
          )}
        </p>
      </div>
    </div>
  );
}

// ==================== 本地模式（未登录） ====================

function LocalDeadline() {
  const [items, setItems] = useState<DeadlineItem[]>([]);
  const [ready, setReady] = useState(false);
  const [now, setNow] = useState(() => Date.now());

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

  // items 本身保持有序，这里再排一次只是防御脏数据。
  // 本地没有 id，用「名称|日期」当 key —— 旧版用下标，删中间一行会让编辑态错位。
  const rows = useMemo<DeadlineRow[]>(
    () =>
      sortByDate(items).map((item, i) => ({
        id: `local-${i}-${item.date}`,
        name: item.name,
        date: item.date,
      })),
    [items],
  );

  const actions: DeadlineApi = {
    add: async (name, date) => {
      setItems((prev) => sortByDate([...prev, { name, date }]));
    },
    rename: async (row, name, date) => {
      setItems((prev) =>
        sortByDate(
          prev.map((item) =>
            item.name === row.name && item.date === row.date ? { name, date } : item,
          ),
        ),
      );
    },
    remove: async (row) => {
      setItems((prev) =>
        prev.filter((item) => !(item.name === row.name && item.date === row.date)),
      );
    },
  };

  return (
    <DeadlinePanel
      rows={rows}
      now={now}
      mode="local"
      busy={false}
      message=""
      onClearMessage={() => undefined}
      api={actions}
    />
  );
}

// ==================== 云端模式（已登录） ====================

/**
 * 迁移只在本会话里试一次。
 *
 * StrictMode 下 effect 会跑两遍，而"迁移成功"的判断依赖网络往返 ——
 * 靠读标记挡不住两个请求同时在飞。模块级开关是这里唯一可靠的闸。
 * （即使两道闸都漏了，逐条 POST 的语义也保证不会重复：成功一条本地就少一条。）
 */
let migrating = false;

function CloudDeadline({ uid }: { uid: string }) {
  const [rows, setRows] = useState<DeadlineRow[]>([]);
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [ready, setReady] = useState(false);
  const aliveRef = useRef(true);

  const reload = useCallback(async () => {
    const res = await api.tasks.list("all", 200);
    if (!aliveRef.current) return;
    setRows(toDeadlineRows(res?.items ?? []));
  }, []);

  useEffect(() => {
    aliveRef.current = true;
    void (async () => {
      // ① 本机旧数据一次性迁进 tasks
      if (!migrating && readJson(migratedKey(uid)) === null) {
        migrating = true;
        const local = readWithMigration<DeadlineItem[]>(
          CAMPUS_KEYS.deadline,
          [LEGACY_KEYS.deadline],
          migrateLegacyDeadline,
        );
        if (local && local.length > 0) {
          const result = await migrateLocalDeadlines(local, (item) =>
            api.tasks.create({
              title: item.name,
              due_at: dueAtFromDate(item.date),
              list_key: "inbox",
            }),
          );
          if (result.error === null) {
            // 全部迁完：本地那份要清掉，否则退出登录会看到一份早就过时的副本。
            // 这里**不加 silent** —— 让云端快照跟着清空，换设备才不会再拉回旧数据。
            writeJson(CAMPUS_KEYS.deadline, []);
            writeJson(migratedKey(uid), { at: Date.now(), moved: result.moved }, { silent: true });
          } else {
            // 部分成功：剩下的写回本地，下次进页面继续。已迁走的不再重复。
            writeJson(CAMPUS_KEYS.deadline, result.left);
            if (aliveRef.current) {
              setMessage(
                `有 ${result.left.length} 项还没同步过来（${result.error}），下次进来自动重试。`,
              );
            }
          }
        } else {
          writeJson(migratedKey(uid), { at: Date.now(), moved: 0 }, { silent: true });
        }
        migrating = false;
      }

      // ② 拉列表
      await reload();
      if (aliveRef.current) setReady(true);
    })();

    const timer = window.setInterval(() => setNow(Date.now()), CLOCK_MS);
    return () => {
      aliveRef.current = false;
      window.clearInterval(timer);
    };
  }, [uid, reload]);

  /** 所有写操作共用的壳：置忙 → 干活 → 重新拉 → 出错给一句人话 */
  const run = useCallback(
    async (job: () => Promise<unknown>, failText: string) => {
      setBusy(true);
      setMessage("");
      try {
        await job();
        await reload();
      } catch (e) {
        setMessage(e instanceof Error ? `${failText}：${e.message}` : failText);
      } finally {
        setBusy(false);
      }
    },
    [reload],
  );

  const actions: DeadlineApi = {
    add: (name, date) =>
      run(
        () =>
          api.tasks.create({
            title: name,
            due_at: dueAtFromDate(date),
            list_key: "inbox",
          }),
        "添加失败",
      ),
    rename: (row, name, date) =>
      run(
        () => api.tasks.patch(row.id, { title: name, due_at: dueAtFromDate(date) }),
        "改不动",
      ),
    remove: (row) => run(() => api.tasks.remove(row.id), "删不掉"),
    complete: (row) => run(() => api.tasks.complete(row.id), "标不了完成"),
  };

  if (!ready) {
    return (
      <div id="campus-panel-deadline" role="tabpanel" aria-label="倒计时">
        <div className="campus-card">
          <p className="campus-empty">正在读取…</p>
        </div>
      </div>
    );
  }

  return (
    <DeadlinePanel
      rows={rows}
      now={now}
      mode="cloud"
      busy={busy}
      message={message}
      onClearMessage={() => setMessage("")}
      api={actions}
    />
  );
}
