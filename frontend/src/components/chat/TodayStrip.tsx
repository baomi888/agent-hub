"use client";

// 首屏「今天」条：把打卡 / 六级 / 委托三件事收成一条横条。
//
// 为什么是**一条**而不是三张卡：欢迎页首屏已经排满（logo + 标题 + 三张日更
// 大卡 + 一行小胶囊 + 三句示例），390×844 上刚好不用滚动。再加一张卡就得滚，
// 而「今天该干什么」的价值恰恰是"一进来就看见"——滚下去才出现等于没做。
//
// 为什么没数据时整条不渲染：一个从没建过计划/委托的人看到「0 天 · 0 个任务」，
// 只会以为这是又一张要填的表单。显隐交给后端 has_any 判定，前端不猜。
//
// 取数走 /api/today 一个请求（不是并发打三个），理由见 api/today.py。
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import type { TodaySnapshot } from "@/lib/types";

/** 秒级时间戳 → 本地 HH:MM。用本地时区：委托的执行时间本来就是"你几点收到"。 */
function hhmm(ts: number): string {
  const d = new Date(ts * 1000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

type View = { main: string; sub: string; href: string; cta: string };

/**
 * 把快照翻成一句话。
 *
 * 主句按「今天最该动手的那件事」排优先级：六级有未完成任务 → 今天没打卡 →
 * 委托还没跑 → 都做完了。副句只放两段，多了在 390 宽下必然折行、把条撑高。
 */
export function describeToday(t: TodaySnapshot): View {
  const cetLeft = t.cet.has_plan ? Math.max(0, t.cet.today_total - t.cet.today_done) : 0;
  const bits: string[] = [];

  // 副句候选：都不是"现在该动手"的事，所以放在第二行
  if (t.cet.has_plan && cetLeft === 0) {
    bits.push(t.cet.days_left != null ? `距考试 ${t.cet.days_left} 天` : "六级计划进行中");
  }
  if (t.delegate.enabled > 0) {
    if (t.delegate.next_at) {
      // 不加书名号：390 宽下副句可用宽度只有约 266px，
      // 「阅读精读：段落匹配 · 22:30 「睡前复盘」」会正好在书名号处被截成
      // 「22:30 「...」。去掉引号刚好能一行放下。
      bits.push(`${hhmm(t.delegate.next_at)} ${t.delegate.next_name || "委托"}`);
    } else {
      // 后端只给未来的时间点：有启用的委托却拿不到 next_at，说明调度算出的
      // 时间已经是过去式（worker 停过）。这时不能编一个时间，只能说清数量。
      bits.push(`${t.delegate.enabled} 个委托已开启`);
    }
  }
  if (t.checkin.streak > 0 && !t.checkin.done_today) {
    bits.push(`已经连续 ${t.checkin.streak} 天`);
  }

  if (cetLeft > 0) {
    return {
      main: `六级今天还剩 ${cetLeft} 个任务`,
      // 副句只放「下一个任务」本身。截图实测：390 宽下可用宽度约 258px，
      // 任务名 + 委托时间拼起来必然被截在「·」之后（「22:30 睡…」），
      // 而半截的时间是纯噪声 —— 不如把整行让给任务名，委托留给其它分支显示。
      sub: t.cet.next_task || bits.slice(0, 2).join(" · "),
      href: "/cet",
      cta: "去做",
    };
  }
  if (!t.checkin.done_today) {
    return {
      main: "今天还没打卡",
      sub: bits.slice(0, 2).join(" · ") || "把今天问过的，压成一段总结",
      href: "/checkin",
      cta: "去打卡",
    };
  }
  if (t.delegate.enabled > 0) {
    return {
      main: t.delegate.next_at
        ? `下次 ${hhmm(t.delegate.next_at)}，知穗去做${t.delegate.next_name ? `「${t.delegate.next_name}」` : "委托"}`
        : `${t.delegate.enabled} 个委托已开启，到点知穗自己去做`,
      sub: bits.slice(0, 2).join(" · "),
      href: "/delegate",
      cta: "看委托",
    };
  }
  return {
    main: "今天的都做完了",
    sub: bits.slice(0, 2).join(" · "),
    href: "/report",
    cta: "看总结",
  };
}

export default function TodayStrip({
  enabled,
  onData,
}: {
  enabled: boolean;
  /** 告诉父组件"今天条到底有没有出现"。
   *
   *  「每日提醒」的入口要排在今天条下面：今天条只在**有事要做**时出现，
   *  而那正好就是推送有意义的人群。没有这条回调，父组件只能自己也请求一次
   *  /api/today 才知道该不该渲染，白多一个请求还可能与这条不同步。 */
  onData?: (hasAny: boolean) => void;
}) {
  const [snap, setSnap] = useState<TodaySnapshot | null>(null);
  // 回调放 ref：父组件每次渲染都会传一个新函数，进依赖会让 effect 反复重跑（重复请求）
  const onDataRef = useRef(onData);
  useEffect(() => {
    onDataRef.current = onData;
  });

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    // 失败就保持不渲染：首屏是最不该出现报错条的地方。
    // 接口没上线时 tryRequest 会返回 null，与"真的没有数据"同样处理。
    api
      .today()
      .then((r) => {
        if (!alive) return;
        setSnap(r);
        onDataRef.current?.(!!r?.has_any);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [enabled]);

  if (!enabled || !snap || !snap.has_any) return null;

  const v = describeToday(snap);
  return (
    <section className="today-strip" aria-label="今天">
      <span className="ts-mark" aria-hidden="true">
        {/* 日出：一件"今天"的事，比日历更贴语义 */}
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
          <path d="M3 18h18" />
          <path d="M6.5 18a5.5 5.5 0 0 1 11 0" />
          <path d="M12 4v2.5M4.9 7.4l1.7 1.7M19.1 7.4l-1.7 1.7" />
        </svg>
      </span>
      <div className="ts-body">
        <p className="ts-main">{v.main}</p>
        {v.sub ? <p className="ts-sub">{v.sub}</p> : null}
      </div>
      <Link href={v.href} className="ts-go">
        {v.cta}
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
          <path d="M9 6l6 6-6 6" />
        </svg>
      </Link>
    </section>
  );
}
