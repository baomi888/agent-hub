"use client";

/**
 * 复习本。
 *
 * 它解决的是「同一轮复习不够用」这件事：原来点「不认识」只是把词塞进**本组**的复习堆，
 * 这一组练完就散了，换个组、换一天再想找这些词已经找不回来。
 * 复习本把「没记住的词」单独攒成一份长期清单，跨组、跨级别、跨天都在。
 *
 * 收词规则见 logic/wordsReview.ts（翻看 ≥5 次、点过「不认识」，任一成立即收），
 * 这里只做呈现与入口，不自己判断该不该收 —— 规则只能有一处。
 */

import { useState } from "react";
import type { ReviewEntry, WordsMemory } from "../../types";
import { MARK_LABEL, REVIEW_FLIP_THRESHOLD, countByMark } from "../../logic/wordsReview";

/** 展开后最多列这么多条，剩下的用一句话交代，避免一屏全是列表 */
const LIST_LIMIT = 15;
const MASTERED_LIMIT = 8;

interface ReviewBookProps {
  mem: WordsMemory;
  /** 级别 id → 中文名，列表里显示「四级核心」而不是 CET4 */
  levelName: (lvl: string) => string;
  onStart: () => void;
  onRemove: (word: string) => void;
  onRestore: (word: string) => void;
  onRemoveMastered: (word: string) => void;
  onClearMastered: () => void;
}

