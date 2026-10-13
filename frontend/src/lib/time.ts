// 时间格式化。四屏页面要显示得比工作台细（下次触发、执行历史、总结日期），
// 所以单独收一处，避免每个页面各写一遍分叉的实现。

/** 后端时间戳有秒也有毫秒，统一成毫秒再格式化 */
export function toMs(ts: number): number {
  return ts < 1e12 ? ts * 1000 : ts;
}

const pad = (n: number) => String(n).padStart(2, "0");

/** 2026-10-13 */
export function fmtDate(d: Date | number): string {
  const x = typeof d === "number" ? new Date(toMs(d)) : d;
  return `${x.getFullYear()}-${pad(x.getMonth() + 1)}-${pad(x.getDate())}`;
}

/** 21:00 */
export function fmtHM(d: Date | number): string {
  const x = typeof d === "number" ? new Date(toMs(d)) : d;
  return `${pad(x.getHours())}:${pad(x.getMinutes())}`;
}

/**
 * 「今天 21:00」「明天 07:30」「10-11 21:00」——
 * 委托列表的「下次触发」用它。带日期的相对说法比裸时间戳好读得多，
 * 而且能一眼看出「它到底还会不会来」。
 */
export function fmtWhen(d: Date | number, now = new Date()): string {
  const x = typeof d === "number" ? new Date(toMs(d)) : d;
  const dayKey = (v: Date) => `${v.getFullYear()}-${v.getMonth()}-${v.getDate()}`;
  const diffDays = Math.round(
    (new Date(dayKey(x)).getTime() - new Date(dayKey(now)).getTime()) / 86400000,
  );
  // 刚发生过的事说「刚刚」：跨零点时（00:05 看 23:58 生成的）说「昨天 23:58」
  // 会让人以为隔了很久。
  //
  // ⚠️ 下界 0 不能省：这是「未来时间戳」——委托列表的「下次触发」就是未来时刻，
  //    now - x 是负数，只写上界的话「今晚 21:00 触发」会被整条吞进这里显示成「刚刚」，
  //    和紧跟着的「还有 20 小时 45 分」自相矛盾（2026-10-13 实机发现）。
  const delta = now.getTime() - x.getTime();
  if (diffDays === 0 && delta >= 0 && delta < 3 * 60_000) return "刚刚";
  if (diffDays === 0) return `今天 ${fmtHM(x)}`;
  if (diffDays === 1) return `明天 ${fmtHM(x)}`;
  if (diffDays === -1) return `昨天 ${fmtHM(x)}`;
  return `${pad(x.getMonth() + 1)}-${pad(x.getDate())} ${fmtHM(x)}`;
}

/** 「还有 2 小时 14 分」——比给一个绝对时刻更能回答「我等不等得起」 */
export function fmtRemaining(target: Date | number, now = new Date()): string {
  const x = typeof target === "number" ? new Date(toMs(target)) : target;
  const mins = Math.round((x.getTime() - now.getTime()) / 60000);
  if (mins <= 0) return "即将触发";
  if (mins < 60) return `还有 ${mins} 分钟`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  if (h < 24) return m ? `还有 ${h} 小时 ${m} 分` : `还有 ${h} 小时`;
  return `还有 ${Math.floor(h / 24)} 天`;
}

/** 「672 ms」/「1.2 s」 */
export function fmtDuration(started: number | null | undefined, finished: number | null | undefined): string | null {
  if (!started || !finished) return null;
  const ms = toMs(finished) - toMs(started);
  if (ms < 0) return null;
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

/** 1234 → 「1,204」 */
export function fmtNum(n: number): string {
  return n.toLocaleString("en-US");
}
