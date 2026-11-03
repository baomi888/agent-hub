"use client";

// 头像裁剪器：上传后先在这里定裁剪范围（拖动 + 缩放），再把裁好的那块传上去。
//
// 交互照微信来：圆形取景框、拖动移动照片、双指/滚轮/滑块缩放。
// 为什么三个缩放入口都要有：
//   · 双指 —— 手机上最自然，但不好发现，也不适合"微调"
//   · 滚轮 —— 桌面上最自然
//   · 滑块 —— 谁都能上手，也是探针唯一能稳定驱动的入口（其余两个靠事件模拟，容易假过）
//
// 取景框直接做成**圆形**：最终展示就是圆的，让用户在裁剪阶段看到的就是成品。
// zoom 下限 1 = 短边刚好铺满这个圆，所以任何位置都不会露出空白（见 clampTransform）。
//
// 本组件不持有"提交"逻辑：它只负责把当前变换编成 blob。按钮在 DialogShell 的
// footer 里（父组件渲染），通过 ref 调 commit()。
import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";

import {
  MAX_ZOOM,
  MIN_ZOOM,
  clampTransform,
  decodeForCrop,
  drawCrop,
  encodeCrop,
  type CropTransform,
} from "@/lib/imageCrop";

/** 取景框的逻辑边长。CSS 宽度可以更窄（手机），绘制坐标始终按这个算 */
const VIEW = 264;

export interface CropperHandle {
  /** 按当前变换裁出 blob。失败返回 null */
  commit: () => Promise<Blob | null>;
  /** 回到初始状态（居中、1 倍） */
  reset: () => void;
}

interface Props {
  file: Blob;
  /** 上传中：禁用一切交互，避免"图还在传又改了位置" */
  busy?: boolean;
  /** 解不了这张图时通知父组件（父组件据此退回选择态并显示提示） */
  onDecodeError?: () => void;
}

