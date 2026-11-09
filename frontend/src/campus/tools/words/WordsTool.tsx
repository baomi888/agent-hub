"use client";

/**
 * 四六级词卡。
 *
 * 保住内测 8.2 修好的那条：**点「不认识」也必须前进**。第一版不前进，
 * 用户卡在同一个词上，看起来就是死循环（原话：「我去不了下一个单词」「必须全部都是认识才能走」）。
 *
 * 键盘只在本组件挂载时生效 —— 旧版是靠「面板是否 active」判断的，
 * 这里组件本来就只有激活时才渲染，天然不会在别的工具里抢方向键。
 *
 * 2026-11-08 起多了两件事，都在本文件里编排、实现分出去：
 *  1. **全屏刷词**（FlashDeck.tsx）：沉浸式一张一张过，键盘与手势都在那一层；
 *  2. **复习本**（ReviewBook.tsx + logic/wordsReview.ts）：把「反复翻看还记不住」的词
 *     单独攒成长期清单，跨组、跨级别、跨天都能再练。
 * 本文件仍然是唯一的状态拥有者：全屏层与复习本都是受控组件，不各自存一份词池。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  GROUP_SIZE,
  doneGroups,
  emptyProgress,
  groupCount,
  groupSlice,
  judge,
  markGroupDone,
  migrateLegacyProgress,
  rememberGroup,
  startSession,
} from "../../logic/words";
import type { WordSession } from "../../logic/words";
import {
  MARK_LABEL,
  REVIEW_FLIP_THRESHOLD,
  countPending,
  emptyMemory,
  entryToItem,
  markMastered,
  normalizeMemory,
  removeMastered,
  removePending,
  restoreToReview,
  clearMastered,
  trackFlip,
  trackJudge,
} from "../../logic/wordsReview";
import type { CollectResult } from "../../logic/wordsReview";
import { speakWord } from "../../logic/speech";
import { useSpeechSupport } from "../../logic/useSpeech";
import { FALLBACK_WORDS } from "../../logic/wordsFallback";
import { readJson, readWithMigration, writeJson } from "../../storage";
import { CAMPUS_KEYS, LEGACY_KEYS } from "../../storage/keys";
import type { WordItem, WordLevel, WordsMemory, WordsProgress } from "../../types";
import FlashDeck from "./FlashDeck";
import ReviewBook from "./ReviewBook";

interface WordSource {
  repo?: string;
  license?: string;
  copyright?: string;
}

interface WordsIndex {
  levels?: WordLevel[];
  source?: WordSource;
  source_examples?: WordSource;
}

/** 加载失败时的兜底级别 id */
const FALLBACK_LEVEL = "FALLBACK";

/** 收词提示停留时长：一句轻提示，不该一直占着版面 */
const TOAST_MS = 4000;

/**
 * 拉某个级别的词库。
 *
 * 抽成模块函数，是因为「首屏加载」和「用户切级别」两个入口要做完全一样的事：
 * fetch → 校验形状 → 写缓存 → 失败用内置词兜底。分开写时两边连提示文案都不一致
 * （一边带原因和词数，一边只说「先用内置词顶着」），用户看到的提示会随入口变化。
 */
async function fetchLevelWords(levelId: string): Promise<WordItem[]> {
  const response = await fetch(`/data/words/${levelId.toLowerCase()}.json`, { cache: "no-cache" });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);

  const data = (await response.json()) as { words?: WordItem[] };
  const words = Array.isArray(data.words) ? data.words : [];
  if (words.length === 0) throw new Error("这个词库是空的");
  return words;
}

/** 词库没加载上时的提示。两个入口共用一句，同一件事只有一种说法 */
function levelFallbackNotice(levelId: string, error: unknown): string {
  const reason = error instanceof Error ? error.message : "未知错误";
  return `「${levelId}」词库没加载上（${reason}），先用内置的 ${FALLBACK_WORDS.length} 个词顶着。`;
}

