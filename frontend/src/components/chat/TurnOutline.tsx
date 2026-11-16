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
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type RefObject,
} from "react";

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

/* ---------- 手机目录页签：可拖动，只停左右边缘 ----------
 *
 * 为什么位置要自己算：它原来靠 CSS 的 `right:0 + top:50%` 定位，而
 * touch.css（globals.css 里排在 turnnav.css **之后**）为了扩触控热区给一批
 * 元素写了 `position: relative`，`.turn-fab` 也在名单里 —— absolute 被覆盖后
 * 这枚页签掉进文档流、`right` 失效、`top:50%` 又把它推下去，于是它贴在左下角
 * 压住「复制 / 检索」（2026-11-16 手机截图现场）。现在 CSS 用 fixed，
 * 横纵坐标全在这里算：
 *   · 横向只有两个合法值（贴左 / 贴右），拖动过程中也不许进中间正文区；
 *   · 纵向关在活动带里 —— 上沿 12px，下沿取输入区 `.chat-dock` 顶边再留 10px，
 *     所以既不会被拖出屏幕，也不会盖住输入框与底栏；
 *   · 位置按「活动带内的比例」存 localStorage，换屏高/横竖屏后仍然合理。
 */
const FAB_POS_KEY = "baomi.turnFab.pos";

interface FabPos {
  side: "left" | "right";
  /** 0 = 活动带上沿，1 = 下沿 */
  ratio: number;
}

const DEFAULT_FAB: FabPos = { side: "right", ratio: 0.4 };

