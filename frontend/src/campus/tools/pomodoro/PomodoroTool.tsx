"use client";

/**
 * 番茄钟（0.3.0 · B2）。
 *
 * 本轮做了三件事，都是为了「每天点一下就能开始」：
 *  1. 任务库 —— 任务建一次长期存在，不必每次输入名字（需求 R5）；
 *  2. 正 / 倒计时 —— 番茄用倒计时，刷题、写代码这类没法定长的用正计时（R1 的一部分）；
 *  3. 会话落库 —— 每次结束写一条记录，成为统计的唯一数据源（B4 饼图与累计时长的地基）。
 *
 * 倒计时走到 00:00 会弹一层「本轮完成」提醒（B2.5）：到点这件事没人看着也会发生，
 * 只靠页面内一行灰字很容易被错过。
 *
 * 计时仍然只记绝对时间点（见 logic/timer.ts），切标签页回来不掉拍。
 */

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import FinishDialog from "./FinishDialog";
import FocusStage from "./FocusStage";
import EntryFold from "./EntryFold";
import RecentLog from "./RecentLog";
import SoundPanel from "./SoundPanel";
import StatsPanel, { type ImportedData } from "./StatsPanel";
import SyncBar from "./SyncBar";
import TaskPicker from "./TaskPicker";
import TimerFace from "./TimerFace";

import { playFinishSound, primeAudio } from "../../logic/audio";
import { DEFAULT_SOUND_PREFS, loadSoundPrefs, saveSoundPrefs } from "../../logic/soundPrefs";
import { joinDuration } from "../../logic/pomodoro";
import {
  enterFullscreen,
  exitFullscreen,
  isFullscreenSnapshot,
  releaseWakeLock,
  requestWakeLock,
  subscribeFullscreen,
  tryLockLandscape,
  unlockOrientation,
  type WakeLockLike,
} from "../../logic/fullscreen";
import {
  buildSession,
  createTimer,
  displaySeconds,
  elapsedOf,
  humanizeSeconds,
  isDue,
  isPaused,
  pauseTimer,
  ratioOf,
  resumeTimer,
  shouldRecord,
  type TimerState,
} from "../../logic/timer";
import { dayKey, makeSessionId, mergeSessions, rangeOf, sortSessions, summarize, trimSessions } from "../../logic/focus";
import { activeTasks, addTask, archiveTask, findTask, makeTask, makeTaskId, mergeTasks, renameTask, restoreTask, touchTask } from "../../logic/focusTask";
import { syncAll, type SyncStatus } from "../../logic/focusSync";
import { loadSessions, loadTasks, saveSessions, saveTasks } from "../../logic/focusStore";
import { lockPageScroll } from "../../logic/scrollLock";
import type { FocusDirection, FocusEndReason, FocusSession, FocusTask, SoundPrefs } from "../../types";

/**
 * 是不是「手机竖着」。
 *
 * 只在同时满足两点时才去锁横屏：粗指针（手指，不是鼠标）+ 当前是竖屏。
 * 桌面宽屏不需要锁（锁了也没用，反而可能在某些实现里抛错）；
 * 已经横着的手机也不需要（lock 是幂等的，但没必要多一次调用）。
 */
function isTouchPortrait(): boolean {
  if (typeof window === "undefined") return false;
  return (
    window.matchMedia("(pointer: coarse)").matches &&
    window.matchMedia("(orientation: portrait)").matches
  );
}

/**
 * 两个入口的图形标记。
 *
 * 之前两行只有文字，上下紧贴时读起来像同一张表的两行数据；
 * 一个表盘、一个饼图，形状不同，扫一眼就知道哪个是「翻记录」、哪个是「看统计」。
 * 和导航图标同一套画法：24 网格、描边 currentColor，深浅主题都跟得上。
 */
function EntryIconLog() {
  return (
    <svg
      className="campus-pomo-entry-ico"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 1.8" />
    </svg>
  );
}

function EntryIconStats() {
  return (
    <svg
      className="campus-pomo-entry-ico"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 12V3.5" />
      <path d="M12 12l7.4 4.3" />
    </svg>
  );
}

