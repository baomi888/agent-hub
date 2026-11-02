"use client";

/**
 * 更新公告弹窗（全屏强制版）。
 *
 * 形态：铺满视口的一整页纸，不是"卡片 + 半透明遮罩"。
 * 为什么不做成遮罩上浮一张卡：这是**每期都强制**的（产品决定，不是这一期的特例），
 * 用户没有关掉的途径，那就没必要再露出背后的界面——留一层能看见却点不动的背景，
 * 只会让人反复去点它。做成整页，读起来就是"更新说明"这件事本身，而不是"挡路的弹窗"。
 *
 * ★ 每期强制的代价，写在这里给以后的人看：用户没有"稍后再说"的选项，
 *   所以公告必须一眼能扫完（现在约 5 条正文 + 7 条细修，手机上四五屏）。
 *   如果哪一期内容涨到需要读五分钟，这条路就走不通了 —— 那时该改成
 *   "首屏强制 + 其余折叠"，而不是继续往这篇里加字。
 *
 * 强制的边界（每条都想清楚了才写）：
 *  ① 没有关闭按钮、点空白不关、Esc 在读到底之前无效。
 *  ② 但**滑到底就一定能关**：解锁条件是「滚到内容底部」或「内容不足一屏」，
 *     两条都满足不了的情况不存在。不留死路，是因为用户被彻底困住时
 *     唯一的出路是关标签页，那就连"下次再弹"的机会都没有了。
 *  ③ 解锁之后不再回锁。往回滚一点又给锁上，是拿用户的手势惩罚用户。
 *  ④ 内容不足一屏（大屏常见）直接解锁，否则会出现"没东西可滚、按钮永远灰着"。
 *
 * 和 GuideModal 的关系：
 *   新用户进来会先撞上四步上手说明。两个都弹会叠成两层，所以这里读一次
 *   `baomi.guide.v1.<uid>`——**挂载那一刻**没读完就先不弹，顺延到下次进入。
 *   用挂载时的快照而不是实时值：用户在这一次会话里关掉引导的瞬间，
 *   实时值会变，公告会紧接着弹出来，等于连着被拦两次。
 *
 * 已读判定复用 useDismissed，key 带期号：换一期就换一个 key，所有人重新看到。
 */

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { useDismissed } from "@/lib/hooks/useDismissed";
import { findAnnouncement, latestAnnouncement } from "@/lib/announcements";
import CornIcon from "@/components/CornIcon";
import Rich from "./Rich";

/** 引导的已读键，和 GuideModal 里的 KEY 必须是同一个（改了要一起改） */
const GUIDE_KEY = "baomi.guide.v1";
const ANNOUNCE_KEY = "baomi.announce.v1";

/** 触底判定的容差。留 4px 是因为手机端 scrollTop 常带小数，严格相等永远不成立 */
const BOTTOM_EPS = 4;

const subscribe = () => () => {};
const mountedSnapshot = () => true;
const serverSnapshot = () => false;

interface Props {
  /** 当前登录用户 id；未登录不弹（记不住已读，会每次进来都弹） */
  userId?: string | null;
}

