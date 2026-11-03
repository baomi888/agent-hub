/**
 * 去掉「没有来源可指」的引用编号。
 *
 * 为什么需要：模型会照着上下文里的历史回答（老会话里全是 `[1]` / `依据：[1][2]`）
 * 继续写编号，即使这一轮压根没检索、没联网。用户看到的是一排**点了没反应**的
 * 小方块 —— 它长得像引用，实际上什么都不是，末尾那句「依据：[1][2]」更是无中生有。
 *
 * 规则（只删指不到的，不动真引用）：
 *   [Wn] → webIds 里有 n 才留，否则删
 *   [n]  → refIds 里有 n 才留，否则删（`[文案](url)` 这类 markdown 链接不碰）
 *   整行只剩「依据：」这类前缀时，连行一起删 —— 没有依据却写"依据"是事实错误
 *
 * ★ 只在**回答结束之后**调用（streaming 期间不能调）：联网来源是收尾时才下发的，
 *   流式途中 webIds 还是空的，这时清理会把本轮所有真角标一起删掉，等来源到了
 *   再"长回来"，用户看到的是角标在闪。
 */
export interface CiteLookup {
  /** 本条回答里真实存在的资料库来源编号 */
  refIds: Set<number>;
  /** 本条回答里真实存在的联网来源编号（W 后面的数字） */
  webIds: Set<number>;
}

const WEB_TOKEN = /\[W(\d{1,3})\]/gi;
/** 排除 `[1](url)`：那是 markdown 链接的文案，不是引用 */
const REF_TOKEN = /\[(\d{1,3})\](?!\()/g;
/** 删干净之后只剩前缀的行：依据 / 参考资料 / 参考来源 / 引用来源 / Sources */
const BARE_TAIL =
  /^\s*(?:\*\*)?\s*(依据|参考资料|参考来源|引用来源|来源|sources?)\s*[:：]\s*(?:\*\*)?\s*[\[\]\d\s,，、;；&]*$/i;

export function stripPhantomCitations(text: string, { refIds, webIds }: CiteLookup): string {
  if (!text) return text;
  let out = text;

  // 1) 联网编号：指不到就删。连同前面一个空格一起删，避免留下「结论 」这种尾空格。
  if (webIds.size === 0) {
    out = out.replace(/\s?\[W\d{1,3}\]/gi, "");
  } else {
    out = out.replace(/\s?\[W(\d{1,3})\]/gi, (all, d: string) =>
      webIds.has(Number(d)) ? all : ""
    );
  }

  // 2) 资料库编号：同上。没有资料库来源时整类清掉。
  if (refIds.size === 0) {
    out = out.replace(/\s?\[(\d{1,3})\](?!\()/g, "");
  } else {
    out = out.replace(/\s?\[(\d{1,3})\](?!\()/g, (all, d: string) =>
      refIds.has(Number(d)) ? all : ""
    );
  }

  if (out === text) return text;

  // 3) 逐行收尾：只剩光杆前缀的行直接删；顺手去掉行尾空格和多余空行。
  const lines = out.split("\n");
  const kept: string[] = [];
  for (const line of lines) {
    if (BARE_TAIL.test(line)) continue;
    kept.push(line.replace(/[ \t]+$/, ""));
  }
  out = kept.join("\n").replace(/\n{3,}/g, "\n\n");
  return out.replace(/\s+$/, "");
}