/** 判定「这是拖动还是点了一下」的位移阈值：手指点按本身就会漂 1~2px */
const DRAG_SLOP = 6;

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

  // 手机页签的位置与拖动状态
  const [fab, setFab] = useState<FabPos>(DEFAULT_FAB);
  const [dragging, setDragging] = useState(false);
  // 活动带：bottom 是先验 0，量到之前不写内联 top（退回 CSS 的 38%）
  const [band, setBand] = useState({ top: 12, bottom: 0 });
  const fabRef = useRef<HTMLButtonElement>(null);
  const dragRef = useRef<{
    id: number;
    startY: number;
    baseY: number;
    moved: boolean;
    lastY: number;
    lastSide: FabPos["side"];
  } | null>(null);
  // 拖完浏览器照样会补一个 click，别让它顺手把目录弹出来
  const skipClick = useRef(false);

  const measureBand = useCallback(() => {
    const h = fabRef.current?.offsetHeight ?? 56;
    const dock = document.querySelector<HTMLElement>(".chat-dock");
    const dockTop = dock ? dock.getBoundingClientRect().top : 0;
    const vh = window.innerHeight;
    // 只有「确实贴在页面下半截」的 dock 才可信：
    //   · 0 = 还没挂载；
    //   · 被公告弹层之类挤到中间（< 45% 屏高）时按当前高度算，页签会跟着往上跳。
    // 不可信就退回"视口高 - 100"，这个值在 390×844 上落在安全区内。
    const usable = dockTop > vh * 0.45 && dockTop < vh;
    const limit = (usable ? dockTop : vh - 100) - 10 - h;
    setBand({ top: 12, bottom: Math.max(12, limit) });
  }, []);

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

  // 恢复上次拖到的位置（按比例存，换屏高也说得通）
  useEffect(() => {
    try {
      const raw = localStorage.getItem(FAB_POS_KEY);
      if (raw) {
        const v = JSON.parse(raw) as Partial<FabPos>;
        const sideOk = v.side === "left" || v.side === "right";
        const ratioOk = typeof v.ratio === "number" && Number.isFinite(v.ratio);
        if (sideOk && ratioOk) {
          setFab({
            side: v.side as FabPos["side"],
            ratio: Math.min(1, Math.max(0, v.ratio as number)),
          });
        }
      }
    } catch {
      // 隐私模式禁了 storage / 存量值是坏的：退回默认位置，功能不受影响
    }
    measureBand();
  }, [measureBand]);

  // 屏高变了（旋转、键盘弹出、地址栏收放）要重新划活动带，否则页签会停在带外
  useEffect(() => {
    const onResize = () => measureBand();
    window.addEventListener("resize", onResize);
    window.addEventListener("orientationchange", onResize);
    const vv = window.visualViewport;
    vv?.addEventListener("resize", onResize);
    // 输入框会随多行输入长高 —— 只看 window.resize 会漏掉，页签会被它顶住
    const dock = document.querySelector<HTMLElement>(".chat-dock");
    const ro = dock ? new ResizeObserver(() => measureBand()) : null;
    if (ro && dock) ro.observe(dock);
    return () => {
      window.removeEventListener("resize", onResize);
      window.removeEventListener("orientationchange", onResize);
      vv?.removeEventListener("resize", onResize);
      ro?.disconnect();
    };
  }, [measureBand]);

  const show = canNav && turns.length >= 3;

  // 把「页签停在哪一侧」同步到根元素，让消息区把那一侧的槽位让出来
  // （chat.css 的 --chat-fab-slot）。三件事必须一起判：
  //   · show=false（问答不足 3 轮）时页签不出现 → 属性清空，别白留 28px；
  //   · 桌面（≥900）页签换成页边刻度 → 同上；
  //   · 拖动过程中不同步：一路改会让正文跟着跳，手感很乱，松手才让位。
  // ★ 读的是同一次渲染的 show，所以只能写在它后面（提到上面的 effect 堆里会 TDZ）。
  useEffect(() => {
    if (dragging) return;
    document.documentElement.dataset.fabSide = show ? fab.side : "";
  }, [fab.side, dragging, show]);
  useEffect(() => {
    return () => {
      delete document.documentElement.dataset.fabSide;
    };
  }, []);

  const tipTop = trackH ? Math.min(Math.max(hover?.y ?? 0, 34), trackH - 34) : 0;
  // ★ 不 return null：rail 元素得先存在才量得到高度（canNav 靠它算出来的）。
  //   藏起来用 is-off（opacity+pointer-events），不用 display:none —— 后者量到 0。
  const off = show ? "" : " is-off";

  // 量到活动带之前不写内联 top，退回 CSS 里的默认（右缘 38%），免得闪一下
  const ready = band.bottom > band.top;
  const fabY = ready ? band.top + fab.ratio * (band.bottom - band.top) : 0;

  const onFabDown = (e: ReactPointerEvent<HTMLButtonElement>) => {
    const el = fabRef.current;
    if (!el || !ready) return;
    dragRef.current = {
      id: e.pointerId,
      startY: e.clientY,
      baseY: fabY,
      moved: false,
      lastY: fabY,
      lastSide: fab.side,
    };
    try {
      el.setPointerCapture(e.pointerId);
    } catch {
      // 少数老 WebView 没有 capture：下面每个 move 都校验 pointerId，照样能拖
    }
    setDragging(true);
  };

  const onFabMove = (e: ReactPointerEvent<HTMLButtonElement>) => {
    const d = dragRef.current;
    if (!d || d.id !== e.pointerId) return;
    const dy = e.clientY - d.startY;
    if (!d.moved) {
      if (Math.abs(dy) < DRAG_SLOP) return;
      d.moved = true;
    }
    // 纵向关在带内；横向只认"手指在屏幕哪半边"——中间正文区永远不是落点
    const y = Math.min(Math.max(d.baseY + dy, band.top), band.bottom);
    const side: FabPos["side"] = e.clientX < window.innerWidth / 2 ? "left" : "right";
    d.lastY = y;
    d.lastSide = side;
    setFab({ side, ratio: (y - band.top) / (band.bottom - band.top) });
  };

  const onFabUp = (e: ReactPointerEvent<HTMLButtonElement>) => {
    const d = dragRef.current;
    if (!d || d.id !== e.pointerId) return;
    dragRef.current = null;
    setDragging(false);
    if (!d.moved) return; // 只是点了一下 → 交给 onClick 打开目录
    skipClick.current = true;
    // 拖完浏览器通常还会补一个 click；但万一没补，也必须复位，
    // 否则 skipClick 一直挂着，下一次正常点击会被吞掉
    window.setTimeout(() => {
      skipClick.current = false;
    }, 320);
    try {
      const ratio = (d.lastY - band.top) / (band.bottom - band.top);
      localStorage.setItem(FAB_POS_KEY, JSON.stringify({ side: d.lastSide, ratio }));
    } catch {
      // 存不下就只在本次会话里有效
    }
  };

  const onFabCancel = () => {
    dragRef.current = null;
    setDragging(false);
  };

  const openSheet = () => {
    if (skipClick.current) {
      skipClick.current = false;
      return;
    }
    setSheet(true);
  };

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

      {/* 手机：常驻页签，可拖到左/右边缘。问数直接写在上面 —— 它既是入口
          也是一句提示：数字会变，用户迟早会问"这个 12 是什么"。
          位置固定贴边、只在两侧轨道里移动，正文区永远碰不到。 */}
      <button
        type="button"
        ref={fabRef}
        className={`turn-fab${off}${fab.side === "left" ? " turn-fab--left" : ""}${
          dragging ? " is-dragging" : ""
        }`}
        style={ready ? { top: Math.round(fabY) } : undefined}
        onClick={openSheet}
        onPointerDown={onFabDown}
        onPointerMove={onFabMove}
        onPointerUp={onFabUp}
        onPointerCancel={onFabCancel}
        title="拖动可换到左/右边缘，点击打开目录"
        aria-haspopup="dialog"
        aria-expanded={sheet}
        aria-label={`打开对话目录，共 ${turns.length} 问（可拖到左/右边缘）`}
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