/** 进度百分比：面板与全屏两处都要算，分母为 0 时按 0 算（空词库不该出现 NaN%） */
function percentOf(index: number, total: number): number {
  return total === 0 ? 0 : Math.round((index / total) * 100);
}

export default function WordsTool() {
  const [levels, setLevels] = useState<WordLevel[]>([]);
  const [lvl, setLvl] = useState("");
  const [group, setGroup] = useState(1);
  const [session, setSession] = useState<WordSession>(startSession([]));
  const [flipped, setFlipped] = useState(false);
  const [progress, setProgress] = useState<WordsProgress>(emptyProgress());
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);

  const [source, setSource] = useState<WordSource | null>(null);
  const [sourceExamples, setSourceExamples] = useState<WordSource | null>(null);

  /** 复习本档案 */
  const [memory, setMemory] = useState<WordsMemory>(emptyMemory());
  /** 全屏刷词层：关着时 mode 无意义，开着时由它决定词池来自哪里 */
  const [deckOpen, setDeckOpen] = useState(false);
  const [deckMode, setDeckMode] = useState<"group" | "review">("group");
  /** 复习本的这一轮：快照，开始后不随复习本变动（否则掌握的词会中途消失） */
  const [reviewPool, setReviewPool] = useState<WordItem[]>([]);
  const [reviewIdx, setReviewIdx] = useState(0);
  const [reviewDone, setReviewDone] = useState(false);
  const [toast, setToast] = useState("");
  /** 首次进入的引导条：只在没看过时开，第一次翻面/判定也会把它收掉 */
  const [introOpen, setIntroOpen] = useState(false);

  /** 级别的词库缓存：切级别时不必反复请求 */
  const cache = useRef<Record<string, WordItem[]>>({});
  const [progressReady, setProgressReady] = useState(false);

  useEffect(() => {
    if (!progressReady) return;
    writeJson(CAMPUS_KEYS.words, progress);
  }, [progress, progressReady]);

  /**
   * 复习本持久化。
   *
   * 单独一个 ready 标志，理由同 progress：首帧 state 是空档案，此时写一轮
   * 会把用户昨天攒的复习本覆盖成空，得等 boot 读完再开写。
   */
  const [memoryReady, setMemoryReady] = useState(false);
  useEffect(() => {
    if (!memoryReady) return;
    writeJson(CAMPUS_KEYS.wordsReview, memory);
  }, [memory, memoryReady]);

  /* 事件处理里要拿到「最新」的档案：连续两次判定如果都走 setState(prev => …) 的函数式更新，
     就没法在更新外面知道「这次到底有没有收进复习本」。用 ref 镜像一份，读写都不落后一帧。 */
  const memoryRef = useRef<WordsMemory>(memory);
  useEffect(() => {
    memoryRef.current = memory;
  }, [memory]);

  function commitMemory(next: WordsMemory) {
    memoryRef.current = next;
    setMemory(next);
  }

  /**
   * 打开「某一级别的某一组」：组号、词池、翻面状态三件一起复位，并记住位置。
   *
   * 首屏、换级别、上一组 / 下一组、重练这组 —— 四个入口做的都是这件事，
   * 原来各写一遍（4 份），漏掉一处就是「换了组但翻面状态没复位」这类怪 bug。
   * 组号在这里统一夹到合法区间，调用方不必各自 `Math.min(Math.max(...))`。
   */
  const openGroup = useCallback((levelId: string, wantedGroup: number, words: WordItem[]) => {
    const target = Math.min(Math.max(1, wantedGroup), groupCount(words.length));

    setLvl(levelId);
    setGroup(target);
    setSession(startSession(groupSlice(words, target)));
    setFlipped(false);
    setProgress((prev) => rememberGroup(prev, levelId, target));
  }, []);

  useEffect(() => {
    if (toast === "") return;
    const timer = window.setTimeout(() => setToast(""), TOAST_MS);
    return () => window.clearTimeout(timer);
  }, [toast]);

  /* 朗读按钮只在支持的浏览器显示。用 useSyncExternalStore 探测而不是 effect + setState：
     后者会被 react-hooks 的 lint 判为级联渲染，前者在首次 hydration 用服务端快照，不会错位 */
  const ttsReady = useSpeechSupport();

  useEffect(() => {
    let cancelled = false;

    /** 加载某个级别的词库（先看缓存）；失败时用内置词兜底 */
    async function loadLevel(levelId: string, wantedGroup: number) {
      let words = cache.current[levelId];

      if (!words && levelId !== FALLBACK_LEVEL) {
        try {
          words = await fetchLevelWords(levelId);
        } catch (error) {
          words = [...FALLBACK_WORDS];
          if (!cancelled) setNotice(levelFallbackNotice(levelId, error));
        }
        cache.current[levelId] = words;
      }

      if (cancelled || !words) return;
      openGroup(levelId, wantedGroup, words);
    }

    async function boot() {
      const saved =
        readWithMigration<WordsProgress>(CAMPUS_KEYS.words, [LEGACY_KEYS.words], migrateLegacyProgress) ??
        emptyProgress();

      if (cancelled) return;
      setProgress(saved);
      setProgressReady(true);

      /* 复习本与进度分开读：复习本坏了不该连带把「上次练到第几组」也丢掉，
         反之亦然 —— normalizeMemory 已经把脏结构洗成空档案，这里不抛。 */
      const savedMemory = normalizeMemory(readJson(CAMPUS_KEYS.wordsReview));
      memoryRef.current = savedMemory;
      setMemory(savedMemory);
      setMemoryReady(true);

      /* 引导只在第一次出现。放在 boot 里读而不是 useState 初值：
         首帧是服务端渲染的，那一刻没有 localStorage，直接读会闪一版引导再收掉 */
      setIntroOpen(readJson(CAMPUS_KEYS.wordsIntro) === null);

      try {
        const response = await fetch("/data/words/index.json", { cache: "no-cache" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        const index = (await response.json()) as WordsIndex;
        const list = Array.isArray(index.levels) ? index.levels : [];
        if (list.length === 0) throw new Error("词库清单为空");

        if (cancelled) return;
        setLevels(list);
        setSource(index.source ?? null);
        setSourceExamples(index.source_examples ?? null);
        setLoading(false);

        const wanted = list.some((item) => item.id === saved.lvl) ? saved.lvl : list[0].id;
        await loadLevel(wanted, saved.last[wanted] ?? 1);
      } catch (error) {
        if (cancelled) return;

        // 兜底路径：词库文件缺失或断网。必须说清楚，不能让人以为词库就这么点
        setNotice(
          `词库没加载上（${
            error instanceof Error ? error.message : "未知错误"
          }），先用内置的 ${FALLBACK_WORDS.length} 个词顶着。`,
        );
        setLevels([
          {
            id: FALLBACK_LEVEL,
            name: `内置 ${FALLBACK_WORDS.length} 词`,
            count: FALLBACK_WORDS.length,
          },
        ]);

        const fallback = [...FALLBACK_WORDS];
        cache.current[FALLBACK_LEVEL] = fallback;
        setLoading(false);
        openGroup(FALLBACK_LEVEL, saved.last[FALLBACK_LEVEL] ?? 1, fallback);
      }
    }

    void boot();
    return () => {
      cancelled = true;
    };
  }, [openGroup]);

  const total = session.pool.length;
  const current: WordItem | undefined = session.pool[session.idx];
  const learned = doneGroups(progress, lvl);

  /**
   * 翻到背面顺手记一笔。
   *
   * 计数只在「正面 → 背面」这一次跳变时加，来回点翻转不会把次数刷上去；
   * 跨会话累计。翻看满 5 次就标「可能不熟悉」，这是两个独立条件里的第一个。
   */
  function flipCard() {
    setFlipped(true);
    dismissIntro();
    if (!current) return;

    const result = trackFlip(memoryRef.current, current, lvl, Date.now());
    commitMemory(result.mem);
    announce(result, current[0]);
  }

  /** 关掉引导并落盘。判过一次就等于「会用了」，不必再点「知道了」 */
  function dismissIntro() {
    if (!introOpen) return;
    setIntroOpen(false);
    writeJson(CAMPUS_KEYS.wordsIntro, { v: 1, at: Date.now() });
  }

  /** 收词/升级时给一句提示；没变化就什么都不弹 */
  function announce(result: CollectResult, w: string) {
    if (result.change === "newly") {
      setToast(
        result.mark === "sure"
          ? `「${w}」收进复习本 · ${MARK_LABEL.sure}`
          : `「${w}」翻看了 ${REVIEW_FLIP_THRESHOLD} 次，收进复习本 · ${MARK_LABEL.unsure}`,
      );
    } else if (result.change === "upgraded") {
      setToast(`「${w}」改成「${MARK_LABEL.sure}」`);
    }
  }

  function handleJudge(know: boolean) {
    if (session.done || session.pool.length === 0) return;

    dismissIntro();
    const next = judge(session, know);
    setSession(next);
    setFlipped(false);

    if (next.done) {
      setProgress((prev) => markGroupDone(prev, lvl, group));
    }

    if (!know && current) {
      const result = trackJudge(memoryRef.current, current, lvl, know, Date.now());
      commitMemory(result.mem);
      announce(result, current[0]);
    }
  }

  /** 全屏刷词：沿用当前这一组，不重开 —— 用户在面板里翻到第 8 个，点全屏应该接着第 8 个 */
  function openGroupDeck() {
    setDeckMode("group");
    setDeckOpen(true);
  }

  /** 进复习本：先把待复习清单拍成快照，这一轮里掌握的词不会中途把后面的位置挪掉 */
  function startReview() {
    const pool = memoryRef.current.items.map(entryToItem);
    if (pool.length === 0) return;

    setReviewPool(pool);
    setReviewIdx(0);
    setReviewDone(false);
    setFlipped(false);
    setDeckMode("review");
    setDeckOpen(true);
  }

  function handleReviewJudge(know: boolean) {
    const word = reviewPool[reviewIdx];
    if (!word) return;

    if (know) {
      // 认识就移进「已掌握」：清单越练越短，剩下的才是真没记住的
      commitMemory(markMastered(memoryRef.current, word[0], Date.now()));
    }

    const next = reviewIdx + 1;
    setFlipped(false);
    if (next >= reviewPool.length) {
      setReviewDone(true);
      setReviewIdx(reviewPool.length);
    } else {
      setReviewIdx(next);
    }
  }

  function restartGroup() {
    const words = cache.current[lvl];
    if (!words) return;
    openGroup(lvl, group, words);
  }

  function goGroup(next: number) {
    const words = cache.current[lvl];
    if (!words) return;
    openGroup(lvl, next, words);
  }

  async function switchLevel(levelId: string) {
    if (levelId === lvl) return;

    let words = cache.current[levelId];
    if (!words) {
      setLoading(true);
      try {
        words = await fetchLevelWords(levelId);
      } catch (error) {
        words = [...FALLBACK_WORDS];
        setNotice(levelFallbackNotice(levelId, error));
      }
      cache.current[levelId] = words;
      setLoading(false);
    }

    openGroup(levelId, progress.last[levelId] ?? 1, words);
  }

  // 键盘：只在输入框之外生效
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const tag = (event.target as HTMLElement | null)?.tagName ?? "";
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (loading) return;
      // 全屏层开着时键盘归它管，否则按一下空格会既翻全屏的卡又翻面板的卡
      if (deckOpen) return;

      if (session.done) {
        if (event.key === " " || event.key === "Enter") {
          event.preventDefault();
          restartGroup();
        }
        return;
      }

      if (event.key === " " || event.key === "Enter") {
        event.preventDefault();
        if (flipped) handleJudge(true);
        else flipCard();
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        handleJudge(true);
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        handleJudge(false);
      }
    }

    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  });

  const groupMax = groupCount(
    lvl === FALLBACK_LEVEL
      ? FALLBACK_WORDS.length
      : levels.find((item) => item.id === lvl)?.count ?? GROUP_SIZE,
  );

  const percent = percentOf(session.idx, total);
  const isLastGroup = group >= groupMax;

  /* 全屏层的全部输入：组模式读 session，复习模式读复习快照。
     两套状态合成一份「现在该显示什么」，全屏层自己就不需要知道词从哪来。 */
  const isReviewDeck = deckMode === "review";
  const deckWord = isReviewDeck ? reviewPool[reviewIdx] : current;
  const deckIndex = isReviewDeck ? reviewIdx : session.idx;
  const deckTotal = isReviewDeck ? reviewPool.length : total;
  const deckDone = isReviewDeck ? reviewDone : session.done;
  const deckPercent = percentOf(deckIndex, deckTotal);
  const deckCounter = deckDone ? `${deckTotal} / ${deckTotal}` : `${deckIndex + 1} / ${deckTotal}`;
  const pendingCount = countPending(memory);

  function levelNameOf(id: string): string {
    return levels.find((item) => item.id === id)?.name ?? id;
  }

  function renderSource(item: WordSource | null, tag: string) {
    if (!item?.repo) return null;
    const name = item.repo.replace("https://github.com/", "");
    return (
      <span key={tag}>
        {tag} {item.license ?? ""} ·{" "}
        <a href={item.repo} target="_blank" rel="noopener noreferrer">
          {name}
        </a>
      </span>
    );
  }

  return (
    <div
      id="campus-panel-words"
      className={introOpen ? "is-intro" : undefined}
      role="tabpanel"
      aria-label="单词卡"
    >
      {/*
       * 新手引导：规则只在第一次讲一遍。
       * 原来「一组 20 个词…」「翻看 5 次…」「键盘…」「词库来源…」四条常驻在卡片底部，
       * 手机上一屏塞了四段同色同号的灰字，读起来像一段乱码 ——
       * 讲一次的东西不该天天占版面，之后收进右侧「怎么用」折叠块。
       */}
      {introOpen ? (
        <section className="campus-words-guide" aria-label="新手提示">
          <div className="campus-words-guide-head">
            <span className="campus-words-guide-kicker">新手提示 · 三步就够</span>
            <button
              type="button"
              className="campus-words-guide-close"
              onClick={dismissIntro}
              aria-label="关闭新手提示"
            >
              ✕
            </button>
          </div>
          <ol className="campus-words-guide-steps">
            <li>
              <b>点卡片</b>翻面看释义
              <span className="campus-desktop-only">（或按空格）</span>
            </li>
            <li>
              <b>认识 / 不认识</b> 二选一，都会往下走
            </li>
            <li>
              反复翻看或点过「不认识」→ 自动进<b>复习本</b>
            </li>
          </ol>
          <button type="button" className="campus-words-guide-ok" onClick={dismissIntro}>
            知道了，开始刷
          </button>
        </section>
      ) : null}

      <div className="campus-words-layout">
        <div className="campus-card campus-words-main">
          <div className="campus-card-head">
            <h2 className="campus-card-title">四六级词卡</h2>
            {/* 组导航并进标题行：原来「第 1 / 228 组」自己占一整行，
                和上面那枚「第 1 组 · 1 / 20」说的是同一件事，两行灰字叠着读。
                现在箭头夹着那枚状态胶囊，一行就交代「我在哪、能前后走」。 */}
            <div className="campus-words-group">
              <button
                type="button"
                className="campus-wk-btn"
                aria-label="上一组"
                disabled={group <= 1}
                onClick={() => goGroup(group - 1)}
              >
                ‹
              </button>
              <span className="campus-words-state" title={`共 ${groupMax} 组`}>
                {loading
                  ? "正在加载词库…"
                  : session.done
                    ? `第 ${group} 组 · 完成`
                    : session.round > 1
                      ? `第 ${group} 组 · 复习 ${session.round} · ${session.idx + 1} / ${total}`
                      : `第 ${group} 组 · ${session.idx + 1} / ${total}`}
              </span>
              <button
                type="button"
                className="campus-wk-btn"
                aria-label="下一组"
                disabled={group >= groupMax}
                onClick={() => goGroup(group + 1)}
              >
                ›
              </button>
            </div>
          </div>

          {/* 级别 */}
          <div className="campus-chips">
            {levels.map((level) => (
              <button
                key={level.id}
                type="button"
                className={`campus-chip${level.id === lvl ? " is-on" : ""}`}
                aria-pressed={level.id === lvl}
                onClick={() => void switchLevel(level.id)}
              >
                {level.name}
                <small>{level.count}</small>
              </button>
            ))}
          </div>

        {/* 进度：条 + 百分比。原来只有 4px 细条，看半天不知道还剩多少 */}
        <div className="campus-words-meter">
          <div
            className="campus-words-bar"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={percent}
            aria-label="本组进度"
          >
            <div className="campus-words-fill" style={{ width: `${percent}%` }} />
          </div>
          <span className="campus-words-pct">{percent}%</span>
        </div>

        {/*
         * 词卡。外层是容器而**不是**一个整块的 <button>：
         * 手机端原来卡片下面还挂着「不认识 / 认识」两行按钮，卡片本身空着一大片，
         * 整屏像一张白纸配一排遥控器。现在把判定按钮和全屏入口都收进卡内，
         * 卡就是「一张牌」，按钮长在牌上 —— 这也是百词斩那类刷词器的样子。
         *
         * 卡片内部三层：左上角状态 / 右上角小工具 / 中间可点的牌面 + 底部判定按钮。
         */}
        <div className={`campus-flip${session.done ? " is-done" : ""}${flipped ? " is-flipped" : ""}`}>
          {/* 左上角这枚小字说的是「你现在看的是正面还是释义」，
              没有它的话翻面只换了内容、卡片长得一模一样，读者会以为自己没点上 */}
          <span className="campus-flip-tag">
            {session.done ? "本组完成" : flipped ? "释义" : "正面"}
          </span>

          {/* 右上角：朗读、重练。都是低频小动作，做成图标，不跟「认识」抢位置 */}
          <div className="campus-flip-tools">
            {ttsReady && !session.done ? (
              <button
                type="button"
                className="campus-flip-icon"
                onClick={() => speakWord(current?.[0] ?? "")}
                aria-label="朗读这个单词"
                title="朗读"
              >
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M11 5 6.5 9H3v6h3.5L11 19z" />
                  <path d="M15.5 9.5a3.5 3.5 0 0 1 0 5" />
                  <path d="M18 7a7 7 0 0 1 0 10" />
                </svg>
              </button>
            ) : null}
            {!session.done ? (
              <button
                type="button"
                className="campus-flip-icon"
                onClick={restartGroup}
                aria-label="重练这一组"
                title="重练这组"
              >
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M3 12a9 9 0 1 1 2.6 6.3" />
                  <path d="M3 20v-5h5" />
                </svg>
              </button>
            ) : null}
          </div>

          <button
            type="button"
            className="campus-flip-face"
            aria-label={
              session.done ? "本组已完成，点一下重练这一组" : "词卡，点击翻转看释义"
            }
            onClick={() => {
              if (session.done) {
                restartGroup();
                return;
              }
              if (!flipped) flipCard();
            }}
          >
            {session.done ? (
              <>
                <span className="campus-flip-word">这组认完了</span>
                <span className="campus-flip-hint">
                  {isLastGroup
                    ? "这个级别的词库已经练到最后一组了。点这里重练这一组。"
                    : "点这里重练这一组，或用上面的箭头去下一组。"}
                </span>
              </>
            ) : (
              <>
                <span className="campus-flip-word">{current?.[0] ?? "—"}</span>
                {/* 词与释义之间一道 30px 的短线：翻面后词会缩小、释义顶上来，
                    没有这道线，两段字就是上下堆着；有了它，读的是「标题 / 正文」两层 */}
                <span className="campus-flip-rule" aria-hidden="true" />

                {flipped && current ? (
                  <span className="campus-flip-body">
                    <span className="campus-flip-def">{current[1]}</span>
                    {current[2] && current[3] && (
                      <span className="campus-flip-eg">
                        <span className="campus-flip-eg-tag">例句</span>
                        <span className="campus-flip-eg-en">{current[2]}</span>
                        <span className="campus-flip-eg-zh">{current[3]}</span>
                      </span>
                    )}
                  </span>
                ) : (
                  <span className="campus-flip-hint">
                    {session.round > 1 && session.idx === 0
                      ? `这一轮只练刚才没记住的 ${total} 个词 · 点击卡片翻转`
                      : (
                        <>
                          点击卡片
                          {/* 键盘提示只给电脑：手机上「按空格」是句废话 */}
                          <span className="campus-desktop-only">（或按空格）</span>
                          翻转看释义
                        </>
                      )}
                  </span>
                )}

                {/*
                 * 本组位置点阵。
                 * 进度条回答「百分之多少」，点阵回答「走到第几个、还剩几个」。
                 * 它跟着词一起居中：原来牌面下半截只剩一段说不清用途的留白，
                 * 这条轨道落在那儿，留白就有了交代，翻词也有「一格一格往前走」的手感。
                 */}
                {total > 0 ? (
                  <span className="campus-flip-dots" aria-hidden="true">
                    {session.pool.map((item, i) => (
                      <span
                        key={`${item[0]}-${i}`}
                        className={`campus-flip-dot${i < session.idx ? " is-past" : ""}${
                          i === session.idx ? " is-now" : ""
                        }`}
                      />
                    ))}
                  </span>
                ) : null}
              </>
            )}
          </button>

          {/*
           * 判定按钮长在牌面上：
           * 进行中是「不认识 / 认识」二选一（认识是主操作，金实底）；
           * 认完了就不再摆两个禁用按钮装样子，直接换成「重练 / 下一组」。
           *
           * 全屏入口并到同一行（牌底最右）而不是单起一行：
           * 原来它自己占 30px，牌底就成了「按钮行 + 一条小按钮 + 卡片下沿」三层，
           * 手机上白白多出一截高度；并进这一行后，牌底只有一条操作带。
           */}
          <div className="campus-flip-controls">
            <div className="campus-flip-actions">
              {session.done ? (
                <>
                  <button type="button" className="campus-words-btn" onClick={restartGroup}>
                    重练这组
                  </button>
                  {!isLastGroup && (
                    <button
                      type="button"
                      className="campus-words-btn is-known"
                      onClick={() => goGroup(group + 1)}
                    >
                      下一组
                    </button>
                  )}
                </>
              ) : (
                <>
                  <button
                    type="button"
                    className="campus-words-btn"
                    onClick={() => handleJudge(false)}
                  >
                    不认识
                  </button>
                  <button
                    type="button"
                    className="campus-words-btn is-known"
                    onClick={() => handleJudge(true)}
                  >
                    认识
                  </button>
                </>
              )}
            </div>

            {/* 沉浸刷词是这页的第二形态。做成方形图标按钮与判定按钮同行 ——
                它的字号小于「认识」，不会跟主操作抢；但图标 + 所在位置一眼可见 */}
            {!session.done ? (
              <button
                type="button"
                id="campus-words-deck-entry"
                className="campus-words-deck-btn"
                onClick={openGroupDeck}
                disabled={total === 0}
                aria-label="全屏刷词"
                title="全屏刷词"
              >
                <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M4 9V5h4M20 9V5h-4M4 15v4h4M20 15v4h-4" />
                </svg>
                {/* 电脑上地方够，带上两个字；手机上只留图标（方格箭头本身就等于全屏） */}
                <span className="campus-words-deck-label">全屏</span>
              </button>
            ) : null}
          </div>
        </div>

        {/* 三格读数：把原来挤在一行小灰字里的「还剩 / 待复习」提成数字 */}
        <div className="campus-words-stats">
          <div className="campus-words-stat">
            <span className="campus-words-stat-num">{Math.max(0, total - session.idx)}</span>
            <span className="campus-words-stat-label">本组还剩</span>
          </div>
          <div className="campus-words-stat">
            <span className="campus-words-stat-num">{session.heavy.length}</span>
            <span className="campus-words-stat-label">本组待复习</span>
          </div>
          <div className="campus-words-stat">
            <span className="campus-words-stat-num">{learned.length}</span>
            <span className="campus-words-stat-label">已练组数</span>
          </div>
        </div>

        {toast !== "" && (
          <p className="campus-words-toast" role="status">
            {toast}
          </p>
        )}

        {notice !== "" && (
          <p className="campus-card-note" role="status" style={{ color: "var(--red-ink)" }}>
            {notice}
          </p>
        )}
        </div>

        {/*
         * 右栏（手机上落在下方）：复习本 + 一个默认折叠的「怎么用」。
         * 规则、键盘、词库来源都收进折叠块 —— 它们是「想知道的时候查」，不是「每次都要看」。
         */}
        <aside className="campus-words-side">
          <ReviewBook
            mem={memory}
            levelName={levelNameOf}
            onStart={startReview}
            onRemove={(w) => commitMemory(removePending(memoryRef.current, w))}
            onRestore={(w) => commitMemory(restoreToReview(memoryRef.current, w))}
            onRemoveMastered={(w) => commitMemory(removeMastered(memoryRef.current, w))}
            onClearMastered={() => commitMemory(clearMastered(memoryRef.current))}
          />

          <details className="campus-words-help">
            <summary>怎么用 · 规则与词库来源</summary>
            <ul className="campus-words-help-list">
              <li>
                一组 {GROUP_SIZE} 个词。点「不认识」的词会在整组过完之后单独再练一轮。
              </li>
              <li>
                翻看满 {REVIEW_FLIP_THRESHOLD} 次 → <b>可能不熟悉</b>；点过「不认识」→{" "}
                <b>确定不熟悉</b>。两个条件各自独立，任一成立就收进复习本。
              </li>
              <li>复习本里点「认识」的词会移到已掌握，不再出现在复习轮里。</li>
              <li className="campus-desktop-only">
                键盘：<kbd>空格</kbd> 翻面 · <kbd>→</kbd> 认识 · <kbd>←</kbd> 不认识 ·{" "}
                <kbd>Esc</kbd> 退出全屏
              </li>
            </ul>
            <p className="campus-card-note campus-words-source">
              {renderSource(source, "词库")}
              {source && sourceExamples ? " · " : ""}
              {renderSource(sourceExamples, "例句")}
            </p>
          </details>
        </aside>
      </div>

      <FlashDeck
        open={deckOpen}
        eyebrow={isReviewDeck ? "复习本" : `第 ${group} 组`}
        counter={deckCounter}
        percent={deckPercent}
        word={deckWord}
        flipped={flipped}
        done={deckDone}
        doneTitle={isReviewDeck ? "复习本过完了" : "这组认完了"}
        doneText={
          isReviewDeck
            ? `认下的 ${Math.max(0, reviewPool.length - pendingCount)} 个已移进已掌握，复习本里还剩 ${pendingCount} 个。`
            : session.round > 1
              ? `这一轮把刚才没记住的 ${total} 个词又过了一遍。`
              : `一组 ${total} 个词过完了。`
        }
        note={
          isReviewDeck
            ? "认识 → 移到已掌握；不认识 → 留在复习本"
            : `翻看 ${REVIEW_FLIP_THRESHOLD} 次或不认识 → 进复习本`
        }
        onFlip={isReviewDeck ? () => setFlipped(true) : flipCard}
        onJudge={isReviewDeck ? handleReviewJudge : handleJudge}
        onClose={() => setDeckOpen(false)}
        onRestart={
          isReviewDeck
            ? pendingCount > 0
              ? startReview
              : undefined
            : restartGroup
        }
        restartText={
          isReviewDeck ? `复习剩下的 ${pendingCount} 个` : "重练这组"
        }
        returnFocusTo="#campus-words-deck-entry"
      />
    </div>
  );
}
