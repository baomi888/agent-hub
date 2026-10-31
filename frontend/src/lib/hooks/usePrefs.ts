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
import { DEFAULT_TOP_K, normalizeFontScale, patchPrefs, readPrefs } from "@/lib/consolePrefs";
// FontScale 从 consolePrefs 取，**不要在这个文件里再定义一份**：
// 之前两处各写一个 "auto"|"s"|"m"|"l"，改档位时漏一处就是类型能过、UI 对不上的隐性 bug。
import type { FontScale } from "@/lib/consolePrefs";
/** 转发一下：Sidebar 等处一直从 usePrefs 引这个类型，保留出口免得各处改 import */
export type { FontScale };

/**
 * 模式归一（2026-10-30 收窄）：只把 hybrid 归回 auto，「智能体」不再归一。
 *
 * 10-29 那次把 agent + hybrid 一起归成 auto，理由是"界面上已经没有这两个按钮"。
 * 现在「智能体」按钮回退了（见 components/chat/constants.tsx），再归一就等于
 * 用户选了它、一刷新又变回自动 —— 按钮在、选择却不作数，比没按钮更糟。
 *
 * 仍然归一 hybrid：它是唯一会被后端开关静默降级的档位（开关关闭时退回 agent，
 * 用户以为选了工作流、其实没走），留着只会制造"我选的不是它"的困惑。
 */
const LEGACY_MODES = new Set<string>(["hybrid"]);
const normalizeMode = (m: ChatMode): ChatMode => (LEGACY_MODES.has(m) ? "auto" : m);

/**
 * 老用户的「模式」→「两个能力开关」一次性迁移（2026-10-31）。
 *
 * 10-30 之后后端契约改成 use_kb / use_web，`mode` 降级为兼容字段。后端
 * `_resolve_switches` 给出的等价关系是：
 *   rag    → (use_kb=True,  use_web=False)
 *   llm    → (use_kb=False, use_web=False)
 *   auto   → (use_kb=has_kb, use_web=True)
 *
 * 如果升级后直接落到新默认「两个都不勾」，一个老用户（本地存着 auto）问资料库里
 * 的东西，模型会答"我没有你的资料"——这是断崖式降级，而且没有任何提示。
 * 所以首次读到「没有 useKb/useWeb 但有 mode」的旧偏好时，按上表翻译一次；
 * 下面的写 effect 会把结果落盘，之后就不会再走这里。
 *
 * auto 这里给 useKb=true 而不是 has_kb：前端此刻并不知道用户有没有库，
 * 后端会再 and 一次 has_kb，所以给 true 是安全的（没库时等价于 false）。
 */
/**
 * 旧四档 → 两个开关。**全站唯一一份表**，三处消费：
 *   ① 上面的老用户一次性迁移；② 模式条/模板 ?mode 改动时的同步写；③ 探针核对。
 *
 * 为什么必须唯一：批 A 之后后端 `_resolve_switches` 的优先级是 fields > mode ——
 * 只要请求体带了 use_kb/use_web，`mode` 就被**完全忽略**。于是模式条（默认路径
 * 下它就是用户能看到的唯一选择器）改 mode 而不改开关，等于按钮点了不生效：
 * 实测点「纯对话」后请求体仍是 use_kb=true，用户要的纯对话变成了带检索的智能体。
 * 修法是模式改变时按本表同步写开关；表和迁移用同一份，避免日后改一张忘一张。
 */
export const MODE_CAPS: Record<string, { kb: boolean; web: boolean }> = {
  rag: { kb: true, web: false },
  llm: { kb: false, web: false },
  agent: { kb: true, web: true },
  hybrid: { kb: true, web: true },
  auto: { kb: true, web: true },
};

function migrateLegacyCaps(saved: Prefs): { useKb: boolean; useWeb: boolean } | null {
  // 已经是新偏好（存过任一开关）就不再迁移，否则用户手动关掉的会被旧 mode 覆盖回来
  if (typeof saved.useKb === "boolean" || typeof saved.useWeb === "boolean") return null;
  // 全新用户：没有旧 mode，直接走新默认（两个都不勾，符合决策 D3）
  if (!saved.mode) return null;
  // 未知旧值按 auto 处理（与后端 _resolve_switches 的兜底一致）
  const c = MODE_CAPS[saved.mode] ?? MODE_CAPS.auto;
  return { useKb: c.kb, useWeb: c.web };
}

export interface Prefs {
  mode?: ChatMode;
  /** 能力开关：本轮是否使用知识库。undefined = 老版本没存过（走迁移或新默认） */
  useKb?: boolean;
  /** 能力开关：本轮是否允许智能联网。undefined = 老版本没存过 */
  useWeb?: boolean;
  topK?: number;
  chunkSize?: number;
  chunkOverlap?: number;
  kbPanel?: boolean;
  fontScale?: FontScale;
}

export function usePrefs() {
  const [mode, setMode] = useState<ChatMode>("auto");
  // 两个能力开关：默认都不勾（决策 D3）。老用户由下面的迁移按旧 mode 翻译。
  const [useKb, setUseKb] = useState(false);
  const [useWeb, setUseWeb] = useState(false);
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
    // ⚠️ 每一项都要读回来。漏读某项 = 该项在挂载后立刻被下面的写 effect 用默认值覆盖
    if (saved.mode) setMode(normalizeMode(saved.mode));
    // 能力开关：优先用存过的值；没有则按旧 mode 迁移（只对老用户生效）
    const legacyCaps = migrateLegacyCaps(saved);
    if (typeof saved.useKb === "boolean") setUseKb(saved.useKb);
    else if (legacyCaps) setUseKb(legacyCaps.useKb);
    if (typeof saved.useWeb === "boolean") setUseWeb(saved.useWeb);
    else if (legacyCaps) setUseWeb(legacyCaps.useWeb);
    if (typeof saved.topK === "number" && saved.topK > 0) setTopK(saved.topK);
    if (typeof saved.chunkSize === "number") setChunkSize(saved.chunkSize);
    if (typeof saved.chunkOverlap === "number") setChunkOverlap(saved.chunkOverlap);
    if (typeof saved.kbPanel === "boolean") setKbPanelPref(saved.kbPanel);
    // 收敛而不是直接赋值：老用户本地存的是已取消的 "s" 档，
    // 直接塞进 state 会让 data-fs="s" + 三个按钮都不高亮（看起来像坏了）
    if (saved.fontScale) setFontScale(normalizeFontScale(saved.fontScale));
    setPrefsReady(true);
  }, []);

  // 偏好变化后落盘（prefsReady 之前不写，避免默认值反向覆盖）
  // 走 patchPrefs 而**不是**整对象覆盖：设置页可能刚在这个键里写了检索参数，
  // 整份覆盖会把它们一起冲掉。kbPanelPref 为 null（用户没设置过）时不写该键。
  useEffect(() => {
    if (!prefsReady) return;
    patchPrefs({
      mode,
      useKb,
      useWeb,
      topK,
      chunkSize,
      chunkOverlap,
      ...(kbPanelPref === null ? {} : { kbPanel: kbPanelPref }),
      fontScale,
    });
  }, [prefsReady, mode, useKb, useWeb, topK, chunkSize, chunkOverlap, kbPanelPref, fontScale]);

  return {
    mode,
    setMode,
    useKb,
    setUseKb,
    useWeb,
    setUseWeb,
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