export default function AnnounceModal({ userId = null }: Props) {
  const mounted = useSyncExternalStore(subscribe, mountedSnapshot, serverSnapshot);

  /**
   * `?ann=<id>` 强制显示某一期，忽略已读。
   * 走 URL 而不是 prop：这个组件挂在两个地方（首页 / 控制台），
   * 走 prop 就要在两处各开一条参数通道；而"我想再看看那次公告"这种需求
   * 本来就该能靠一个链接复现。`?ann=1` 等价于最新一期。
   */
  const [debug] = useState(() => {
    if (typeof window === "undefined") return "";
    return new URLSearchParams(window.location.search).get("ann") ?? "";
  });

  const ann = (debug && findAnnouncement(debug)) || latestAnnouncement();
  const isDebug = !!debug && !!ann;

  /**
   * 挂载那一刻引导读完没有。
   * 惰性初始值在客户端首次渲染时求值；SSR 阶段没有 window，返回 true，
   * 但那时 `mounted` 是 false、整块不渲染，不会造成水合不一致。
   */
  const [guideDone] = useState(() => {
    if (typeof window === "undefined") return true;
    try {
      return localStorage.getItem(`${GUIDE_KEY}.${userId ?? ""}`) === "1";
    } catch {
      return true; // 隐私模式读不到：放行公告，别把功能锁死在存储上
    }
  });

  const key = `${ANNOUNCE_KEY}.${userId ?? ""}.${ann?.id ?? ""}`;
  const [dismissed, dismiss] = useDismissed(key);

  const [unlocked, setUnlocked] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);
  const barRef = useRef<HTMLDivElement>(null);
  const openerRef = useRef<Element | null>(null);

  /**
   * 什么时候弹。
   * · `?ann=` 走预览：**不需要登录**。预览是拿来给没账号的人看版式的，
   *   一上来先撞登录墙、再撞后端 500，等于看不到。
   * · 正常路径仍要求登录：没有 uid 就记不住已读，未登录用户每次进来都会被拦一次。
   */
  const open = mounted && !!ann && (isDebug || (!!userId && !dismissed && guideDone));

  /* 触底检测。只在没解锁时跑，解锁后立刻摘掉监听，别让滚动每帧都进这里。 */
  const check = useCallback(() => {
    const el = bodyRef.current;
    if (!el) return;
    const max = el.scrollHeight - el.clientHeight;
    if (max <= BOTTOM_EPS) {
      setUnlocked(true);
      if (barRef.current) barRef.current.style.transform = "scaleX(1)";
      return;
    }
    const at = Math.min(1, Math.max(0, el.scrollTop / max));
    // 进度条直接写 style：滚一下 setState 一次会把整篇公告重渲染一遍
    if (barRef.current) barRef.current.style.transform = `scaleX(${at})`;
    if (el.scrollTop >= max - BOTTOM_EPS) setUnlocked(true);
  }, []);

  useEffect(() => {
    if (!open || unlocked) return;
    const el = bodyRef.current;
    if (!el) return;
    // ResizeObserver 负责首次与"字体加载完高度又变了"：scroll 事件覆盖不到这种情况
    const ro = new ResizeObserver(check);
    ro.observe(el);
    if (el.firstElementChild) ro.observe(el.firstElementChild);
    el.addEventListener("scroll", check, { passive: true });
    return () => {
      ro.disconnect();
      el.removeEventListener("scroll", check);
    };
  }, [open, unlocked, check]);

  /* 焦点：进来落在内容区（键盘可直接 PageDown 读），出去还给原来的元素 */
  useEffect(() => {
    if (!open) return;
    openerRef.current = document.activeElement;
    bodyRef.current?.focus({ preventScroll: true });
    const onFocusIn = (e: FocusEvent) => {
      const root = bodyRef.current?.parentElement;
      if (!root) return;
      const t = e.target as Element | null;
      if (!t || root.contains(t)) return;
      // 和 GuideModal 同一条规矩：焦点在别的模态里就不抢。
      // 两边都无条件"拉回来"会互相抢成死循环（见 GuideModal 那处注释）
      if (t.closest?.('[role="dialog"][aria-modal="true"]')) return;
      bodyRef.current?.focus({ preventScroll: true });
    };
    document.addEventListener("focusin", onFocusIn);
    return () => {
      document.removeEventListener("focusin", onFocusIn);
      if (openerRef.current instanceof HTMLElement) openerRef.current.focus?.();
    };
  }, [open]);

  /* Esc：解锁后才认。没解锁时按 Esc 什么也不发生（不是"报错提示"，而是彻底静默） */
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (!unlocked) {
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      dismiss();
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [open, unlocked, dismiss]);

  if (!open || !ann) return null;

  return createPortal(
    <div className="ann-page" role="presentation">
      <div
        className="ann-shell"
        role="dialog"
        aria-modal="true"
        aria-labelledby="ann-title"
        aria-describedby="ann-lead"
      >
        {/* 顶上一条细金线，读多少长多少 */}
        <div className="ann-progress" aria-hidden="true">
          <div className="ann-progress-bar" ref={barRef} />
        </div>

        <header className="ann-head">
          <span className="ann-eyebrow">
            {ann.label} · {ann.date}
          </span>
          <span className="ann-brand">
            <CornIcon name="corn" className="ann-brand-corn" />
            苞米地 · 知穗
          </span>
        </header>

        <div
          className="ann-body"
          ref={bodyRef}
          tabIndex={0}
          // 读屏把整段念出来，而不是让用户自己在页面里找
          aria-label={`${ann.title}，共 ${ann.changes.length} 条主要更新`}
        >
          <div className="ann-inner">
            <p className="ann-kicker">更新说明</p>
            <h1 id="ann-title" className="ann-title">
              {ann.title}
            </h1>
            {/* 摘要框带一枚自家玉米：整篇公告里唯一的图形元素，
                放在"先说最直接的"这句话旁边，既是品牌记号也是视觉锚点 */}
            <div className="ann-lead">
              <CornIcon name="corn" className="ann-lead-icon" />
              <p id="ann-lead" className="ann-lead-text">
                <Rich text={ann.lead} />
              </p>
            </div>

            <ol className="ann-list">
              {ann.changes.map((c) => (
                <li key={c.no} className="ann-item">
                  <span className="ann-no" aria-hidden="true">
                    {c.no}
                  </span>
                  <div className="ann-item-main">
                    <h2 className="ann-item-title">{c.title}</h2>
                    <p className="ann-item-body">
                      <Rich text={c.body} />
                    </p>
                    {c.points && c.points.length > 0 && (
                      <ul className="ann-points">
                        {c.points.map((p) => (
                          <li key={p}>
                            <Rich text={p} />
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </li>
              ))}
            </ol>

            {ann.minors && ann.minors.length > 0 && (
              <section className="ann-minor">
                <h3 className="ann-minor-title">还有些小地方</h3>
                <ul className="ann-minor-list">
                  {ann.minors.map((m) => (
                    <li key={m}>
                      <Rich text={m} />
                    </li>
                  ))}
                </ul>
              </section>
            )}

            <p className="ann-sign">
              有不顺手的，点输入框上面那枚「反馈」说两句就行。下一期改什么，很多就是从那儿来的。
            </p>
          </div>
        </div>

        <footer className="ann-foot">
          <p className="ann-hint" aria-live="polite">
            {unlocked ? (
              <>
                <span className="ann-hint-ok">已读完</span>
                <span>
                  共 {ann.changes.length} 条主要更新
                  {ann.minors?.length ? ` · ${ann.minors.length} 处细修` : ""}
                </span>
              </>
            ) : (
              <>
                <span className="ann-hint-arrow" aria-hidden="true">
                  ↓
                </span>
                <span>往下滑，读完就能进</span>
              </>
            )}
          </p>
          <button
            type="button"
            className={`btn btn-primary ann-go${unlocked ? "" : " is-locked"}`}
            disabled={!unlocked}
            aria-disabled={!unlocked}
            onClick={dismiss}
          >
            {unlocked ? "开始使用" : "还没读完"}
          </button>
        </footer>
      </div>
    </div>,
    document.body
  );
}
