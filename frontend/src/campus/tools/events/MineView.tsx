"use client";

/**
 * 「我的」视图：置顶提醒 + 已处置清单。
 *
 * 已处置的条目从年历表里消失，但必须在这里找得到、撤得回 ——
 * 否则点错一次「不感兴趣」就再也找不回来，用户只能清空数据重来。
 *
 * 置顶支持自定义文案：学生真正需要的往往不是「数学建模」，而是「问问学长数模队还缺不缺人」。
 * 只让置顶原条目名，等于把一个提醒功能做成收藏夹。
 */

import type { EventItem, EventMark, EventsState } from "../../types";
import { CATEGORY_MAP, daysText, groupByMark } from "../../logic/events";

interface MineViewProps {
  items: readonly EventItem[];
  state: EventsState;
  pins: {
    id: string;
    item: EventItem | null;
    label: string;
    next: { date: string; daysLeft: number; precise: boolean; phase: "signup" | "run" } | null;
    marked: boolean;
  }[];
  editingPin: string | null;
  draftPinText: string;
  onBeginEditPin: (id: string) => void;
  onChangePinText: (text: string) => void;
  onSavePin: () => void;
  onRemovePin: (id: string) => void;
  onPickItem: (id: string) => void;
  onUnmark: (id: string) => void;
}

/** 三种处置的动词，和详情页保持一套说法 */
function markVerb(mark: EventMark, item: EventItem): string {
  if (mark === "skip") return "不感兴趣";
  if (item.kind === "contest") return "参加过";
  if (item.kind === "cert") return "已通过";
  return "已上岸";
}

export default function MineView({
  items,
  state,
  pins,
  editingPin,
  draftPinText,
  onBeginEditPin,
  onChangePinText,
  onSavePin,
  onRemovePin,
  onPickItem,
  onUnmark,
}: MineViewProps) {
  const groups = groupByMark(items, state.marks);

  return (
    <div className="campus-ev-mine">
      <section className="campus-ev-mine-block">
        <h3 className="campus-ev-mine-title">
          置顶提醒
          <span className="campus-ev-mine-count">{pins.length} 条</span>
        </h3>

        {pins.length === 0 ? (
          <p className="campus-empty">
            还没有置顶。在任意条目详情里点「置顶提醒」，它就会固定在这里，
            文案还能改成你要做的具体那一步。
          </p>
        ) : (
          <ul className="campus-ev-pins-list">
            {pins.map((pin) => (
              <li key={pin.id} className="campus-ev-pin">
                {editingPin === pin.id ? (
                  <div className="campus-ev-pin-edit">
                    <input
                      className="campus-input"
                      type="text"
                      value={draftPinText}
                      maxLength={30}
                      aria-label="置顶文案"
                      placeholder="例如：问问学长数模队还缺不缺人"
                      onChange={(e) => onChangePinText(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") onSavePin();
                      }}
                    />
                    <button type="button" className="campus-btn is-primary" onClick={onSavePin}>
                      保存
                    </button>
                  </div>
                ) : (
                  <div className="campus-ev-pin-main">
                    <button
                      type="button"
                      className="campus-ev-pin-label"
                      onClick={() => pin.item && onPickItem(pin.item.id)}
                      disabled={!pin.item}
                    >
                      {pin.label}
                      {pin.item && pin.item.short !== pin.label && (
                        <span className="campus-ev-pin-sub">{pin.item.short}</span>
                      )}
                    </button>
                    <span className="campus-ev-pin-when">
                      {pin.next
                        ? `${daysText(pin.next.daysLeft)} · ${pin.next.precise ? "有确切日期" : "约在"} ${
                            pin.next.date.slice(5, 7)
                          } 月`
                        : pin.marked
                          ? "已标记，不再提醒"
                          : pin.item
                            ? "今年不办"
                            : "条目已不在数据里"}
                    </span>
                  </div>
                )}
                <div className="campus-ev-pin-ops">
                  {editingPin === pin.id ? (
                    <button type="button" className="campus-btn" onClick={onSavePin}>
                      完成
                    </button>
                  ) : (
                    <button type="button" className="campus-btn" onClick={() => onBeginEditPin(pin.id)}>
                      改文案
                    </button>
                  )}
                  <button type="button" className="campus-btn" onClick={() => onRemovePin(pin.id)}>
                    取消
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {groups.length === 0 ? (
        <p className="campus-empty" style={{ marginTop: 8 }}>
          还没有标记过的条目。在详情里点「参加过 / 已通过」，它就会移到这里，不再占年历表。
        </p>
      ) : (
        groups.map((group) => (
          <section key={group.mark} className="campus-ev-mine-block">
            <h3 className="campus-ev-mine-title">
              {group.label}
              <span className="campus-ev-mine-count">{group.items.length}</span>
            </h3>
            <ul className="campus-ev-rows">
              {group.items.map((item) => {
                const cat = CATEGORY_MAP[item.category];
                return (
                  <li key={item.id}>
                    <div className="campus-ev-row is-static">
                      <span className="campus-ev-row-mark" style={{ background: cat.color }} aria-hidden="true" />
                      <span className="campus-ev-row-when">{markVerb(group.mark, item)}</span>
                      <span className="campus-ev-row-name">{item.name}</span>
                      <span className="campus-ev-row-cat">{cat.name}</span>
                      <button
                        type="button"
                        className="campus-btn"
                        onClick={() => onUnmark(item.id)}
                        aria-label={`把「${item.name}」放回年历表`}
                      >
                        放回年历表
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          </section>
        ))
      )}

      <p className="campus-ev-foot-note">
        标记只存在这台设备。清浏览器数据会一起清掉，年历表本身的数据不受影响。
      </p>
    </div>
  );
}
