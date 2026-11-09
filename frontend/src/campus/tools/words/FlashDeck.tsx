"use client";

/**
 * 全屏刷词层（沉浸式）。
 *
 * 为什么单独一个组件：全屏态要处理「锁滚动、接管键盘、退出时收尾」三件与面板无关的事，
 * 塞回 WordsTool 会让那个组件又变回什么都干。
 *
 * 这一层是**受控**的：词、下标、是否翻面、是否结束全由父层给，自己只负责呈现和收键盘。
 * 父层有两种来源（本组练习 / 复习本），共用一个层，避免两套几乎一样的界面各写一遍。
 */

import { useCallback, useEffect } from "react";
import type { WordItem } from "../../types";
import { lockPageScroll } from "../../logic/scrollLock";
import { speakWord, stopSpeaking } from "../../logic/speech";
import { useSpeechSupport } from "../../logic/useSpeech";

interface FlashDeckProps {
  open: boolean;
  /** 顶栏左侧的身份：第 10 组 / 复习本 */
  eyebrow: string;
  /** 顶栏右侧进度文案，如「3 / 20」 */
  counter: string;
  /** 顶部进度百分比（0-100） */
  percent: number;
  word?: WordItem;
  flipped: boolean;
  done: boolean;
  /** 完成态的标题与说明 */
  doneTitle: string;
  doneText: string;
  /** 底部一行小字：说明这次判定的后果（复习本模式才有） */
  note?: string;
  onFlip: () => void;
  onJudge: (know: boolean) => void;
  onClose: () => void;
  /** 完成态的主按钮：重新开始这一组 / 复习剩下的 */
  onRestart?: () => void;
  restartText?: string;
  /** 关闭后是否要把焦点还给打开它的按钮（无障碍） */
  returnFocusTo?: string;
}

