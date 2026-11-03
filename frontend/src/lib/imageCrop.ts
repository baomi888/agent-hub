// 头像上传的「本地处理层」：解码 → 用户裁剪 → 编码。
//
// 文件名从 imageCompress 改过来：那一版是"自动居中裁方"，用户没法选范围，
// 长脸/竖图会被硬切掉两侧。现在改成人来定裁剪框，压缩退化成裁剪的副产品。
//
// 三层限额只剩两层（见 docs/账号自助_功能实现说明 §4）：
//   ① 前端裁剪：**只传裁好的那块**，640×640 q0.85 一般 40~80KB
//   ② 后端：硬限 5MB + 真格式白名单 + 落盘 192×192
// 原来那层"先压小再传"没必要了 —— 原图压根不上传，多大都行（只受本地解码能力限制）。
//
// 参数（改之前先想清楚）：
//   LOAD_MAX_SIDE 1920 —— 解码后位图长边上限。4000×4000 的位图是 64MB，
//                         手机上解两张就 OOM；1920 已经远超 640 的输出需求。
//                         ★ 这也是"预览和输出用同一张位图"的前提，两边像素一致。
//   CROP_OUT_SIDE 640  —— 最终只显示 192px，留 3 倍视网膜余量足够，再大纯属浪费。
//   QUALITY 0.85       —— 肉眼无损的甜点；0.7 以下人像脸会发灰发脏。

/** 与后端 `service/avatar.py::MAX_BYTES` 对齐 */
export const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;

/** 原图大小上限。**不上传**，只限制"能解码得动"，所以比 5MB 宽得多 */
export const MAX_SOURCE_BYTES = 20 * 1024 * 1024;

/** 解码后位图的长边上限（px） */
const LOAD_MAX_SIDE = 1920;
/** 输出边长上限（px） */
const CROP_OUT_SIDE = 640;
/** 输出边长下限：后端落盘是 192，低于它没有意义 */
const CROP_OUT_MIN = 192;
const QUALITY = 0.85;

/** 缩放倍数范围。下限 1 = 短边刚好铺满裁剪框（再小就露白） */
export const MIN_ZOOM = 1;
export const MAX_ZOOM = 4;

/**
 * 裁剪变换。
 *
 * `ox / oy` 是**比例**而不是像素：图片中心相对裁剪框中心的偏移，单位是"裁剪框边长的几分之几"。
 * 这样预览（264px）与输出（640px）用同一份数据、同一个 `drawCrop`，天然不会错位 ——
 * 如果存像素，就得在两边各乘一次换算系数，而那个系数正是最容易写错的地方。
 */
export interface CropTransform {
  zoom: number;
  ox: number;
  oy: number;
}

/** 把图片按变换画进一个 side×side 的正方形（左上角为原点） */
export function drawCrop(
  ctx: CanvasRenderingContext2D,
  bmp: ImageBitmap,
  t: CropTransform,
  side: number,
): void {
  const minSide = Math.min(bmp.width, bmp.height);
  if (minSide <= 0) return;
  // base：zoom=1 时短边刚好等于 side
  const s = (side / minSide) * t.zoom;
  const w = bmp.width * s;
  const h = bmp.height * s;
  ctx.drawImage(bmp, side / 2 + t.ox * side - w / 2, side / 2 + t.oy * side - h / 2, w, h);
}

/**
 * 把偏移夹回合法范围：图片必须始终盖满裁剪框，任何角度都不许露白。
 *
 * 边界值：zoom=1 时短边方向 `half=0.5` → 可动量为 0，只能固定居中；
 * 长边方向可动量为 `(长边/短边 - 1) / 2`。
 */
export function clampTransform(t: CropTransform, bmp: ImageBitmap): void {
  const minSide = Math.min(bmp.width, bmp.height);
  if (minSide <= 0) return;
  const halfW = (t.zoom * (bmp.width / minSide)) / 2;
  const halfH = (t.zoom * (bmp.height / minSide)) / 2;
  const mx = Math.max(0, halfW - 0.5);
  const my = Math.max(0, halfH - 0.5);
  t.ox = Math.min(mx, Math.max(-mx, t.ox));
  t.oy = Math.min(my, Math.max(-my, t.oy));
}

/**
 * 解码一张图并限制长边，返回可直接复用的位图。
 *
 * **失败会抛错**，调用方要接住并给中文提示 —— 与旧的 `compressAvatar` 返回 null 不同：
 * 那边返回 null 的意思是"压不了，传原图让后端兜"，而裁剪没有这条路，
 * 解不了就是解不了，必须当场告诉用户换一张（典型触发：iPhone 的 HEIC）。
 */
export async function decodeForCrop(file: Blob): Promise<ImageBitmap> {
  // Safari 14 及以下没有 createImageBitmap：这里直接抛，由调用方提示
  if (typeof createImageBitmap !== "function") {
    throw new Error("createImageBitmap unsupported");
  }

  const bmp = await createImageBitmap(file);
  const long = Math.max(bmp.width, bmp.height);
  if (long <= LOAD_MAX_SIDE) return bmp;

  // 先缩到上限再留着用：后面每次拖动都从这张位图采样，尺寸直接决定流畅度
  const k = LOAD_MAX_SIDE / long;
  const w = Math.max(1, Math.round(bmp.width * k));
  const h = Math.max(1, Math.round(bmp.height * k));
  const cv = document.createElement("canvas");
  cv.width = w;
  cv.height = h;
  const ctx = cv.getContext("2d");
  if (!ctx) {
    bmp.close?.();
    throw new Error("no 2d context");
  }
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(bmp, 0, 0, w, h);
  bmp.close?.();
  return createImageBitmap(cv);
}

/**
 * 按当前变换裁出成品。返回 `null` 表示编码失败（基本只可能是内存不够）。
 *
 * 输出边长取 `min(640, 裁剪区在源图上的实际像素)`：小图不放大。
 * 放大不会增加信息，只会让上传体积翻几倍，而服务器最终还要缩回 192。
 */
export async function encodeCrop(bmp: ImageBitmap, t: CropTransform): Promise<Blob | null> {
  const srcSide = Math.min(bmp.width, bmp.height) / Math.max(1, t.zoom);
  const outSide = Math.min(CROP_OUT_SIDE, Math.max(CROP_OUT_MIN, Math.round(srcSide)));

  const cv = document.createElement("canvas");
  cv.width = outSide;
  cv.height = outSide;
  const ctx = cv.getContext("2d");
  if (!ctx) return null;

  // 透明区域垫白：JPEG 没有 alpha，不垫的话透明会变黑（白底表情包变黑框头像）
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, outSide, outSide);
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  drawCrop(ctx, bmp, t, outSide);

  return new Promise<Blob | null>((resolve) => {
    cv.toBlob((b) => resolve(b), "image/jpeg", QUALITY);
  });
}