/** 完成弹窗要显示的那一轮，不存整条 session —— 今日汇总另算，避免两份数据打架 */
interface DoneInfo {
  taskName: string;
  category: string;
  seconds: number;
}

const DEFAULT_MINUTES = 25;
/** 250ms 一跳：秒数变化才重渲染，比 1s 更跟手，也不会太耗 */
const TICK_MS = 250;
/** 不计时时也要更新「今天」的判断，跨零点统计才不会停在昨天 */
const SLOW_TICK_MS = 60_000;
/** 当前展开的是哪一块：null 表示都收起。两块内容占地大，平时只留两个入口卡 */
type SheetKind = "recent" | "stats";

export default function PomodoroTool() {
  const [tasks, setTasks] = useState<FocusTask[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [sessions, setSessions] = useState<FocusSession[]>([]);
  const [minutes, setMinutes] = useState(DEFAULT_MINUTES);
  const [direction, setDirection] = useState<FocusDirection>("countdown");
  const [timer, setTimer] = useState<TimerState | null>(null);
  const [nowTick, setNowTick] = useState(() => Date.now());
  /** 到点后表盘停在 00:00，直到用户开始下一轮，避免"刚完成却显示 25:00"的错愕 */
  const [zeroed, setZeroed] = useState(false);
  const [notice, setNotice] = useState<{ text: string; done: boolean }>({
    text: "选一个任务，点开始就能计时。",
    done: false,
  });
  const [ready, setReady] = useState(false);
  /** 非 null 表示刚有一轮倒计时走完、提醒弹窗开着 */
  const [done, setDone] = useState<DoneInfo | null>(null);
  const [sound, setSound] = useState<SoundPrefs>(DEFAULT_SOUND_PREFS);
  /** 专注视图是否展开。计时状态不在这里面，所以展开 / 收起都不会丢计时 */
  const [stageOpen, setStageOpen] = useState(false);
  /** 当前打开的是哪个展开区 */
  const [sheet, setSheet] = useState<SheetKind | null>(null);
  /** 展开区里的导入/导出结果。只放这一次操作的回执，不把页面那句常驻提示copy进来 */
  const [statsFeedback, setStatsFeedback] = useState<string | null>(null);
  /**
   * 是否真的进了浏览器全屏。只有用户亲手点了「全屏」才可能为 true。
   * 直接订阅浏览器的全屏状态，不自己拿一份 state 去追 —— 少一次同步就少一类 bug。
   */
  const stageFs = useSyncExternalStore(subscribeFullscreen, isFullscreenSnapshot, () => false);
  /**
   * 页内全屏：iPhone Safari 之类没有元素全屏 API 的环境，用 CSS 把这块铺满视口。
   * 它和 `stageFs` 是**互斥的两条路**（先试真全屏，失败才落到这里），合起来就是「整屏」。
   */
  const [immersive, setImmersive] = useState(false);
  const stageRef = useRef<HTMLElement | null>(null);
  /**
   * 全屏宿主：一个**始终存在**的普通 div，专注视图渲染在它里面。
   *
   * 为什么不在视图本身请求全屏：`requestFullscreen` 必须在点击的同一个任务里发出去，
   * 而视图是 `setStageOpen(true)` 之后才挂载的 —— 等它出现再请求，手势已经过期，
   * 浏览器直接拒绝。这就是「要点两次全屏才进得去」的根因：第一次只是把视图铺开，
   * 第二次才真正全屏。留一个常驻节点，第一次点击就能一步到位。
   */
  const stageHostRef = useRef<HTMLDivElement | null>(null);
  /** 云同步状态。`checking` 是首屏还没问过服务器时的样子 —— 只有 guest / error 会显示出来 */
  const [sync, setSync] = useState<{ status: SyncStatus; message: string }>({
    status: "checking",
    message: "",
  });

  const running = timer !== null && !isPaused(timer);
  const paused = timer !== null && isPaused(timer);
  const activeTask = findTask(tasks, activeId);

  /**
   * 同步一次。
   *
   * **只从 localStorage 读，不从组件状态读**：存储是唯一事实来源（每次改动都先落盘），
   * 所以这里读到的永远是最新一份，不会像闭包那样拿到过期的 sessions。
   * 合并结果写回之前再读一次存储，避免 await 期间新写入的记录被这份结果覆盖。
   */
  const runSync = useCallback(async () => {
    setSync((prev) => ({ ...prev, status: "syncing" }));
    const outcome = await syncAll({ tasks: loadTasks(), sessions: loadSessions() });

    if (outcome.status !== "ok") {
      setSync((prev) => ({
        ...prev,
        status: outcome.status === "guest" ? "guest" : "error",
        message: outcome.message,
      }));
      return;
    }

    const fresh = { tasks: loadTasks(), sessions: loadSessions() };
    const tasks = mergeTasks(fresh.tasks, outcome.snapshot.tasks);
    const sessions = trimSessions(sortSessions(mergeSessions(fresh.sessions, outcome.snapshot.sessions)));

    saveTasks(tasks);
    saveSessions(sessions);
    setTasks(tasks);
    setSessions(sessions);
    setSync({ status: "synced", message: outcome.message });
  }, []);

  // 首屏恢复。effect 里读外部存储在 React 文档里是允许的例外：
  // 此时已挂载、localStorage 可用，且只在挂载时跑一次。
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    const storedTasks = loadTasks();
    const storedSessions = loadSessions();
    setTasks(storedTasks);
    setSessions(storedSessions);
    setSound(loadSoundPrefs());

    // 默认选中最近用过的任务，省掉每天开机多点一次
    const first = activeTasks(storedTasks)[0];
    if (first) setActiveId(first.id);
    setReady(true);

    // 先让界面用本地数据跑起来，再后台问云端 —— 网络慢不该让页面白等
    void runSync();
  }, [runSync]);
  /* eslint-enable react-hooks/set-state-in-effect */

  /**
   * 两个「该重新同步了」的信号：
   *  - 网络恢复：断网期间攒下的记录要补传；
   *  - 页面重新可见：可能在别的标签页登录了账号，或者刚在另一台设备上改过。
   * 同步本身是幂等的，重复跑不会产生重复数据，所以不需要节流。
   */
  useEffect(() => {
    const onOnline = () => void runSync();
    const onVisible = () => {
      if (document.visibilityState === "visible") void runSync();
    };

    window.addEventListener("online", onOnline);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("online", onOnline);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [runSync]);

  // 计时中每 250ms 刷新表盘。到点结算不在这里做 —— 见下面的 due 副作用，
  // 定时器只负责"让界面动起来"，不该承担"写入一条记录"这种只有一次的事情
  useEffect(() => {
    if (!timer) return;

    const id = window.setInterval(() => setNowTick(Date.now()), TICK_MS);
    return () => window.clearInterval(id);
  }, [timer]);

  // 慢速心跳：不计时时也更新「今天」，跨零点后今日统计自动归零
  useEffect(() => {
    const id = window.setInterval(() => setNowTick(Date.now()), SLOW_TICK_MS);
    return () => window.clearInterval(id);
  }, []);

  /** 结算本轮：写记录、更新任务使用时间、给出提示 */
  const finish = useCallback(
    (reason: FocusEndReason) => {
      if (!timer) return;
      const task = findTask(tasks, activeId);
      if (!task) {
        setTimer(null);
        return;
      }

      const endedAt = Date.now();
      const session = buildSession({
        state: timer,
        endedAt,
        endReason: reason,
        task: { id: task.id, name: task.name, category: task.category },
        id: makeSessionId(endedAt),
      });

      setTimer(null);
      /*
       * 一轮结束就退出整屏：完成弹窗要能被看见，用户也该从屏幕里出来。
       *
       * 这里必须**连同浏览器全屏一起退**：视图收起后宿主是个空盒子，
       * 如果还挂在全屏里，屏幕上就是一片空白 —— 到点本来该提醒，结果比什么都不做更糟。
       */
      setStageOpen(false);
      setImmersive(false);
      unlockOrientation();
      void exitFullscreen();

      if (!shouldRecord(session)) {
        // 误触（点开又点掉）不写记录，否则「专注条数」和连续天数会被污染
        setNotice({
          text: `只专注了 ${session.seconds} 秒，未计入记录。`,
          done: false,
        });
        return;
      }

      const next = trimSessions(sortSessions([...sessions, session]));
      setSessions(next);
      saveSessions(next);

      // 记录使用时间，「最近在用」的任务排在前面
      const touched = touchTask(tasks, task.id, endedAt);
      setTasks(touched);
      saveTasks(touched);
      setActiveId(task.id);

      // 刚写进本机就顺手推一次：登录状态下换设备能马上看到这一轮
      void runSync();

      if (reason === "finished") {
        setZeroed(true);
        setNotice({
          text: `完成一段「${task.name}」，${humanizeSeconds(session.seconds)}。站起来走两步再回来。`,
          done: true,
        });
        // 到这里记录已经落库（上面 setSessions / saveSessions 都跑过了），
        // 弹窗只是"通知"，不再承担写入 —— 关掉弹窗不会丢数据。
        setDone({ taskName: task.name, category: task.category, seconds: session.seconds });
        // 声音与弹窗是两条独立的提醒通道：浏览器拦了播放，弹窗照样出现
        playFinishSound(sound);
      } else {
        setZeroed(false);
        setNotice({
          text: `已结束并记下 ${humanizeSeconds(session.seconds)}（${task.name}）。`,
          done: false,
        });
      }
    },
    [timer, tasks, activeId, sessions, sound, runSync],
  );

  /**
   * 倒计时到点自动结算。
   *
   * 用「due 从 false 变 true」驱动，而不是在定时器里判断：定时器每 250ms 跑一次，
   * 判断放在里面就有重复写入的可能；做成派生状态后，这个 effect 只会跑一次。
   */
  const due = timer !== null && isDue(timer, nowTick);
  useEffect(() => {
    if (!due) return;
    // 到点结算是时钟驱动的，没有用户事件可以挂；写记录本身又是副作用（要落存储），
    // 只能放在 effect 里。due 只在 false → true 时变一次，不会级联渲染。
    // eslint-disable-next-line react-hooks/set-state-in-effect
    finish("finished");
  }, [due, finish]);

  /**
   * 到点时人很可能不在这个标签页上，弹窗也就看不到。
   * 页面不可见时把标题改成提醒，切回来一眼就知道刚才发生了什么；弹窗关掉后自动还原。
   */
  useEffect(() => {
    if (!done) return;

    const original = document.title;
    let renamed = false;

    function syncTitle() {
      if (document.visibilityState === "hidden") {
        document.title = `⏰ 专注完成 · ${original}`;
        renamed = true;
      } else if (renamed) {
        document.title = original;
        renamed = false;
      }
    }

    syncTitle();
    document.addEventListener("visibilitychange", syncTitle);
    return () => {
      document.removeEventListener("visibilitychange", syncTitle);
      // 弹窗在页面不可见时被关掉（比如切回来先按了 Esc），标题也要还原
      if (renamed) document.title = original;
    };
  }, [done]);

  /**
   * 页面被卸载时兜底退出全屏 / 解除横屏锁。
   * 切去别的工具（组件卸载）时不移交这些状态，浏览器会停在「没有地址栏、还横着」的怪状态里。
   */
  useEffect(() => {
    return () => {
      void exitFullscreen();
      unlockOrientation();
    };
  }, []);

  /**
   * 页内全屏时锁住背后的页面。
   *
   * 那层是 `position: fixed` 的覆盖层，底下的 `.campus-scroll` 还在，滚轮会穿透过去，
   * 视觉上像是「专注页自己在动」。滚动锁沿用弹窗那一套（给 body 打类，由 CSS 收口）。
   */
  useEffect(() => {
    if (!immersive) return;
    return lockPageScroll();
  }, [immersive]);

  /**
   * 计时卡上的「全屏」入口 —— 打开专注视图 + 立刻整屏。
   *
   * 只在计时中给：专注视图是用来「专心」的，不是用来看设置的。
   *
   * 以前这里只 `setStageOpen(true)`，真正的全屏在视图里的另一颗按钮上，
   * 两颗按钮都叫「全屏」，第一次却没全屏 —— 用户要点两次才进得去。
   *
   * 顺序不能反：先拿常驻宿主节点，再在同一段同步代码里发 `requestFullscreen`。
   * 真全屏请求失败（iPhone Safari、被权限策略拦住的 iframe）就退到页内全屏，
   * 所以「点一下有整屏」在两种环境里都成立。想回到页面内的大视图，
   * 从视图里点「退出全屏」即可，视图本身不会收起。
   */
  function handleFocusFullscreen() {
    if (!timer) return;
    setStageOpen(true);

    const host = stageHostRef.current;
    if (!host) {
      setImmersive(true);
      return;
    }

    void enterFullscreen(host).then((ok) => {
      if (!ok) {
        setImmersive(true);
        return;
      }
      // 只有真全屏才谈得上锁横屏（Android Chrome 要求文档已全屏），且只在手机竖着时锁
      if (isTouchPortrait()) void tryLockLandscape();
    });
  }

  /** 退出整屏：两条路一起收，回到页面内的专注视图（不收起视图） */
  async function exitFocusFullscreen() {
    setImmersive(false);
    unlockOrientation();
    await exitFullscreen();
  }

  /** 视图里的「全屏 / 退出全屏」是个开关 */
  function toggleFocusFullscreen() {
    if (stageFs || immersive) {
      void exitFocusFullscreen();
      return;
    }
    handleFocusFullscreen();
  }

  /**
   * 真进全屏时才保持屏幕常亮。
   *
   * 只在页面内看计时没必要拦着锁屏 —— 那反而像「我的屏幕被占住了」。
   * 页内全屏同样算「整屏专注」，一起纳入。
   * 浏览器在标签页不可见时会自动释放锁，切回来必须重新申请，所以这里是
   * 「申请 / 重申请」而不是一个开关。暂停时释放 —— 既然没在计时，就没必要常亮。
   */
  useEffect(() => {
    if (!stageOpen || !(stageFs || immersive)) return;

    let lock: WakeLockLike | null = null;
    let stopped = false;

    async function acquire() {
      if (stopped) return;
      if (!running || document.visibilityState !== "visible") {
        await releaseWakeLock(lock);
        lock = null;
        return;
      }
      lock = await requestWakeLock();
    }

    void acquire();
    const onVisible = () => {
      if (document.visibilityState === "visible") void acquire();
    };
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      stopped = true;
      document.removeEventListener("visibilitychange", onVisible);
      void releaseWakeLock(lock);
    };
  }, [stageOpen, stageFs, immersive, running]);

  function persistTasks(next: FocusTask[]) {
    setTasks(next);
    saveTasks(next);
  }

  function handleCreate(name: string, category: string) {
    const created = makeTask(
      { name, category: category === "" ? null : category },
      makeTaskId(Date.now(), tasks.length),
      Date.now(),
    );
    if (!created) return;

    const result = addTask(tasks, created);
    persistTasks(result.tasks);
    setActiveId(result.task.id);
    setZeroed(false);
    setNotice({
      text: result.created ? `已添加「${result.task.name}」。` : `「${result.task.name}」已在列表里，已选中。`,
      done: false,
    });
  }

  function handleArchive(id: string) {
    const target = findTask(tasks, id);
    persistTasks(archiveTask(tasks, id));
    if (activeId === id) {
      const rest = tasks.filter((item) => !item.archived && item.id !== id);
      setActiveId(rest[0]?.id ?? null);
    }
    if (target) setNotice({ text: `已归档「${target.name}」，历史记录保留。`, done: false });
  }

  function handleRestore(id: string) {
    persistTasks(restoreTask(tasks, id));
  }

  /**
   * 改名。
   *
   * 校验全在 `renameTask` 里（空名 / 重名 / 任务不存在），这里只负责翻译成一句人话：
   * 返回 null 表示成功，返回字符串表示没改成、把原因交给面板就地显示。
   * 之所以返回值而不是自己弹提示：错误要贴着输入框，用户才知道该改哪。
   */
  function handleRename(id: string, name: string): string | null {
    const result = renameTask(tasks, id, name);
    if (!result.ok) {
      if (result.reason === "empty") return "任务名不能为空";
      if (result.reason === "duplicate") return "已经有同名任务了，换一个名字";
      return "这个任务找不到了，刷新页面再试";
    }

    if (result.changed) persistTasks(result.tasks);
    setNotice({
      text: result.changed ? `已改名为「${result.task.name}」。` : "名字没变。",
      done: false,
    });
    return null;
  }

  function handleStart() {
    if (!activeTask) return;
    setZeroed(false);
    setDone(null);
    // 必须在点击事件里预热音频上下文：等到 25 分钟后才创建，
    // 浏览器会以「非用户手势」为由拒绝出声。
    primeAudio();
    setNotice({ text: `正在专注「${activeTask.name}」…`, done: false });
    setTimer(
      createTimer(direction, direction === "countdown" ? minutes * 60 : 0, Date.now()),
    );
  }

  /** 改提示音偏好：立刻落库，下次打开还是这套 */
  function handleSoundChange(next: SoundPrefs) {
    setSound(next);
    saveSoundPrefs(next);
  }

  /** 试听走和到点播放完全相同的路径，听到什么到时候就响什么 */
  function handleSoundPreview(next: SoundPrefs) {
    primeAudio();
    playFinishSound(next);
  }

  /**
   * 弹窗里的「再来一轮」。
   *
   * 复用 handleStart 而不是各写一份：任务选中、时长、方向都该和手动点「开始」完全一致，
   * 两份实现迟早会漂。
   */
  function handleAgain() {
    handleStart();
  }

  /** 关掉完成提醒。稳定引用，免得每次渲染都让弹窗的 Esc 监听重绑一遍 */
  const closeDone = useCallback(() => setDone(null), []);

  function handlePause() {
    if (!timer) return;
    setTimer(pauseTimer(timer, Date.now()));
    setNotice({ text: "已暂停，继续时会接着算。", done: false });
  }

  function handleResume() {
    if (!timer) return;
    setTimer(resumeTimer(timer, Date.now()));
    setNotice({ text: "继续专注中…", done: false });
  }

  /**
   * 结束并记录。
   *
   * 倒计时提前结束叫 `early`（本来有终点，只是没走到）；
   * 正计时没有终点，手动结束就是正常收尾，记 `stopped` —— 分开统计才知道「提前放弃」有多少。
   */
  function handleStop() {
    finish(direction === "countdown" ? "early" : "stopped");
  }

  /** 重置：明确丢弃本轮，不留记录 */
  function handleReset() {
    setTimer(null);
    setZeroed(false);
    // 和 finish 同理：视图一收，全屏里就只剩一个空盒子
    setStageOpen(false);
    setImmersive(false);
    unlockOrientation();
    void exitFullscreen();
    setNotice({ text: "已重置，本轮没有记录。", done: false });
  }

  /** 导入备份：任务与会话都只补不改，落库后统计立即反映新数据 */
  function handleImport(data: ImportedData) {
    setTasks(data.tasks);
    saveTasks(data.tasks);

    const next = trimSessions(sortSessions(data.sessions));
    setSessions(next);
    saveSessions(next);

    // 导入的记录都没有 syncedAt，下一次同步会自动并进账户
    void runSync();
  }

  const closeStage = useCallback(() => {
    setStageOpen(false);
    setImmersive(false);
    unlockOrientation();
    // 页内全屏没有浏览器态可退，但要兜住「浏览器全屏里按收起」这条路
    void exitFullscreen();
  }, []);

  /** 时长改动：两个输入框与快捷芯片都走这里，统一夹取区间 */
  function handleDuration(hours: number, mins: number) {
    setMinutes(joinDuration(hours, mins));
    setZeroed(false);
  }

  function handleDirection(next: FocusDirection) {
    setDirection(next);
    setZeroed(false);
    setNotice({
      text: next === "countdown" ? "倒计时：到点自动结束并记录。" : "正计时：一直往上走，自己点结束才算完。",
      done: false,
    });
  }

  const todayDate = dayKey(nowTick);
  const todaySummary = useMemo(() => {
    if (!ready) return null;
    const range = rangeOf("day", nowTick);
    return summarize(sessions, range.from, range.to);
    // 依赖里只放日期键：同一天内结果不变，没必要每 250ms 重算一遍
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessions, todayDate, ready]);

  const display = zeroed ? 0 : timer ? displaySeconds(timer, nowTick) : minutes * 60;
  /** 已过秒数：正计时时 display 本身就是已过，这里只在倒计时下才有区分意义 */
  const elapsed = timer ? elapsedOf(timer, nowTick) : 0;

  let percent = 0;
  if (timer) {
    percent = Math.round(ratioOf(timer, nowTick) * 100);
  } else if (zeroed) {
    percent = 100;
  }


  return (
    <div id="campus-panel-pomodoro" role="tabpanel" aria-label="番茄钟">
      <SyncBar status={sync.status} message={sync.message} onSync={() => void runSync()} />

      {/*
        全屏宿主。**它始终在页面上**（收起时是个空盒子，高度为 0），专注视图渲染在它里面。

        存在的唯一理由是「一次点击就全屏」：`requestFullscreen` 必须在点击的同一段任务里
        同步发出，而视图本身要等 `setStageOpen(true)` 之后才挂载 —— 拿它当请求目标，
        必然要等第二次点击。改成对着这个常驻节点请求，第一次点击就能进。
      */}
      <div className="campus-stage-host" ref={stageHostRef}>
        {stageOpen && timer && (
          <FocusStage
            containerRef={stageRef}
            taskName={activeTask?.name ?? null}
            category={activeTask?.category ?? "未分类"}
            direction={direction}
            display={display}
            elapsed={elapsed}
            percent={percent}
            running={running}
            paused={paused}
            now={nowTick}
            fullscreen={stageFs || immersive}
            immersive={immersive}
            onPause={handlePause}
            onResume={handleResume}
            onStop={handleStop}
            onToggleFullscreen={toggleFocusFullscreen}
            onExit={closeStage}
          />
        )}

        {/*
          完成提醒必须挂在宿主**里面**。
          浏览器全屏时只渲染全屏元素及其后代，挂在外面的弹窗等于没渲染 ——
          整屏专注跑完一轮，到点却什么都不出现，是最不能接受的那种静默失败。
          页内全屏下它也一样在这层里，z-index 70 高于整屏层的 60，盖得住。
        */}
        {done && (
          <FinishDialog
            taskName={done.taskName}
            category={done.category}
            seconds={done.seconds}
            todayCount={todaySummary?.sessionCount ?? 0}
            todaySeconds={todaySummary?.totalSeconds ?? 0}
            onAgain={handleAgain}
            onClose={closeDone}
          />
        )}
      </div>

      {/* 专注视图已经放大了大钟和操作，这块就收起，免得同屏摆两个计时 */}
      {!(stageOpen && timer) && (
        <div className="campus-card">
          <div className="campus-card-head">
            <h2 className="campus-card-title">专注计时</h2>
            <span className="campus-card-note" style={{ margin: 0 }}>
              今日 <b>{todaySummary?.sessionCount ?? 0}</b> 段 ·{" "}
              {humanizeSeconds(todaySummary?.totalSeconds ?? 0)}
            </span>
          </div>

          <TimerFace
            direction={direction}
            display={display}
            elapsed={elapsed}
            percent={percent}
            running={running}
            paused={paused}
            canStart={activeTask !== null}
            taskName={activeTask?.name ?? null}
            minutes={minutes}
            onDurationChange={handleDuration}
            onDirectionChange={handleDirection}
            onStart={handleStart}
            onPause={handlePause}
            onResume={handleResume}
            onStop={handleStop}
            onReset={handleReset}
            onFullscreen={handleFocusFullscreen}
          />

          <p className="campus-card-note campus-pomo-notice" aria-live="polite">
            {notice.text}
          </p>

          <SoundPanel prefs={sound} onChange={handleSoundChange} onPreview={handleSoundPreview} />
        </div>
      )}

      <div className="campus-card">
        <div className="campus-card-head">
          <h2 className="campus-card-title">我的任务</h2>
          <span className="campus-card-note" style={{ margin: 0 }}>
            建一次，以后每天直接点开始
          </span>
        </div>

        <TaskPicker
          tasks={tasks}
          activeId={activeId}
          locked={timer !== null}
          onSelect={(id) => {
            setActiveId(id);
            setZeroed(false);
          }}
          onCreate={handleCreate}
          onRename={handleRename}
          onArchive={handleArchive}
          onRestore={handleRestore}
        />
      </div>

      {/*
        「最近记录」和「专注统计」收成两个入口。
        它们原来各占一张卡片、加起来一千多像素高，把「选任务 → 开始」挤成要滚两屏才能用；
        而这两块都是「偶尔查一下」的内容，不属于每天要点的路径。

        两张**各自独立的卡**，不是同一张卡里的上下两行 ——
        挤在一张卡里时，中间只有一条 1px 分隔线，看起来像同一张表的表头，
        既分不清是哪两个去处，又显得贴得太死。现在各自有边框、圆角和间距。
        展开区固定在两张卡之后 —— 点哪个按钮，内容都出现在同一处，
        两个按钮的位置一动不动，不会出现「点上面那个，下面那个被顶下去」的跳动。
        不弹窗、不遮住计时盘和任务列表；看完按「收起」或 Esc 收回，页面高度自己长回来。
      */}
      <div className="campus-pomo-entries">
        <button
          type="button"
          className={`campus-pomo-entry${sheet === "recent" ? " is-open" : ""}`}
          aria-expanded={sheet === "recent"}
          aria-controls="campus-pomo-log-fold"
          onClick={() => setSheet(sheet === "recent" ? null : "recent")}
        >
          <EntryIconLog />
          <span className="campus-pomo-entry-label">最近记录</span>
          <span className="campus-pomo-entry-value">
            {sessions.length > 0 ? `${sessions.length} 条` : "还没有"}
          </span>
          <span className="campus-pomo-entry-go" aria-hidden="true">
            ›
          </span>
        </button>

        <button
          type="button"
          className={`campus-pomo-entry${sheet === "stats" ? " is-open" : ""}`}
          aria-expanded={sheet === "stats"}
          aria-controls="campus-pomo-stats-fold"
          onClick={() => {
            setStatsFeedback(null);
            setSheet(sheet === "stats" ? null : "stats");
          }}
        >
          <EntryIconStats />
          <span className="campus-pomo-entry-label">专注统计</span>
          <span className="campus-pomo-entry-value">
            {/* humanizeSeconds(0) 会说「不到 1 分钟」，放在按钮上像出错了，干脆直说还没开始 */}
            {(todaySummary?.totalSeconds ?? 0) > 0
              ? `今日 ${humanizeSeconds(todaySummary?.totalSeconds ?? 0)}`
              : "今日还没有"}
          </span>
          <span className="campus-pomo-entry-go" aria-hidden="true">
            ›
          </span>
        </button>

        {sheet === "recent" && (
          <EntryFold
            regionId="campus-pomo-log-fold"
            title="最近记录"
            note={`共 ${sessions.length} 条`}
            titleId="campus-pomo-log-title"
            onClose={() => setSheet(null)}
          >
            <RecentLog sessions={sessions} />
          </EntryFold>
        )}

        {sheet === "stats" && (
          <EntryFold
            regionId="campus-pomo-stats-fold"
            title="专注统计"
            titleId="campus-pomo-stats-title"
            onClose={() => setSheet(null)}
          >
            <StatsPanel
              sessions={sessions}
              tasks={tasks}
              now={nowTick}
              ready={ready}
              onImport={handleImport}
              onFeedback={(text, ok) => {
                setNotice({ text, done: ok });
                setStatsFeedback(text);
              }}
            />
            {/* 导入/导出回执原来只写页面底部的提示条，展开区在下面看不到，这里补一份 */}
            {statsFeedback && (
              <p className="campus-card-note campus-pomo-fold-foot" aria-live="polite">
                {statsFeedback}
              </p>
            )}
          </EntryFold>
        )}
      </div>
    </div>
  );
}