export default function ReviewBook({
  mem,
  levelName,
  onStart,
  onRemove,
  onRestore,
  onRemoveMastered,
  onClearMastered,
}: ReviewBookProps) {
  const [open, setOpen] = useState(false);

  const pending = mem.items;
  const mastered = mem.mastered;
  const byMark = countByMark(mem);

  return (
    <div className="campus-card campus-rb">
      <div className="campus-card-head">
        <h2 className="campus-card-title">复习本</h2>
        <span className="campus-rb-count">跨组 · 跨天</span>
      </div>

      {/*
       * 两个数字做成仪表。
       * 右栏只有 300 宽，原来顶上是一行 12px 灰字「待复习 0 · 已掌握 0」，
       * 整块纸看上去是空的 —— 这里换成衬线大数字，扫一眼就知道攒了多少，
       * 清单反而是要展开才看的东西，不该抢这层重量。
       */}
      <div className="campus-rb-meters">
        <div className="campus-rb-meter">
          <span className="campus-rb-meter-num">{pending.length}</span>
          <span className="campus-rb-meter-label">待复习</span>
        </div>
        <div className="campus-rb-meter">
          <span className="campus-rb-meter-num">{mastered.length}</span>
          <span className="campus-rb-meter-label">已掌握</span>
        </div>
      </div>

      {/*
       * 原来这里是一段三句话的说明（「两个条件各自独立：翻看满 5 次记…，点过…记…，
       * 任一成立就收进来。复习时点…，换组、换天都还在。」）。
       * 手机上它跟下面的清单长得一样，读起来就是一团字。
       * 改成图例：徽章跟清单里用的是同一套，扫一眼就把「哪种标记 = 什么条件」对上。
       */}
      <div className="campus-rb-legend">
        <span className="campus-rb-legend-item">
          <span className="campus-rb-mark is-unsure">可能不熟悉</span>
          翻看满 {REVIEW_FLIP_THRESHOLD} 次
        </span>
        <span className="campus-rb-legend-item">
          <span className="campus-rb-mark is-sure">确定不熟悉</span>
          点过「不认识」
        </span>
      </div>

      {/*
       * 空本子不放按钮。
       * 原来这里是一颗通栏的灰色禁用按钮「还没有要复习的词」—— 占着面板最重的位置，
       * 却是一块点不动的死控件，看起来像坏了。没词可复的时候只需要一句话说清规则，
       * 等真的有词进本，那颗金色「开始复习 N 个词」才出现。
       * 「看清单」只要有任何一条记录就留着：已掌握的词也该能翻回去看。
       */}
      {pending.length > 0 || mastered.length > 0 ? (
        <div className="campus-rb-actions">
          {pending.length > 0 ? (
            <button type="button" className="campus-words-btn is-known" onClick={onStart}>
              开始复习 {pending.length} 个词
            </button>
          ) : null}
          <button
            type="button"
            className="campus-rb-toggle"
            onClick={() => setOpen((prev) => !prev)}
            aria-expanded={open}
          >
            {open ? "收起清单" : "看清单"}
          </button>
        </div>
      ) : (
        <p className="campus-rb-hint">
          还没有词进本。反复翻看，或点过「不认识」，够条件的词会自己进来。
        </p>
      )}

      {open ? (
        <div className="campus-rb-body">
          <section>
            <div className="campus-rb-title-row">
              <h3 className="campus-rb-title">待复习</h3>
              {pending.length > 0 ? (
                <span className="campus-rb-split">
                  确定 {byMark.sure} · 可能 {byMark.unsure}
                </span>
              ) : null}
            </div>
            {pending.length === 0 ? (
              <p className="campus-card-note">
                还没有词进本。反复翻看，或点过「不认识」，够条件的词会自己进来。
              </p>
            ) : (
              <ul className="campus-rb-list">
                {pending.slice(0, LIST_LIMIT).map((item) => (
                  <li key={item.w} className="campus-rb-row">
                    <RowText entry={item} levelName={levelName} />
                    <button
                      type="button"
                      className="campus-words-mini"
                      onClick={() => onRemove(item.w)}
                      aria-label={`从复习本移除 ${item.w}`}
                    >
                      移除
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {pending.length > LIST_LIMIT ? (
              <p className="campus-card-note">
                还有 {pending.length - LIST_LIMIT} 个，先复习完这一批。
              </p>
            ) : null}
          </section>

          <section>
            <div className="campus-rb-title-row">
              <h3 className="campus-rb-title">已掌握</h3>
              {mastered.length > 0 ? (
                <button type="button" className="campus-words-mini" onClick={onClearMastered}>
                  清空
                </button>
              ) : null}
            </div>
            {mastered.length === 0 ? (
              <p className="campus-card-note">复习时点「认识」的词会挪到这里，可以回看。</p>
            ) : (
              <ul className="campus-rb-list">
                {mastered.slice(0, MASTERED_LIMIT).map((item) => (
                  <li key={item.w} className="campus-rb-row is-mastered">
                    <RowText entry={item} levelName={levelName} />
                    <span className="campus-rb-row-actions">
                      <button
                        type="button"
                        className="campus-words-mini"
                        onClick={() => onRestore(item.w)}
                        aria-label={`把 ${item.w} 移回待复习`}
                      >
                        再练
                      </button>
                      <button
                        type="button"
                        className="campus-words-mini"
                        onClick={() => onRemoveMastered(item.w)}
                        aria-label={`从已掌握移除 ${item.w}`}
                      >
                        移除
                      </button>
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {mastered.length > MASTERED_LIMIT ? (
              <p className="campus-card-note">还有 {mastered.length - MASTERED_LIMIT} 个已掌握的词。</p>
            ) : null}
          </section>
        </div>
      ) : null}
    </div>
  );
}

function RowText({
  entry,
  levelName,
}: {
  entry: ReviewEntry;
  levelName: (lvl: string) => string;
}) {
  return (
    <span className="campus-rb-row-text">
      <span className="campus-rb-word">
        {entry.w}
        <span className={`campus-rb-mark is-${entry.mark}`}>{MARK_LABEL[entry.mark]}</span>
      </span>
      <span className="campus-rb-def">{entry.def}</span>
      <span className="campus-rb-meta">
        {levelName(entry.lvl)} · 翻了 {entry.flips} 次
      </span>
    </span>
  );
}
