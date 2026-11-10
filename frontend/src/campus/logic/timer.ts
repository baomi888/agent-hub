/**
 * 校园工具集 · 番茄钟计时状态机（纯函数）
 *
 * 为什么单独一层：计时是「暂停 / 继续 / 提前结束 / 到点自动结束」四条路径交织，
 * 混在组件里写，暂停过的会话时长会算错（这是这类工具最常见的 bug）。
 * 这里只做算术，组件只负责把结果画出来、把状态存起来。
 *
 * 核心约定：**只记绝对时间点，不记"已经过了多少秒"**。
 * 后台节流、系统休眠、用户改系统时间，都不会让计时漂移。
 */

import type { FocusDirection, FocusEndReason, FocusMode, FocusSession } from "../types";

/** 低于这个秒数的会话视为误触，不写入统计（正常倒计时最短 1 分钟，不受影响） */
export const MIN_RECORD_SECONDS = 30;

/**
 * 一次计时过程。
 *
 * `accumulatedSeconds` 是「之前几个运行段累计的秒数」，`segmentStartAt` 是「当前这一段从何时开始」。
 * 暂停时把当前段折进 accumulated，并把 segmentStartAt 置空 —— 这样"暂停中"就是可判定的状态，
 * 不需要额外的 paused 布尔值（两个字段描述同一件事迟早会不一致）。
 */
export interface TimerState {
  direction: FocusDirection;
  /** 会话开始时刻，统计归属按它算 */
  startedAt: number;
  /** 倒计时的目标秒数；正计时恒为 0 */
  plannedSeconds: number;
  accumulatedSeconds: number;
  /** 当前运行段的起点；null 表示暂停中 */
  segmentStartAt: number | null;
  pausedCount: number;
}

/** 开一个计时。正计时忽略 plannedSeconds */
export function createTimer(
  direction: FocusDirection,
  plannedSeconds: number,
  now: number,
): TimerState {
  const planned =
    direction === "countdown" && Number.isFinite(plannedSeconds)
      ? Math.max(0, Math.round(plannedSeconds))
      : 0;

  return {
    direction,
    startedAt: now,
    plannedSeconds: planned,
    accumulatedSeconds: 0,
    segmentStartAt: now,
    pausedCount: 0,
  };
}

export function isPaused(state: TimerState): boolean {
  return state.segmentStartAt === null;
}

/**
 * 有效计时秒数（已扣除暂停）。
 *
 * 倒计时会被夹到计划时长：到点那一刻组件还没反应过来时，
 * 剩余时间显示 00:00 而不是负数，统计也不会写成 25:01。
 */
export function elapsedOf(state: TimerState, now: number): number {
  const acc = Number.isFinite(state.accumulatedSeconds)
    ? Math.max(0, state.accumulatedSeconds)
    : 0;
  const segment =
    state.segmentStartAt === null ? 0 : Math.max(0, (now - state.segmentStartAt) / 1000);
  const total = acc + segment;

  if (state.direction === "countdown" && state.plannedSeconds > 0) {
    return Math.min(total, state.plannedSeconds);
  }
  return total;
}

/**
 * 时钟上该显示的数字（秒）。
 *
 * 用 floor 而不是 ceil：刚开始应是 25:00，满 1 秒才变 24:59；
 * 到点前最后一秒显示 00:01，不会出现"01 秒卡了两次"。
 */
export function displaySeconds(state: TimerState, now: number): number {
  const elapsed = Math.floor(elapsedOf(state, now));
  if (state.direction === "countdown") {
    return Math.max(0, state.plannedSeconds - elapsed);
  }
  return elapsed;
}

/**
 * 进度 0–1，用于画进度条。
 *
 * 倒计时按「已过 / 计划」正着填；正计时没有终点，按「本小时内的位置」循环填充，
 * 否则进度条永远停在 0，看着像坏了。
 */
export function ratioOf(state: TimerState, now: number): number {
  if (state.direction === "countdown") {
    if (state.plannedSeconds <= 0) return 0;
    return Math.min(1, elapsedOf(state, now) / state.plannedSeconds);
  }
  const elapsed = elapsedOf(state, now);
  return (elapsed % 3600) / 3600;
}

/** 倒计时是否到点。正计时永远不到点，只能手动结束 */
export function isDue(state: TimerState, now: number): boolean {
  if (state.direction !== "countdown") return false;
  if (state.plannedSeconds <= 0) return false;
  // elapsedOf 已夹到 plannedSeconds，所以到点必然是相等而不是大于
  return elapsedOf(state, now) >= state.plannedSeconds;
}

/** 暂停：把当前段折进累计，清空段起点 */
export function pauseTimer(state: TimerState, now: number): TimerState {
  if (isPaused(state)) return state;
  return {
    ...state,
    accumulatedSeconds: elapsedOf(state, now),
    segmentStartAt: null,
    pausedCount: state.pausedCount + 1,
  };
}

/** 继续：以当前时刻作为新段起点。已经暂停的不重复计数 */
export function resumeTimer(state: TimerState, now: number): TimerState {
  if (!isPaused(state)) return state;
  return { ...state, segmentStartAt: now };
}

/**
 * 结束并生成一条会话记录。
 *
 * `seconds` 不从 `endedAt - startedAt` 反推 —— 暂停过就对不上，必须用有效秒数。
 * 倒计时正常完成时直接取计划时长，避免纳秒级误差把 25:00 记成 24:59。
 */
export function buildSession(params: {
  state: TimerState;
  endedAt: number;
  endReason: FocusEndReason;
  task: { id: string; name: string; category: string };
  /** 会话 id，由调用方生成，保证可预测、可去重 */
  id: string;
  mode?: FocusMode;
}): FocusSession {
  const { state, endedAt, endReason, task, id } = params;
  const elapsed = elapsedOf(state, endedAt);
  const seconds = endReason === "finished" ? state.plannedSeconds : Math.round(elapsed);

  return {
    id,
    taskId: task.id,
    taskName: task.name,
    category: task.category,
    startedAt: state.startedAt,
    endedAt,
    seconds: Math.max(0, seconds),
    plannedSeconds: state.plannedSeconds,
    direction: state.direction,
    mode: params.mode ?? "normal",
    pausedCount: state.pausedCount,
    // 切走检测属于模式功能（本轮暂缓），先固定为 0，字段留着免得将来改存储结构
    awayCount: 0,
    awaySeconds: 0,
    endReason,
  };
}

/**
 * 这条会话该不该写进统计。
 *
 * 正常完成的番茄一定记录；中途结束的低于 30 秒视为误触（点开又点掉），
 * 不写 —— 否则误触会污染「专注条数」和「连续天数」，让人不敢相信这个数字。
 */
export function shouldRecord(session: FocusSession, minSeconds = MIN_RECORD_SECONDS): boolean {
  if (session.endReason === "finished") return true;
  return session.seconds >= minSeconds;
}

/** 把秒数说成人话：「1 小时 5 分」「25 分」，用于结束提示与历史列表 */
export function humanizeSeconds(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "不到 1 分钟";
  const total = Math.round(seconds);
  if (total < 60) return "不到 1 分钟";
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest === 0 ? `${hours} 小时` : `${hours} 小时 ${rest} 分`;
}