export default function FlashDeck({
  open,
  eyebrow,
  counter,
  percent,
  word,
  flipped,
  done,
  doneTitle,
  doneText,
  note,
  onFlip,
  onJudge,
  onClose,
  onRestart,
  restartText = "再来一次",
  returnFocusTo,
}: FlashDeckProps) {
  // 朗读走浏览器自带语音：不联网、不需要接口，隐私模式下多半没有，所以先探测再决定要不要显示按钮
  const ttsReady = useSpeechSupport();

  // 退出/换词时把上一句掐掉，否则会念着上一个单词
  const wordKey = word?.[0] ?? "";
  useEffect(() => {
    if (!open) return;
    return () => stopSpeaking();
  }, [open, wordKey]);

  const speak = useCallback(() => {
    if (!ttsReady || !word) return;
    speakWord(word[0]);
  }, [ttsReady, word]);

  // 打开时锁住背后页面的滚动（板块页的滚动在 .campus-scroll 上，见 logic/scrollLock）
  useEffect(() => {
    if (!open) return;
    return lockPageScroll();
  }, [open]);

  // 键盘：空格翻面/继续，←→ 判定，Esc 退出
  useEffect(() => {
    if (!open) return;

    function onKeyDown(event: KeyboardEvent) {
      const tag = (event.target as HTMLElement | null)?.tagName ?? "";
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;

      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }

      if (done) {
        if (event.key === " " || event.key === "Enter") {
          event.preventDefault();
          if (onRestart) onRestart();
          else onClose();
        }
        return;
      }

      if (event.key === " " || event.key === "Enter") {
        event.preventDefault();
        if (flipped) onJudge(true);
        else onFlip();
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        onJudge(true);
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        onJudge(false);
      }
    }

    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, done, flipped, onClose, onFlip, onJudge, onRestart]);

  // 退出后把焦点还回去：键盘用户不会因为全屏关掉而失去落点
  useEffect(() => {
    if (open || !returnFocusTo) return;
    const el = document.querySelector<HTMLButtonElement>(returnFocusTo);
    el?.focus();
  }, [open, returnFocusTo]);

  if (!open) return null;

  return (
    <div className="campus-deck" role="dialog" aria-modal="true" aria-label={`全屏刷词 · ${eyebrow}`}>
      <div className="campus-deck-bar">
        <button type="button" className="campus-deck-exit" onClick={onClose} aria-label="退出全屏刷词">
          ✕
        </button>
        <span className="campus-deck-eyebrow">{eyebrow}</span>
        <span className="campus-deck-counter">{counter}</span>
      </div>

      <div
        className="campus-deck-progress"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-label="本次刷词进度"
      >
        <div className="campus-deck-progress-fill" style={{ width: `${percent}%` }} />
      </div>

      {/*
       * 判定按钮放进卡片里，卡片才是完整的一张「牌」。
       * 原来按钮浮在卡外的底栏上：手机上一张只有单词的纸占掉 560 高，
       * 按钮另起一行贴着屏幕边缘，纸面大半是空的；按钮进卡以后，
       * 纸的上下沿就是操作区的边界，空白被按钮和留白分掉，不再是大白纸。
       *
       * 卡片本身不能再是 <button>（button 里套 button 是非法结构），
       * 所以拆成「容器 + 可点牌面 + 按钮行」三层，牌面负责翻面。
       */}
      <div className="campus-deck-stage">
        <div className={`campus-deck-card${done ? " is-done" : ""}`}>
          <button
            type="button"
            className={`campus-deck-face${flipped && !done ? " is-flipped" : ""}`}
            onClick={() => {
              if (done) {
                if (onRestart) onRestart();
                return;
              }
              if (!flipped) onFlip();
            }}
            aria-label={done ? doneTitle : flipped ? "词卡背面，点一下收起" : "词卡正面，点一下看释义"}
          >
            {done ? (
              <>
                <span className="campus-deck-done-title">{doneTitle}</span>
                <span className="campus-deck-done-text">{doneText}</span>
              </>
            ) : (
              <>
                <span className="campus-deck-tag">{flipped ? "释义" : "正面"}</span>

                <span className="campus-deck-main">
                  {/* 词与释义拆成「词头 + 短线」一组：翻面后词缩小、释义从右边接上，
                      中间那道金线就是两者的分界，不再靠字号差硬撑层级 */}
                  <span className="campus-deck-lead">
                    <span className="campus-deck-word">{word?.[0] ?? "—"}</span>
                    <span className="campus-deck-rule" aria-hidden="true" />
                  </span>

                  {flipped ? (
                    <span className="campus-deck-body">
                      <span className="campus-deck-def">{word?.[1]}</span>
                      {word?.[2] && word?.[3] ? (
                        <span className="campus-deck-eg">
                          <span className="campus-deck-eg-en">{word[2]}</span>
                          <span className="campus-deck-eg-zh">{word[3]}</span>
                        </span>
                      ) : null}
                    </span>
                  ) : (
                    <span className="campus-deck-hint">
                      点一下看释义
                      <span className="campus-desktop-only"> · 或按空格</span>
                    </span>
                  )}
                </span>
              </>
            )}
          </button>

          <div className="campus-deck-actions">
            {done ? (
              <>
                {onRestart ? (
                  <button type="button" className="campus-deck-btn is-known" onClick={onRestart}>
                    {restartText}
                  </button>
                ) : null}
                <button type="button" className="campus-deck-btn" onClick={onClose}>
                  回到面板
                </button>
              </>
            ) : (
              <>
                {ttsReady ? (
                  <button type="button" className="campus-deck-speak" onClick={speak} aria-label="朗读这个单词">
                    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <path d="M11 5 6.5 9H3v6h3.5L11 19z" />
                      <path d="M15.5 9.5a3.5 3.5 0 0 1 0 5" />
                      <path d="M18 7a7 7 0 0 1 0 10" />
                    </svg>
                  </button>
                ) : null}
                <button type="button" className="campus-deck-btn" onClick={() => onJudge(false)}>
                  不认识
                </button>
                <button type="button" className="campus-deck-btn is-known" onClick={() => onJudge(true)}>
                  认识
                </button>
              </>
            )}
          </div>
        </div>
      </div>

      <p className="campus-deck-foot">
        <span className="campus-deck-foot-text">{note}</span>
        <span className="campus-desktop-only">空格 翻面 · → 认识 · ← 不认识 · Esc 退出</span>
      </p>
    </div>
  );
}
