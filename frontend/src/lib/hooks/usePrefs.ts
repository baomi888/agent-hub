"use client";

// 用户偏好持久化：模式 / 检索参数 / 资料库面板开合
// 只存用户显式改过的项，读不到就回落到后端 defaults
import { useEffect, useRef, useState } from "react";
import type { ChatMode } from "@/lib/types";

const PREFS_KEY = "baomi.prefs";

/** 聊天正文字号档位：跟随系统 / 小 / 标准 / 大（CSS 侧映射为 --msg-fs） */
export type FontScale = "auto" | "s" | "m" | "l";
const FONT_SCALES: FontScale[] = ["auto", "s", "m", "l"];

export interface Prefs {
  mode?: ChatMode;
  topK?: number;
  chunkSize?: number;
  chunkOverlap?: number;
  kbPanel?: boolean;
  fontScale?: FontScale;
}

function readPrefs(): Prefs {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    return raw ? (JSON.parse(raw) as Prefs) : {};
  } catch {
    return {};
  }
}

function writePrefs(p: Prefs) {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(p));
  } catch {
    /* 隐私模式下写入失败可忽略 */
  }
}

export function usePrefs() {
  const [mode, setMode] = useState<ChatMode>("auto");
  const [topK, setTopK] = useState(3);
  const [chunkSize, setChunkSize] = useState(500);
  const [chunkOverlap, setChunkOverlap] = useState(50);
  const [kbPanelPref, setKbPanelPref] = useState<boolean | null>(null);
  const [fontScale, setFontScale] = useState<FontScale>("m");
  // 偏好是否已经从本地读回来，读完之前不要往回写，否则默认值会覆盖用户设置
  const [prefsReady, setPrefsReady] = useState(false);
  // 挂载时读到的本地偏好：供初始化流程做 defaults 兜底判断（本地存过的项不用后端默认值）
  const savedRef = useRef<Prefs>({});

  useEffect(() => {
    const saved = readPrefs();
    savedRef.current = saved;
    if (saved.mode) setMode(saved.mode);
    if (typeof saved.kbPanel === "boolean") setKbPanelPref(saved.kbPanel);
    if (FONT_SCALES.includes(saved.fontScale as FontScale)) setFontScale(saved.fontScale as FontScale);
    setPrefsReady(true);
  }, []);

  // 偏好变化后落盘（prefsReady 之前不写，避免默认值反向覆盖）
  useEffect(() => {
    if (!prefsReady) return;
    writePrefs({
      mode,
      topK,
      chunkSize,
      chunkOverlap,
      kbPanel: kbPanelPref ?? undefined,
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
