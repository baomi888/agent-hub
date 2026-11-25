"use client";

/**
 * 截图框选层。
 *
 * 浏览器没有「调用系统截图工具并拿回选区」的接口：getDisplayMedia 只能拿到
 * 整帧画面，而且必须让用户先选标签页 / 窗口 / 整个屏幕。所以这里在拿到整帧后
 * 再铺一层全屏画布，让用户拖一个框，把框里的像素裁出来当附件。
 *
 * 用 portal 挂到 body：对话区有 transform / overflow 的祖先，弹层留在组件树里
 * 会被裁掉（代码块全屏那次已经踩过）。
 */

import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { createPortal } from "react-dom";

export type CropRect = { x: number; y: number; w: number; h: number };

type Point = { x: number; y: number };
type Display = { w: number; h: number; scale: number };

type Props = {
  imageUrl: string;
  width: number;
  height: number;
  onConfirm: (rect: CropRect) => void;
  onCancel: () => void;
};

const clamp = (v: number, min: number, max: number) => Math.max(min, Math.min(max, v));

export default function ScreenshotCropper({ imageUrl, width, height, onConfirm, onCancel }: Props) {
  const stageRef = useRef<HTMLDivElement | null>(null);
  const imageBoxRef = useRef<HTMLDivElement | null>(null);
  const startRef = useRef<Point | null>(null);
  const [display, setDisplay] = useState<Display | null>(null);
  /** 框选坐标是「屏幕上显示的大小」，确认时才换算回原始像素 */
  const [box, setBox] = useState<CropRect | null>(null);

  // 画面要按容器等比缩小。用 ResizeObserver 而不是只算一次：
  // 横竖屏切换 / 侧栏展开都会改变可用区域，重新量才不会让框和像素错位。
  const fit = useCallback(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const r = stage.getBoundingClientRect();
    const ratio = Math.min((r.width - 28) / width, (r.height - 28) / height, 1);
    if (!(ratio > 0)) return;
    const w = Math.max(1, Math.floor(width * ratio));
    const h = Math.max(1, Math.floor(height * ratio));
    setDisplay({ w, h, scale: width / w });
  }, [width, height]);

  useEffect(() => {
    fit();
    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => fit());
    if (stageRef.current && ro) ro.observe(stageRef.current);
    window.addEventListener("resize", fit);
    return () => {
      ro?.disconnect();
      window.removeEventListener("resize", fit);
    };
  }, [fit]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  const pointOf = useCallback((e: ReactPointerEvent<HTMLDivElement>): Point => {
    const r = imageBoxRef.current?.getBoundingClientRect();
    if (!r) return { x: 0, y: 0 };
    return {
      x: clamp(e.clientX - r.left, 0, r.width),
      y: clamp(e.clientY - r.top, 0, r.height),
    };
  }, []);

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    const p = pointOf(e);
    startRef.current = p;
    setBox({ x: p.x, y: p.y, w: 0, h: 0 });
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      // 某些浏览器在 pointerdown 阶段不给 capture，拖出边界后仍能在框内结束
    }
  };

  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const start = startRef.current;
    if (!start) return;
    const p = pointOf(e);
    setBox({
      x: Math.min(start.x, p.x),
      y: Math.min(start.y, p.y),
      w: Math.abs(p.x - start.x),
      h: Math.abs(p.y - start.y),
    });
  };

  const onPointerEnd = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!startRef.current) return;
    onPointerMove(e);
    startRef.current = null;
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      // 已经自动释放过就不用管
    }
  };

  const sourceRect: CropRect | null =
    display && box && box.w >= 6 && box.h >= 6
      ? {
          x: clamp(Math.round(box.x * display.scale), 0, width - 1),
          y: clamp(Math.round(box.y * display.scale), 0, height - 1),
          w: 0,
          h: 0,
        }
      : null;
  if (sourceRect && display && box) {
    sourceRect.w = clamp(Math.round(box.w * display.scale), 1, width - sourceRect.x);
    sourceRect.h = clamp(Math.round(box.h * display.scale), 1, height - sourceRect.y);
  }

  if (typeof document === "undefined") return null;

  return createPortal(
    <div className="shot-crop-mask" role="dialog" aria-modal="true" aria-labelledby="shot-crop-title">
      <div className="shot-crop-card">
        <header className="shot-crop-head">
          <div>
            <h2 id="shot-crop-title">选择要发送的区域</h2>
            <p>在画面上拖一个框；不框选就整张发送</p>
          </div>
          <button type="button" className="shot-crop-close" aria-label="关闭截图框选" onClick={onCancel}>
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </header>

        <div className="shot-crop-stage" ref={stageRef}>
          <div
            ref={imageBoxRef}
            className="shot-crop-image-box"
            style={display ? { width: display.w, height: display.h } : undefined}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerEnd}
            onPointerCancel={onPointerEnd}
          >
            {/* eslint-disable-next-line @next/next/no-img-element -- 用户截取的 dataURL，不是站点资源 */}
            <img src={imageUrl} alt="截取的画面" draggable={false} />
            {box && box.w > 0 && box.h > 0 ? (
              <div
                className="shot-crop-selection"
                style={{ left: box.x, top: box.y, width: box.w, height: box.h }}
              >
                {sourceRect ? (
                  <span className="shot-crop-size">
                    {sourceRect.w} × {sourceRect.h}
                  </span>
                ) : null}
              </div>
            ) : null}
          </div>
        </div>

        <footer className="shot-crop-actions">
          <button type="button" className="shot-crop-ghost" onClick={onCancel}>
            取消
          </button>
          <span className="shot-crop-tip">拖框选定一块，或直接整张用</span>
          <button
            type="button"
            className="shot-crop-ghost"
            onClick={() => onConfirm({ x: 0, y: 0, w: width, h: height })}
          >
            整张使用
          </button>
          <button
            type="button"
            className="shot-crop-primary"
            disabled={!sourceRect}
            onClick={() => sourceRect && onConfirm(sourceRect)}
          >
            用这一块
          </button>
        </footer>
      </div>
    </div>,
    document.body,
  );
}
