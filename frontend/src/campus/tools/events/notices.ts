"use client";

/**
 * 官网公告的同步结果。
 *
 * 数据由 `scripts/sync-events.mjs` 在构建前抓一次，写到 `public/data/events/notices.json`。
 * 页面打开时读它，把「官网最新公告」贴在条目详情里。
 *
 * 三条原则：
 *
 *  1. **只读公告标题与链接，不改任何日期。** 报名时间写在公告正文里，各站措辞不同、
 *     年年改版，正则一旦错位就会把日期安到别的考试上 —— 一条错日期比一条旧日期伤人。
 *  2. **抓不到就安静。** 文件缺失、离线、接口报错，一律返回空，页面回落到原来的静态文案。
 *  3. **不缓存。** 换服务器上那个 json 就应该立刻生效，这正是「不改代码也能更新」的意义。
 */

export interface SyncedNotice {
  title: string;
  url: string;
}

export interface NoticeBundle {
  /** 抓取时间（ISO），用来在界面上标「同步于 X」；没抓到过就是 null */
  generatedAt: string | null;
  /** 条目 id → 该条目的官网最新公告 */
  notices: Record<string, SyncedNotice[]>;
}

const EMPTY: NoticeBundle = { generatedAt: null, notices: {} };

/** 公告地址只认 http(s)：文件被改动过也不该让一个 javascript: 链接进到页面里 */
function safeUrl(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  try {
    const url = new URL(raw);
    return url.protocol === "http:" || url.protocol === "https:" ? url.toString() : null;
  } catch {
    return null;
  }
}

export async function loadNotices(): Promise<NoticeBundle> {
  try {
    const res = await fetch("/data/events/notices.json", { cache: "no-cache" });
    if (!res.ok) return EMPTY;

    const raw = (await res.json()) as {
      generatedAt?: unknown;
      notices?: Record<string, unknown>;
    };

    const notices: Record<string, SyncedNotice[]> = {};
    for (const [id, list] of Object.entries(raw.notices ?? {})) {
      if (!Array.isArray(list)) continue;
      const clean = list
        .map((n) => {
          const title = typeof n?.title === "string" ? n.title.trim() : "";
          const url = safeUrl(n?.url);
          return title && url ? { title, url } : null;
        })
        .filter((n): n is SyncedNotice => n !== null)
        .slice(0, 3);
      if (clean.length > 0) notices[id] = clean;
    }

    return {
      generatedAt: typeof raw.generatedAt === "string" ? raw.generatedAt : null,
      notices,
    };
  } catch {
    return EMPTY;
  }
}

/** 「2026-11-20T11:37:54.438Z」→「11-20」；抓取时间只用来提示新鲜度 */
export function syncedDay(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
