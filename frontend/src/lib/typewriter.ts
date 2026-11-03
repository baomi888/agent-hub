// 回答文字的播放器：把"模型吐多快"和"人看多快"解耦
//
// 为什么需要它（2026-11-01 实测，dev 环境）：
//   纯对话   首帧 1.70s，2.38 秒吐完 426 字   ≈ 179 字/秒
//   带资料库 首帧 14.34s，0.65 秒吐完 119 字  ≈ 183 字/秒
// 人正常阅读约 6 字/秒。也就是说一条 400 字的回答，模型 2.4 秒就推完了，
// 是阅读速度的 30 倍 —— 页面表现必然是"刷屏式滑到底再停住"，用户只看得见
// 最后一行。这不是滚动策略能救的，只能让文字**放慢到能读**。
//
// 速度是自适应的：积压越多放得越快，长回答不会拖到让人烦；
// 积压追上来了就自动回到 18 字/秒，短回答读起来是豆包那种稳稳的节奏。
//
// 2026-11-02 再降一档（30/120 → 18/50）：线上实测 1299 字的回答 25 秒放完
// ≈ 53 字/秒，是中文舒适阅读（6~8 字/秒）的 7 倍 —— 用户反馈"只能看到最后一行，
// 答完还得滑回去看"。降到 18~50 后同样长度约 34 秒，能跟得上。
//
// 2026-11-03 又提回来（18/50 → 26/92），因为 18/50 矫正过头了：
//   实测后端首字 1.3~2.9 秒，出字 150~183 字/秒，而播放器封顶 50 ——
//   一条 700 字的回答模型 4.7 秒就吐完，界面上却要放到 16 秒，
//   用户点完发送后盯着一个"已在慢慢流"的框子等 15 秒，体感就是"慢"。
//   ★ 关键认识：当初要解决的是**刷屏**（180 字/秒，人眼跟不上），不是"快"本身。
//   180 需要压，50 压过头了。92 大约是中文"看得清但不用等"的档位：
//   700 字约 8 秒放完，比模型慢 3 秒，这是能接受的滞后；再慢就是纯等待。
//   滚动侧不受影响：programScroll 是匀速追平（速度 = 落差/固定时长），
//   每帧位移随落差同步变大，仍在单帧 ≤20px 的判据内。
//   如果以后又要降速，先确认是不是滚动没跟住 —— 那该修滚动，不是砍播放速度。

/** 慢档：积压清零时的速度（字/秒）。26 ≈ 舒适阅读的 3~4 倍，稳定的"在打字"感 */
const MIN_CPS = 26;
/** 快档：积压很多时的上限（字/秒）。700 字约 8 秒放完 */
const MAX_CPS = 92;
/** 每积压 1 个字提速多少。165 字积压顶到上限（26 + 165×0.4 = 92） */
const BACKLOG_GAIN = 0.4;
/** 单帧最多按 64ms 计：标签页切回来时 dt 可能是几秒，补算会一次性喷完 */
const MAX_FRAME_MS = 64;
/** 句末标点：一帧正好跨过它就停在这里，读起来有口气，而不是匀速戳字 */
const BREAK_CHARS = ["。", "！", "？", "；", "…", "\n"];

function speedFor(backlog: number): number {
  return Math.min(MAX_CPS, MIN_CPS + backlog * BACKLOG_GAIN);
}

export interface Typewriter {
  /** 上游来了一小段字：进缓冲，按节奏往外放 */
  push: (text: string) => void;
  /** 上游说完了：把手里的放完再收尾（"稳稳放完"，不突然砸一大段） */
  finish: () => void;
  /** 立刻全显：出错 / 用户点停止时用，不留半截 */
  flush: () => void;
  /** 丢弃还没放的部分（切会话、重试时用） */
  cancel: () => void;
  /** 已经放出来的全文 */
  readonly shown: string;
  /** 缓冲里还剩多少字 */
  readonly backlog: number;
}

export function createTypewriter(opts: {
  /** 每放出一小段就回调一次，参数是**已放出的全文**（不是增量） */
  onText: (shown: string) => void;
  /** 缓冲放空且上游已结束时触发，只触发一次 */
  onDone?: () => void;
}): Typewriter {
  let pending = "";
  let shownText = "";
  let finished = false;
  let reported = false; // onDone 只准响一次
  let raf: number | null = null;
  let lastTs = 0;
  let carry = 0; // 不足一个字的零头，跨帧累加，否则慢速时永远放不出字

  const done = () => {
    if (reported) return;
    reported = true;
    opts.onDone?.();
  };

  function pump(ts: number) {
    raf = null;
    const dt = lastTs ? Math.min(MAX_FRAME_MS, ts - lastTs) : 16;
    lastTs = ts;

    if (!pending) {
      if (finished) {
        done();
        return;
      }
      // 缓冲空但上游还没结束：停下来等 push，别空转烧 CPU
      lastTs = 0;
      return;
    }

    const quota = (speedFor(pending.length) * dt) / 1000 + carry;
    let n = Math.floor(quota);
    carry = quota - n;
    if (n <= 0) {
      raf = requestAnimationFrame(pump);
      return;
    }

    // 这一帧如果正好跨过句末标点，就停在标点后面 —— 长句子中间不要断
    const slice = pending.slice(0, n);
    const hit = BREAK_CHARS.reduce((acc, ch) => Math.max(acc, slice.lastIndexOf(ch)), -1);
    if (hit >= 2 && hit < n - 1) n = hit + 1;

    shownText += pending.slice(0, n);
    pending = pending.slice(n);
    opts.onText(shownText);

    if (!pending && finished) {
      done();
      return;
    }
    raf = requestAnimationFrame(pump);
  }

  function kick() {
    if (raf != null) return;
    raf = requestAnimationFrame(pump);
  }

  return {
    push(text: string) {
      if (!text) return;
      pending += text;
      kick();
    },
    finish() {
      finished = true;
      if (!pending) {
        done();
        return;
      }
      kick();
    },
    flush() {
      if (raf != null) {
        cancelAnimationFrame(raf);
        raf = null;
      }
      if (pending) {
        shownText += pending;
        pending = "";
        opts.onText(shownText);
      }
      finished = true;
      done();
    },
    cancel() {
      if (raf != null) {
        cancelAnimationFrame(raf);
        raf = null;
      }
      pending = "";
      finished = true;
      reported = true; // 主动取消不再回调 onDone，调用方自己在收尾
    },
    get shown() {
      return shownText;
    },
    get backlog() {
      return pending.length;
    },
  };
}
