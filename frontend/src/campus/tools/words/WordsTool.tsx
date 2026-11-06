"use client";

/**
 * 四六级词卡。
 *
 * 保住内测 8.2 修好的那条：**点「不认识」也必须前进**。第一版不前进，
 * 用户卡在同一个词上，看起来就是死循环（原话：「我去不了下一个单词」「必须全部都是认识才能走」）。
 *
 * 键盘只在本组件挂载时生效 —— 旧版是靠「面板是否 active」判断的，
 * 这里组件本来就只有激活时才渲染，天然不会在别的工具里抢方向键。
 */

import { useEffect, useRef, useState } from "react";
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
import { FALLBACK_WORDS } from "../../logic/wordsFallback";
import { readWithMigration, writeJson } from "../../storage";
import { CAMPUS_KEYS, LEGACY_KEYS } from "../../storage/keys";
import type { WordItem, WordLevel, WordsProgress } from "../../types";

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

  /** 级别的词库缓存：切级别时不必反复请求 */
  const cache = useRef<Record<string, WordItem[]>>({});
  const [progressReady, setProgressReady] = useState(false);

  useEffect(() => {
    if (!progressReady) return;
    writeJson(CAMPUS_KEYS.words, progress);
  }, [progress, progressReady]);

  useEffect(() => {
    let cancelled = false;

    /** 用某一级别的词开一组 */
    function openGroup(levelId: string, wantedGroup: number, words: WordItem[]) {
      const total = groupCount(words.length);
      const target = Math.min(Math.max(1, wantedGroup), total);

      setLvl(levelId);
      setGroup(target);
      setSession(startSession(groupSlice(words, target)));
      setFlipped(false);
      setProgress((prev) => rememberGroup(prev, levelId, target));
    }

    /** 加载某个级别的词库；失败时用内置词兜底 */
    async function loadLevel(levelId: string, wantedGroup: number) {
      let words = cache.current[levelId];

      if (!words && levelId !== FALLBACK_LEVEL) {
        try {
          const response = await fetch(`/data/words/${levelId.toLowerCase()}.json`, {
            cache: "no-cache",
          });
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const data = (await response.json()) as { words?: WordItem[] };
          words = Array.isArray(data.words) ? data.words : [];
          if (words.length === 0) throw new Error("这个词库是空的");
        } catch (error) {
          words = [...FALLBACK_WORDS];
          if (!cancelled) {
            setNotice(
              `「${levelId}」词库没加载上（${
                error instanceof Error ? error.message : "未知错误"
              }），先用内置的 ${FALLBACK_WORDS.length} 个词顶着。`,
            );
          }
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
  }, []);

  const total = session.pool.length;
  const current: WordItem | undefined = session.pool[session.idx];
  const learned = doneGroups(progress, lvl);

  function handleJudge(know: boolean) {
    if (session.done || session.pool.length === 0) return;

    const next = judge(session, know);
    setSession(next);
    setFlipped(false);

    if (next.done) {
      setProgress((prev) => markGroupDone(prev, lvl, group));
    }
  }

  function restartGroup() {
    const words = cache.current[lvl];
    if (!words) return;
    setSession(startSession(groupSlice(words, group)));
    setFlipped(false);
  }

  function goGroup(next: number) {
    const words = cache.current[lvl];
    if (!words) return;

    const target = Math.min(Math.max(1, next), groupCount(words.length));
    setGroup(target);
    setSession(startSession(groupSlice(words, target)));
    setFlipped(false);
    setProgress((prev) => rememberGroup(prev, lvl, target));
  }

  async function switchLevel(levelId: string) {
    if (levelId === lvl) return;

    let words = cache.current[levelId];
    if (!words) {
      setLoading(true);
      try {
        const response = await fetch(`/data/words/${levelId.toLowerCase()}.json`, {
          cache: "no-cache",
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = (await response.json()) as { words?: WordItem[] };
        words = Array.isArray(data.words) && data.words.length > 0 ? data.words : [...FALLBACK_WORDS];
      } catch {
        words = [...FALLBACK_WORDS];
        setNotice(`「${levelId}」词库没加载上，先用内置词顶着。`);
      }
      cache.current[levelId] = words;
      setLoading(false);
    }

    const target = Math.min(Math.max(1, progress.last[levelId] ?? 1), groupCount(words.length));
    setLvl(levelId);
    setGroup(target);
    setSession(startSession(groupSlice(words, target)));
    setFlipped(false);
    setProgress((prev) => rememberGroup(prev, levelId, target));
  }

  // 键盘：只在输入框之外生效
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const tag = (event.target as HTMLElement | null)?.tagName ?? "";
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (loading) return;

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
        else setFlipped(true);
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

  const percent = total === 0 ? 0 : Math.round((session.idx / total) * 100);
  const isLastGroup = group >= groupMax;

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
    <div id="campus-panel-words" role="tabpanel" aria-label="单词卡">
      <div className="campus-card">
        <div className="campus-card-head">
          <h2 className="campus-card-title">四六级词卡</h2>
          <span className="campus-card-note" style={{ margin: 0 }}>
            {loading
              ? "正在加载词库…"
              : session.done
                ? `第 ${group} 组 · 完成`
                : session.round > 1
                  ? `第 ${group} 组 · 复习 ${session.round} · ${session.idx + 1} / ${total}`
                  : `第 ${group} 组 · ${session.idx + 1} / ${total}`}
          </span>
        </div>

        {/* 级别 + 组导航 */}
        <div className="campus-words-nav">
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
            <span className="campus-card-note" style={{ margin: 0 }}>
              第 <b>{group}</b> / {groupMax} 组
              {learned.length > 0 ? ` · 已练 ${learned.length} 组` : ""}
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

        {/* 词卡 */}
        <button
          type="button"
          className={`campus-flip${session.done ? " is-done" : ""}${flipped ? " is-flipped" : ""}`}
          aria-label={session.done ? "本组已完成" : "词卡，点击翻转看释义"}
          onClick={() => {
            if (session.done) {
              restartGroup();
              return;
            }
            if (!flipped) setFlipped(true);
          }}
        >
          {session.done ? (
            <>
              <span className="campus-flip-word">这组认完了</span>
              <span className="campus-flip-hint">
                {isLastGroup
                  ? "这个级别的词库已经练到最后一组了。点这里重新练这一组。"
                  : "点这里重新练这一组，或用上面的箭头去下一组。"}
              </span>
            </>
          ) : (
            <>
              <span className="campus-flip-word">{current?.[0] ?? "—"}</span>

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
            </>
          )}
        </button>

        <div className="campus-add-row" style={{ marginTop: 12 }}>
          <button type="button" className="campus-btn" onClick={() => setFlipped((prev) => !prev)} disabled={session.done}>
            翻转
          </button>
          <button
            type="button"
            className="campus-btn is-primary"
            onClick={() => handleJudge(true)}
            disabled={session.done}
          >
            认识
          </button>
          <button
            type="button"
            className="campus-btn"
            onClick={() => handleJudge(false)}
            disabled={session.done}
          >
            不认识，复习
          </button>
          <button type="button" className="campus-btn" onClick={restartGroup}>
            重来这组
          </button>
          {session.done && !isLastGroup && (
            <button type="button" className="campus-btn is-primary" onClick={() => goGroup(group + 1)}>
              下一组
            </button>
          )}
        </div>

        <p className="campus-card-note" style={{ marginTop: 12 }}>
          累计判断 <b>{session.seen}</b> 次，其中认识 <b>{session.known}</b>
          {session.done
            ? ""
            : ` · 本组还剩 ${total - session.idx}${
                session.heavy.length > 0 ? ` · 待复习 ${session.heavy.length}` : ""
              }`}
        </p>

        <p className="campus-card-note">
          一组 {GROUP_SIZE} 个词。点「不认识」的词会进这一组的复习堆，但这一轮照样往下走 ——
          等整组过完，再单独练它们。
        </p>

        {/* 纯键盘说明，只在电脑上出现；手机上没有这些键，整行不渲染（由媒体查询隐藏） */}
        <p className="campus-card-note campus-words-keys campus-desktop-only">
          <kbd>空格</kbd> 翻转 · <kbd>→</kbd> 认识 · <kbd>←</kbd> 不认识
        </p>

        {notice !== "" && (
          <p className="campus-card-note" role="status" style={{ color: "var(--red-ink)" }}>
            {notice}
          </p>
        )}

        <p className="campus-card-note campus-words-source">
          {renderSource(source, "词库")}
          {source && sourceExamples ? " · " : ""}
          {renderSource(sourceExamples, "例句")}
        </p>
      </div>
    </div>
  );
}