const AvatarCropper = forwardRef<CropperHandle, Props>(function AvatarCropper(
  { file, busy = false, onDecodeError },
  ref,
) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const bmpRef = useRef<ImageBitmap | null>(null);
  const tRef = useRef<CropTransform>({ zoom: 1, ox: 0, oy: 0 });
  /** 按下的指针（支持双指）。key 是指针 id */
  const pointersRef = useRef(new Map<number, { x: number; y: number }>());
  /** 单指拖动时的上一个位置 */
  const dragRef = useRef<{ x: number; y: number } | null>(null);
  /** 双指缩放：起始距离 + 起始倍数 */
  const pinchRef = useRef<{ d: number; zoom: number } | null>(null);

  const [zoom, setZoom] = useState(1);
  /** 平移或缩放是否离开初始值；只用于复位按钮，不能只看 zoom（1 倍也能左右拖） */
  const [dirty, setDirty] = useState(false);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);

  /** 重绘取景框。只读 ref，不读 state —— 所以依赖为空 */
  const draw = useCallback(() => {
    const cv = canvasRef.current;
    const bmp = bmpRef.current;
    if (!cv || !bmp) return;

    // 按设备像素比放大画布：不这么做在手机上是一团糊的
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const px = Math.round(VIEW * dpr);
    if (cv.width !== px || cv.height !== px) {
      cv.width = px;
      cv.height = px;
    }
    const ctx = cv.getContext("2d");
    if (!ctx) return;

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, VIEW, VIEW);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    drawCrop(ctx, bmp, tRef.current, VIEW);
  }, []);

  // 解码。file 变了就重来一次（父组件用 key 让本组件重挂，这里只是兜底）
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const bmp = await decodeForCrop(file);
        if (!alive) {
          bmp.close?.();
          return;
        }
        bmpRef.current?.close?.();
        bmpRef.current = bmp;
        tRef.current = { zoom: 1, ox: 0, oy: 0 };
        setZoom(1);
        setDirty(false);
        draw();
        setReady(true);
      } catch {
        if (alive) {
          setFailed(true);
          onDecodeError?.();
        }
      }
    })();
    return () => {
      alive = false;
      // 主动释放位图，别等 GC —— 解码后是几十 MB
      bmpRef.current?.close?.();
      bmpRef.current = null;
    };
  }, [file, draw, onDecodeError]);

  const applyZoom = useCallback(
    (next: number) => {
      const bmp = bmpRef.current;
      if (!bmp) return;
      const z = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, next));
      // 拖滑块会连发很多次，变化太小就跳过重绘（1e-3 在一次绘制里肉眼看不出）
      if (Math.abs(z - tRef.current.zoom) < 0.001) return;
      tRef.current.zoom = z;
      clampTransform(tRef.current, bmp);
      draw();
      setZoom(z);
      setDirty(true);
    },
    [draw],
  );

  const reset = useCallback(() => {
    const bmp = bmpRef.current;
    if (!bmp) return;
    tRef.current = { zoom: 1, ox: 0, oy: 0 };
    setZoom(1);
    setDirty(false);
    draw();
  }, [draw]);

  useImperativeHandle(ref, () => ({
    commit: async () => {
      const bmp = bmpRef.current;
      if (!bmp) return null;
      return encodeCrop(bmp, { ...tRef.current });
    },
    reset,
  }), [reset]);

  // 滚轮缩放。React 的 onWheel 是 passive 的，拦不住页面滚动，必须自己绑
  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv || !ready || failed) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      applyZoom(tRef.current.zoom * (e.deltaY < 0 ? 1.12 : 1 / 1.12));
    };
    cv.addEventListener("wheel", onWheel, { passive: false });
    return () => cv.removeEventListener("wheel", onWheel);
  }, [ready, failed, applyZoom]);

  /** 两指距离 */
  const pinchDistance = () => {
    const pts = [...pointersRef.current.values()];
    if (pts.length < 2) return 0;
    return Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
  };

  const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (!ready || busy) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    pointersRef.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointersRef.current.size === 1) {
      dragRef.current = { x: e.clientX, y: e.clientY };
    } else if (pointersRef.current.size === 2) {
      pinchRef.current = { d: pinchDistance(), zoom: tRef.current.zoom };
      dragRef.current = null;
    }
  };

  const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const pts = pointersRef.current;
    if (!pts.has(e.pointerId)) return;
    pts.set(e.pointerId, { x: e.clientX, y: e.clientY });

    const bmp = bmpRef.current;
    const cv = canvasRef.current;
    if (!bmp || !cv) return;

    if (pts.size >= 2) {
      const p = pinchRef.current;
      if (!p || p.d < 8) return;
      const d = pinchDistance();
      if (d < 8) return;
      applyZoom(p.zoom * (d / p.d));
      return;
    }

    const drag = dragRef.current;
    if (!drag) return;
    // ★ 用 rect.width 而不是 VIEW 换算：手机上取景框被 CSS 压窄了，
    //   手指走的 CSS 像素和逻辑像素不是 1:1，直接用会"手指走 10px，图走 11px"
    const rect = cv.getBoundingClientRect();
    if (rect.width <= 0) return;
    tRef.current.ox += (e.clientX - drag.x) / rect.width;
    tRef.current.oy += (e.clientY - drag.y) / rect.width;
    dragRef.current = { x: e.clientX, y: e.clientY };
    clampTransform(tRef.current, bmp);
    draw();
    setDirty(true);
  };

  const onPointerUp = (e: React.PointerEvent<HTMLCanvasElement>) => {
    pointersRef.current.delete(e.pointerId);
    if (pointersRef.current.size === 0) dragRef.current = null;
    if (pointersRef.current.size < 2) pinchRef.current = null;
    // 双指松开一根后，剩下那根如果继续动，会因为 dragRef 为 null 被忽略。
    // 这是有意的：松手即停，比"突然跳到新位置"好接受
  };

  return (
    <div className="acct-crop">
      <div className="acct-crop-stage">
        <canvas
          ref={canvasRef}
          className="acct-crop-canvas"
          role="img"
          aria-label="头像裁剪区，可拖动调整位置"
          /* 解码完成的机器可读信号。浏览器探针靠它判断"可以点了" ——
             否则只能靠"看像素变没变"，而条纹图的中心恰好是白的，那种判据会假失败 */
          data-ready={ready ? "1" : "0"}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
        />
        <span className="acct-crop-ring" aria-hidden="true" />
      </div>

      {/* 两端的小圆点/大圆点表示"缩小 ↔ 放大"。原来放的是放大镜和靶心图标，
          语义与缩放无关 —— 用户得先猜一下才知道那是干什么的 */}
      <div className="acct-crop-zoom">
        <span className="acct-crop-dot is-sm" aria-hidden="true" />
        <input
          type="range"
          min={MIN_ZOOM}
          max={MAX_ZOOM}
          step={0.01}
          value={zoom}
          onChange={(e) => applyZoom(Number(e.target.value))}
          disabled={!ready || busy}
          aria-label="放大缩小"
          className="acct-crop-range"
        />
        <span className="acct-crop-dot is-lg" aria-hidden="true" />
        <button
          type="button"
          className="acct-crop-reset"
          onClick={reset}
          disabled={busy || !ready || !dirty}
          title="回到居中、原始大小"
        >
          复位
        </button>
      </div>

      {/* 正常状态不显示任何提示 —— 弹窗顶部那句话已经说完了。
          这里只兜"解不了图"这种真的需要用户换一张的情况 */}
      {failed && (
        <p className="acct-crop-hint" role="alert">
          这张图打不开，换一张 JPG / PNG / WebP 试试。
        </p>
      )}
    </div>
  );
});

export default AvatarCropper;
