"use client";

// 用户偏好持久化：模式 / 检索参数 / 资料库面板开合
// 只存用户显式改过的项，读不到就回落到后端 defaults
//
// ⚠️ 读写一律走 consolePrefs（同一个 `baomi.prefs` 键）：设置页也在写这个键，
// 且它是「读进来 → 改一项 → 写回去」。这里如果自己拼对象整份覆盖，
// 就会把用户刚在设置页改的检索参数冲掉 —— 历史 bug：本文件读端只回 3 个字段、
// 写端却写 6 个，于是「设置页改 8 段 → 回工作台 → 又变回 3 段」，
// 且用户不会有任何提示。**新增字段时，读端与写端必须同时改。**
import { useEffect, useRef, useState } from "react";
import type { ChatMode } from "@/lib/types";
import { DEFAULT_TOP_K, patchPrefs, readPrefs } from "@/lib/consolePrefs";

/** 聊天正文字号档位：跟随系统 / 小 / 标准 / 大（CSS 侧映射为 --msg-fs） */
export type FontScale = "auto" | "s" | "m" | "l";
const FONT_SCALES: FontScale[] = ["auto", "s", "m", "l"];

/**
 * 模式入口收敛（2026-10-29）：历史存过 agent / hybrid 的一律回到 auto。
 *
 * 不归一会出两个问题：① 这两个值在界面上已经没有按钮，刷新后没有任何档位高亮；
 * ② 请求会一直带着一个用户无法撤销的模式，等于被旧偏好绑架。
 * 后端仍接受这两个值（内部脚本与演示用），所以只在读偏好这一层做归一。
 */
const LEGACY_MODES = new Set<string>(["agent", "hybrid"]);
const normalizeMode = (m: ChatMode): ChatMode => (LEGACY_MODES.has(m) ? "auto" : m);

export interface Prefs {
  mode?: ChatMode;
  topK?: number;
  chunkSize?: number;
  chunkOverlap?: number;
  kbPanel?: boolean;
  fontScale?: FontScale;
}

export function usePrefs() {
  const [mode, setMode] = useState<ChatMode>("auto");
  const [topK, setTopK] = useState(DEFAULT_TOP_K);
  const [chunkSize, setChunkSize] = useState(500);
  const [chunkOverlap, setChunkOverlap] = useState(50);
  const [kbPanelPref, setKbPanelPref] = useState<boolean | null>(null);
  const [fontScale, setFontScale] = useState<FontScale>("m");
  // 偏好是否已经从本地读回来，读完之前不要往回写，否则默认值会覆盖用户设置
  const [prefsReady, setPrefsReady] = useState(false);
  // 挂载时读到的本地偏好：供初始化流程做 defaults 兜底判断（本地存过的项不用后端默认值）
  const savedRef = useRef<Prefs>({});

  useEffect(() => {
    const saved = readPrefs() as Prefs;
    savedRef.current = saved;
    // ⚠️ 六项都要读回来。漏读某项 = 该项在挂载后立刻被下面的写 effect 用默认值覆盖
    if (saved.mode) setMode(normalizeMode(saved.mode));
    if (typeof saved.topK === "number" && saved.topK > 0) setTopK(saved.topK);
    if (typeof saved.chunkSize === "number") setChunkSize(saved.chunkSize);
    if (typeof saved.chunkOverlap === "number") setChunkOverlap(saved.chunkOverlap);
    if (typeof saved.kbPanel === "boolean") setKbPanelPref(saved.kbPanel);
    if (FONT_SCALES.includes(saved.fontScale as FontScale)) setFontScale(saved.fontScale as FontScale);
    setPrefsReady(true);
  }, []);

  // 偏好变化后落盘（prefsReady 之前不写，避免默认值反向覆盖）
  // 走 patchPrefs 而**不是**整对象覆盖：设置页可能刚在这个键里写了检索参数，
  // 整份覆盖会把它们一起冲掉。kbPanelPref 为 null（用户没设置过）时不写该键。
  useEffect(() => {
    if (!prefsReady) return;
    patchPrefs({
      mode,
      topK,
      chunkSize,
      chunkOverlap,
      ...(kbPanelPref === null ? {} : { kbPanel: kbPanelPref }),
      fontScale,
    });
  }, [prefsReady, mode, topK, chunkSize, chunkOverlap, kbPanelPref, fontScale]);

  return {
    mode,
    setMode,
    topK,
    setTopK,
    chunkSize,
    setChunkSize,
    chunkOverlap,
    setChunkOverlap,
    kbPanelPref,
    setKbPanelPref,
    fontScale,
    setFontScale,
    prefsReady,
    savedRef,
  };
}
