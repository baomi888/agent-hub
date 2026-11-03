"use client";

// 对话目录：桌面 = 右侧页边刻度；手机 = 右下胶囊 + 半屏目录。
//
// 解决的问题：一轮对话几十问之后，想回看前面某一问只能一路往上翻。
// 刻度按「这一问在全文里的相对位置」排，所以它同时是一张密度图 ——
// 哪一段问答特别长，一眼就能看出来。
//
// 为什么不做成左侧列表：桌面左栏已经给了会话列表，再塞一列会把正文压窄；
// 而页边轨不占任何宽度。手机没有 hover，悬停卡片那套用不了，
// 所以换成一枚常驻胶囊（带问数，本身就是提示）+ 半屏目录。
import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

export interface TurnItem {
  mid: string;
  n: number;
  text: string;
}

interface Props {
  turns: TurnItem[];
  scrollRef: RefObject<HTMLDivElement | null>;
  onJump: (mid: string) => void;
  /** 内容签名（条数:末条字数）：变了就重新量刻度，流式期间靠它跟上 */
  layoutKey: string;
}

/** 相邻刻度的最小间距：挤成一坨就点不准了 */
const MIN_GAP = 7;

interface Tick extends TurnItem {
  y: number;
  /** 该问在滚动内容里的绝对偏移，用来算「现在读到第几问」 */
  top: number;
}

