"use client";

/**
 * 长期任务库。
 *
 * 交互按「一眼看懂 + 一键开始」设计：
 *  - 主列表只放未归档的任务，点一下就选中，选中即可开始计时（不必先编辑再开始）；
 *  - 新建时大类默认「自动识别」，用户不用理解归类规则也能建任务；
 *  - 归档任务折叠在底部，想恢复再点，日常不占视线。
 *
 * 改名用行内编辑而不是弹窗：改一个名字弹一层遮罩，视线要跳两次、手指要够两次，
 * 而列表本来就长在这里，就地换行输入是最短路径。
 */

import { useState } from "react";
import type { FocusTask } from "../../types";
import { MAX_TASK_NAME, archivedTasks } from "../../logic/focusTask";
import { DEFAULT_CATEGORY, listCategories } from "../../logic/focusCategory";

interface TaskPickerProps {
  tasks: readonly FocusTask[];
  activeId: string | null;
  /** 计时中不允许换任务，避免把一段时间记到两个任务名下 */
  locked: boolean;
  onSelect: (id: string) => void;
  onCreate: (name: string, category: string) => void;
  /** 改名：成功返回 null，失败返回给用户看的原因（重名、空名等由逻辑层判定） */
  onRename: (id: string, name: string) => string | null;
  onArchive: (id: string) => void;
  onRestore: (id: string) => void;
}

export default function TaskPicker({
  tasks,
  activeId,
  locked,
  onSelect,
  onCreate,
  onRename,
  onArchive,
  onRestore,
}: TaskPickerProps) {
  const [name, setName] = useState("");
  const [category, setCategory] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  /** 正在改名的任务 id；同一时刻只允许一行处于编辑态 */
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);

  const history = archivedTasks(tasks);
  const working = tasks.filter((item) => !item.archived);

  function handleCreate() {
    if (name.trim() === "" || locked) return;
    onCreate(name, category);
    setName("");
    // 大类故意不重置：连建几个同类任务时不用每次重选
  }

  function startEdit(item: FocusTask) {
    setEditingId(item.id);
    setDraft(item.name);
    setError(null);
  }

  function cancelEdit() {
    setEditingId(null);
    setDraft("");
    setError(null);
  }

  function commitEdit() {
    if (editingId === null || locked) return;
    const message = onRename(editingId, draft);
    if (message !== null) {
      setError(message);
      return;
    }
    cancelEdit();
  }

  return (
    <div className="campus-task-panel">
      <div className="campus-task-new">
        <label className="campus-field campus-task-new-name">
          <span>新建任务</span>
          <input
            className="campus-input"
            type="text"
            value={name}
            maxLength={MAX_TASK_NAME}
            placeholder={locked ? "计时中，结束后再新建" : "如：高数第三章习题"}
            disabled={locked}
            onChange={(event) => setName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") handleCreate();
            }}
          />
        </label>
        <label className="campus-field campus-task-new-cat">
          <span>大类</span>
          <select
            className="campus-input"
            value={category}
            disabled={locked}
            onChange={(event) => setCategory(event.target.value)}
          >
            <option value="">自动识别</option>
            {listCategories().map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="campus-btn is-primary campus-task-add"
          onClick={handleCreate}
          disabled={locked || name.trim() === ""}
          title={locked ? "计时中不能新建任务" : "添加到任务库"}
        >
          添加
        </button>
      </div>

      {working.length === 0 ? (
        <p className="campus-card-note" style={{ margin: "12px 0 0" }}>
          还没有任务。先建一个，之后每天点「开始」就能计时，不用再填名字。
        </p>
      ) : (
        <ul className="campus-task-list">
          {working.map((item) => {
            const active = item.id === activeId;

            if (editingId === item.id) {
              return (
                <li key={item.id} className="campus-task-item is-editing">
                  <div className="campus-task-edit">
                    <input
                      className="campus-input campus-task-edit-input"
                      type="text"
                      value={draft}
                      maxLength={MAX_TASK_NAME}
                      autoFocus
                      aria-label={`重命名任务 ${item.name}`}
                      aria-invalid={error !== null}
                      onChange={(event) => {
                        setDraft(event.target.value);
                        setError(null);
                      }}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") commitEdit();
                        if (event.key === "Escape") cancelEdit();
                      }}
                    />
                    <button
                      type="button"
                      className="campus-btn is-primary campus-task-save"
                      onClick={commitEdit}
                      disabled={locked || draft.trim() === ""}
                    >
                      保存
                    </button>
                    <button type="button" className="campus-task-act" onClick={cancelEdit}>
                      取消
                    </button>
                  </div>
                  {error !== null && <p className="campus-task-err">{error}</p>}
                </li>
              );
            }

            return (
              <li key={item.id} className={`campus-task-item${active ? " is-active" : ""}`}>
                <button
                  type="button"
                  className="campus-task-pick"
                  onClick={() => onSelect(item.id)}
                  disabled={locked}
                  aria-pressed={active}
                  title={locked ? "计时中不能切换任务" : "选它，然后点开始"}
                >
                  <span className="campus-task-dot" style={{ background: item.color }} aria-hidden="true" />
                  <span className="campus-task-name">{item.name}</span>
                  <span className="campus-task-cat">{item.category}</span>
                </button>
                <button
                  type="button"
                  className="campus-task-act"
                  onClick={() => startEdit(item)}
                  disabled={locked}
                  title="改名字（大类会跟着重新识别）"
                  aria-label={`重命名任务 ${item.name}`}
                >
                  改名
                </button>
                <button
                  type="button"
                  className="campus-task-act"
                  onClick={() => onArchive(item.id)}
                  disabled={locked}
                  title="归档（历史记录会保留）"
                  aria-label={`归档任务 ${item.name}`}
                >
                  归档
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {history.length > 0 && (
        <div className="campus-task-archived">
          <button
            type="button"
            className="campus-task-toggle"
            onClick={() => setShowArchived((prev) => !prev)}
            aria-expanded={showArchived}
          >
            {showArchived ? "收起已归档" : `已归档 ${history.length} 个`}
          </button>
          {showArchived && (
            <ul className="campus-task-list">
              {history.map((item) => (
                <li key={item.id} className="campus-task-item is-archived">
                  <span className="campus-task-name">{item.name}</span>
                  <span className="campus-task-cat">{item.category || DEFAULT_CATEGORY}</span>
                  <button
                    type="button"
                    className="campus-task-act"
                    onClick={() => onRestore(item.id)}
                    disabled={locked}
                  >
                    恢复
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
