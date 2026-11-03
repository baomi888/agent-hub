// 头像上传的「第 1 层限额」：在浏览器里先把图压小，再传。
//
// 为什么必须有这一层：后端硬限 5MB 是防滥用的，但手机照片动辄 4~5MB，
// 让每个用户每次都把 5MB 推上来，服务器白烧流量、手机上白等十几秒。
// 压完只有 80~150KB，流量降 95%，用户还更快看到结果。
//
// 参数（与方案 §6.4 一致，改之前先想清楚）：
//   长边 640 —— 最终只显示 192px，留 3 倍视网膜余量已经足够，再大纯属浪费
//   q0.85    —— 肉眼无损的甜点；0.7 以下人像脸会发灰发脏
//   先裁方再缩放 —— 否则后端裁方时会把已经缩掉的部分放大，白丢画质
//
// 兜底策略：压不了（老浏览器 / HEIC / 损坏文件）就返回 null，让调用方把原图传上去，
// 交给后端的 5MB 限制和 Pillow 兜 —— **宁可让后端多干活，也不要让用户传不上去**。

/** 与后端 `service/avatar.py::MAX_BYTES` 对齐 */
export const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;

/** 压缩目标长边（px） */
const OUT_SIDE = 640;
/** JPEG 质量 */
const QUALITY = 0.85;

/**
 * 把用户选的图片压成一张 640×640 的 JPEG。
 *
 * 返回 `null` 表示"这张图在浏览器里解不了"，调用方应退回上传原文件。
 * 注意返回 null **不代表文件一定合法** —— 真正的合法性判定在后端（`Image.open`）。
 */
export async function compressAvatar(file: File): Promise<Blob | null> {
  // Safari 14 及以下没有 createImageBitmap；直接交给后端处理
  if (typeof createImageBitmap !== "function") return null;

  let bmp: ImageBitmap | null = null;
  try {
    // 比 new Image() 省内存，且解码不在主线程上，不会卡住页面渲染
    bmp = await createImageBitmap(file);
  } catch {
    return null;
  }

  try {
    // 居中裁正方形：最终展示是圆的，先裁后传，用户看到的就是成品
    const side = Math.min(bmp.width, bmp.height);
    if (side <= 0) return null;
    const sx = (bmp.width - side) / 2;
    const sy = (bmp.height - side) / 2;

    const canvas = document.createElement("canvas");
    canvas.width = OUT_SIDE;
    canvas.height = OUT_SIDE;
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;

    // 透明区域垫白：JPEG 没有 alpha，不垫的话透明会变黑（白底表情包变黑框头像）
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, OUT_SIDE, OUT_SIDE);
    ctx.drawImage(bmp, sx, sy, side, side, 0, 0, OUT_SIDE, OUT_SIDE);

    return await new Promise<Blob | null>((resolve) => {
      canvas.toBlob((b) => resolve(b), "image/jpeg", QUALITY);
    });
  } catch {
    return null;
  } finally {
    // 主动释放位图，别等 GC —— 大图解码后是几十 MB
    bmp.close?.();
  }
}