export default function TurnOutline({ turns, scrollRef, onJump, layoutKey }: Props) {
  const [ticks, setTicks] = useState<Tick[]>([]);
  const [activeN, setActiveN] = useState(1);
  const [hover, setHover] = useState<Tick | null>(null);
  const [sheet, setSheet] = useState(false);
  const [trackH, setTrackH] = useState(0);
  // 内容不足一屏时没有「跳回去」可言，整套入口都不出现
  const [canNav, setCanNav] = useState(false);
  const railRef = useRef<HTMLDivElement>(null);
  const lastAt = useRef(0);

  const measure = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const avail = el.scrollHeight - el.clientHeight;
    setCanNav(avail >= 80);
    if (avail < 80) {
      setTicks([]);
      return;
    }
    // ★ 高度取 rail，取不到就退回视口高 —— 手机端 rail 是 display:none（clientHeight=0），
    //   若在这里 return，canNav 永远上不去，整套入口都不会出现。
    //   刻度位置只有桌面用得上，退回去只是为了别把状态卡死。
    const rail = railRef.current;
    const h = rail?.clientHeight || el.clientHeight;
    setTrackH(h);
    const base = el.getBoundingClientRect().top;
    const next: Tick[] = [];
    let prevY = -MIN_GAP;
    for (const t of turns) {
      const node = el.querySelector<HTMLElement>(`[data-mid="${t.mid}"]`);
      if (!node) continue;
      // 一律用「相对滚动内容的绝对偏移」：scrolling 时 getBoundingClientRect 会跟着变，
      // 加回 scrollTop 才是稳定坐标
      const top = node.getBoundingClientRect().top - base + el.scrollTop;
      let y = Math.round(Math.max(0, Math.min(1, top / avail)) * (h - 8)) + 4;
      if (y - prevY < MIN_GAP) y = prevY + MIN_GAP;
      if (y > h - 4) y = h - 4;
      prevY = y;
      next.push({ ...t, y, top });
    }
    setTicks(next);
    // 「现在读到第几问」= 顶边已经越过视口上方 60px 的最后一问
    const line = el.scrollTop + 60;
    let cur = next[0]?.n ?? 1;
    for (const t of next) if (t.top <= line) cur = t.n;
    setActiveN(cur);
    if (typeof window !== "undefined") {
      // 与 ChatArea 的 __anchorDbg 同一种做法：量位置的东西出错时，
      // 只看"高亮错了"没法定位是偏移错了还是阈值错了
      (window as unknown as Record<string, unknown>).__turnDbg = {
        line: Math.round(line),
        tops: next.map((t) => Math.round(t.top)),
        cur,
      };
    }
  }, [scrollRef, turns]);

  // 内容变了就重量。★ 防抖 120ms：逐 token 量十几个节点会把手机拖卡；
  //   刻度滞后一百毫秒，肉眼看不出来。
  useEffect(() => {
    const t = window.setTimeout(measure, 120);
    return () => window.clearTimeout(t);
  }, [measure, layoutKey]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    let raf = 0;
    let timer = 0;
    // 滚动与「内容长高」共用同一个节流入口：刻度是按绝对偏移算的，
    // 内容一变（字体加载完 / 图片落地 / 流式追加）旧偏移就作废了。
    // ★ 只监听 scroll 不够：字体加载完页面会整体变高，但 scrollTop 不动，
    //   一个 scroll 事件都不会有 —— 实测高亮停在上一问，和正文对不上。
    // ★ 节流必须是「补一次」，不能「丢掉」：早先写成超时就 return，
    //   最后一次事件被吞掉，刻度就永远停在旧偏移上（上面那条就是这样漏的）。
    const poke = () => {
      if (raf || timer) return;
      const wait = Math.max(0, 90 - (performance.now() - lastAt.current));
      timer = window.setTimeout(() => {
        timer = 0;
        lastAt.current = performance.now();
        // ★ raf 句柄要在帧里清掉：不清的话它永远非空，poke 第一行就 return，
        //   之后所有滚动都不再触发测量 —— 高亮会永久停在第一次算出来的那一问。
        raf = requestAnimationFrame(() => {
          raf = 0;
          measure();
        });
      }, wait);
    };
    el.addEventListener("scroll", poke, { passive: true });
    const content = el.firstElementChild;
    const ro = content ? new ResizeObserver(poke) : null;
    if (ro && content) ro.observe(content);
    window.addEventListener("resize", poke);
    return () => {
      el.removeEventListener("scroll", poke);
      window.removeEventListener("resize", poke);
      ro?.disconnect();
      if (raf) cancelAnimationFrame(raf);
      if (timer) window.clearTimeout(timer);
    };
  }, [measure, scrollRef]);

  // 半屏目录：Esc 关闭
  useEffect(() => {
    if (!sheet) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      setSheet(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [sheet]);

  const show = canNav && turns.length >= 3;
  const tipTop = trackH ? Math.min(Math.max(hover?.y ?? 0, 34), trackH - 34) : 0;
  // ★ 不 return null：rail 元素得先存在才量得到高度（canNav 靠它算出来的）。
  //   藏起来用 is-off（opacity+pointer-events），不用 display:none —— 后者量到 0。
  const off = show ? "" : " is-off";

  return (
    <>
      <div
        className={`turn-rail${off}`}
        ref={railRef}
        role="navigation"
        aria-label="对话目录"
        title="对话目录：点刻度跳回任意一问"
      >
        {ticks.map((t) => (
          <button
            key={t.mid}
            type="button"
            className={`turn-tick${t.n === activeN ? " is-active" : ""}`}
            style={{ top: t.y - 8 }}
            aria-label={`跳到第 ${t.n} 问：${t.text.slice(0, 30)}`}
            aria-current={t.n === activeN ? "true" : undefined}
            onMouseEnter={() => setHover(t)}
            onMouseLeave={() => setHover(null)}
            onFocus={() => setHover(t)}
            onBlur={() => setHover(null)}
            onClick={() => onJump(t.mid)}
          />
        ))}
        {hover && (
          <div className="turn-tip" style={{ top: tipTop }} aria-hidden="true">
            <span className="turn-tip-n">第 {hover.n} 问</span>
            <span className="turn-tip-t">{hover.text}</span>
          </div>
        )}
      </div>

      {/* 手机：常驻胶囊。问数直接写在上面 —— 它既是入口也是一句提示：
          数字会变，用户迟早会问"这个 12 是什么" */}
      <button
        type="button"
        className={`turn-fab${off}`}
        onClick={() => setSheet(true)}
        aria-haspopup="dialog"
        aria-expanded={sheet}
        aria-label={`打开对话目录，共 ${turns.length} 问`}
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M8 6h13M8 12h13M8 18h13" />
          <path d="M3.5 6h.01M3.5 12h.01M3.5 18h.01" />
        </svg>
        <span className="turn-fab-n">{turns.length}</span>
      </button>

      {show && sheet && (
        <div className="turn-sheet-wrap">
          <div className="turn-sheet-backdrop" onClick={() => setSheet(false)} aria-hidden="true" />
          <div className="turn-sheet" role="dialog" aria-modal="true" aria-label="对话目录">
            <div className="turn-sheet-grab" aria-hidden="true" />
            <div className="turn-sheet-head">
              <span className="eyebrow">本次对话 · {turns.length} 问</span>
              <button
                type="button"
                className="turn-sheet-x"
                onClick={() => setSheet(false)}
                aria-label="关闭目录"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" aria-hidden="true">
                  <path d="M6 6l12 12M18 6L6 18" />
                </svg>
              </button>
            </div>
            <ul className="turn-sheet-list">
              {turns.map((t) => (
                <li key={t.mid}>
                  <button
                    type="button"
                    className={`turn-sheet-item${t.n === activeN ? " is-active" : ""}`}
                    onClick={() => {
                      setSheet(false);
                      onJump(t.mid);
                    }}
                  >
                    <span className="tsi-n">{t.n}</span>
                    <span className="tsi-t">{t.text || "（附件提问）"}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </>
  );
}
