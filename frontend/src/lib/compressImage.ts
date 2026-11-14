/**
 * 把用户选的图压到「长边 2048 / WebP q0.82」再上传。
 *
 * ## 为什么在浏览器里压，而不是交给服务器
 *
 * 服务器解码一张 1200 万像素的手机原图，峰值 53 MB；缩到 1600 再转 WebP，
 * 79 MB。那台机器 `available` 只剩 400 MB，9 张图同时处理就是 714 MB ——
 * 后端会被 OOM 杀掉，用户只看到「连接被重置」。
 *
 * 放在用户手机上做，服务器就只剩存盘：一张 6 MB 的原图先变成 400 KB，
 * 上传流量、磁盘占用、服务器内存一起降一个数量级。
 *
 * ## 附带解决一个隐私问题
 *
 * 手机照片的 EXIF 里带 GPS 坐标。学生拍宿舍、拍教室发上来，照片里就写着经纬度。
 * canvas 重绘是从像素重建的，元数据全丢 —— 所以**即使图本身已经很小也照样重绘**，
 * 剥 EXIF 比省那几十 KB 重要。服务端也因此把「无 EXIF」当作「前端压过」的判据。
 *
 * ## 一个反直觉的地方
 *
 * 重绘后体积偶尔比原图大一点（已经压过的 WebP 再转一次码）。这里不做
 * 「变大就用原图」的回退 —— 原图可能带 GPS，为了省几十 KB 把位置泄露出去不划算。
 */

const MAX_SIDE = 2048;
const QUALITY = 0.82;
const JPEG_QUALITY = 0.85;

/** 超过这个像素数就不接了。解码它会把用户手机的内存也吃掉。 */
const MAX_PIXELS = 40_000_000;

export interface Compressed {
  file: File;
  /** 原始字节数 */
  before: number;
  /** 压缩后字节数 */
  after: number;
  width: number;
  height: number;
  /** 输出格式，`image/webp` 或 `image/jpeg` */
  type: string;
}

export class CompressError extends Error {}

let webpSupport: boolean | null = null;

/** 只在第一次调用时真的探测，结果缓存。同步、可靠，不靠 UA 猜。 */
function supportsWebp(): boolean {
  if (webpSupport !== null) return webpSupport;
  try {
    const c = document.createElement("canvas");
    c.width = 1;
    c.height = 1;
    webpSupport = c.toDataURL("image/webp").startsWith("data:image/webp");
  } catch {
    webpSupport = false;
  }
  return webpSupport;
}

interface Loaded {
  src: CanvasImageSource;
  w: number;
  h: number;
  release: () => void;
}

async function load(file: File): Promise<Loaded> {
  // createImageBitmap 优先：它在解码阶段就能拿到尺寸，且可以 close() 立刻释放，
  // 比 <img> 省一半内存。Safari 15 以下没有，所以要有降级。
  if (typeof createImageBitmap === "function") {
    try {
      const bmp = await createImageBitmap(file);
      return { src: bmp, w: bmp.width, h: bmp.height, release: () => bmp.close() };
    } catch {
      /* 走到下面用 <img>；SVG 之类 createImageBitmap 会拒 */
    }
  }
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const el = new Image();
      el.onload = () => resolve(el);
      el.onerror = () => reject(new CompressError("这张图打不开，换一张试试"));
      el.src = url;
    });
    return {
      src: img,
      w: img.naturalWidth,
      h: img.naturalHeight,
      release: () => URL.revokeObjectURL(url),
    };
  } catch (e) {
    URL.revokeObjectURL(url);
    throw e instanceof CompressError ? e : new CompressError("这张图打不开，换一张试试");
  }
}

function toBlob(canvas: HTMLCanvasElement, type: string, quality: number): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (b) => (b ? resolve(b) : reject(new CompressError("压缩失败，换一张试试"))),
      type,
      quality,
    );
  });
}

export async function compressImage(file: File): Promise<Compressed> {
  if (!file.type.startsWith("image/")) {
    throw new CompressError("只能选图片");
  }

  const loaded = await load(file);
  try {
    const { w, h } = loaded;
    if (w <= 0 || h <= 0) throw new CompressError("这张图的尺寸不合法");
    if (w * h > MAX_PIXELS) {
      throw new CompressError(`这张图太大了（${w}×${h}），换一张小一点的`);
    }

    // 只缩不放。小图保持原尺寸，放大只会更糊。
    const scale = Math.min(1, MAX_SIDE / Math.max(w, h));
    const tw = Math.max(1, Math.round(w * scale));
    const th = Math.max(1, Math.round(h * scale));

    const canvas = document.createElement("canvas");
    canvas.width = tw;
    canvas.height = th;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new CompressError("这个浏览器不支持图片处理");

    // 透明图垫白底。不垫的话 PNG 截图转 JPEG 会把透明区变黑 —— 一个白底
    // 表情包变成黑块。服务端 `_recompress` 也是同一个处理，两边要一致。
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, tw, th);

    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(loaded.src, 0, 0, tw, th);

    const useWebp = supportsWebp();
    const type = useWebp ? "image/webp" : "image/jpeg";
    const blob = await toBlob(canvas, type, useWebp ? QUALITY : JPEG_QUALITY);

    const ext = useWebp ? ".webp" : ".jpg";
    const name = (file.name || "image").replace(/\.[^./\\]+$/, "") + ext;

    return {
      file: new File([blob], name, { type }),
      before: file.size,
      after: blob.size,
      width: tw,
      height: th,
      type,
    };
  } finally {
    loaded.release();
  }
}

/** 人话体积：`1.2 MB` / `380 KB`。 */
export function fmtBytes(n: number): string {
  if (!n || n < 0) return "0 KB";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
