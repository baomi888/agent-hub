// 资料库上传前置校验：两个上传入口（首页右侧面板 / 资料库页）共用同一套规则。
//
// 为什么抽出来：2026-11-18 用户反馈「上传太慢、老是失败」时查到，首页面板有
// 100MB 单文件校验，资料库页却一个都没有 —— 同一个动作、两套规则，用户在
// 资料库页传超大文件要等传完 + 建完库才被后端拒绝，体验差且报错滞后。
// 规则与后端 api/kb.py 对齐：单文件 ≤100MB、单次总量 ≤200MB、仅 txt/md/pdf。

/** 单文件上限（MB），与后端 api/kb.py 的 MAX_UPLOAD_MB 一致 */
export const UPLOAD_SINGLE_MB = 100;
/** 单次请求总量上限（MB），与后端 config.UPLOAD_TOTAL_MB 默认值一致 */
export const UPLOAD_TOTAL_MB = 200;
/** 允许的文件扩展名，与后端 api/kb.py 的上传白名单一致 */
export const UPLOAD_ACCEPT = [".txt", ".md", ".pdf"] as const;

function extOf(name: string): string {
  const i = name.lastIndexOf(".");
  return i < 0 ? "" : name.slice(i).toLowerCase();
}

export function formatSize(bytes: number): string {
  return bytes < 10 * 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(1)}MB`
    : `${Math.round(bytes / 1024 / 1024)}MB`;
}

/**
 * 选完文件当场校验，返回可读的中文错误；全部合法返回 null。
 * 三道关按「格式 → 单文件大小 → 总量」顺序，先挡最可能的用户失误。
 */
export function validateUpload(files: File[]): string | null {
  if (files.length === 0) return null;
  for (const f of files) {
    const ext = extOf(f.name);
    if (!(UPLOAD_ACCEPT as readonly string[]).includes(ext)) {
      return `不支持的文件格式：${ext || "无扩展名"}（只支持 TXT / Markdown / PDF）`;
    }
  }
  const single = UPLOAD_SINGLE_MB * 1024 * 1024;
  const oversized = files.find((f) => f.size > single);
  if (oversized) {
    return `「${oversized.name}」超过 ${UPLOAD_SINGLE_MB}MB 限制（${formatSize(oversized.size)}）`;
  }
  const total = files.reduce((sum, f) => sum + f.size, 0);
  if (total > UPLOAD_TOTAL_MB * 1024 * 1024) {
    return `本次上传总大小超过 ${UPLOAD_TOTAL_MB}MB，请分批上传`;
  }
  return null;
}
