// 资料处理方式：把「分段长度 / 重叠字数」这两个工程参数，
// 翻译成用户能凭直觉选的三种「读法」。
//
// 用户不需要知道「切片」「分块」「重叠」是什么意思——只需要判断
// 手上这份资料想怎么读：要答案准一点，还是一段话别被切断。
// 原始数字仍然保留（见「高级设置」），但不再作为主界面语言。
//
// 换算关系（后端契约不变，仍是 chunk_size / chunk_overlap）：
//   精细 300/60 · 均衡 500/50 · 完整 1000/100

export type ChunkProfileId = "fine" | "balanced" | "full";

/** 真正的请求参数，后端只认这两个数 */
export interface ChunkParams {
  size: number;
  overlap: number;
}

export interface ChunkProfile {
  id: ChunkProfileId;
  /** 档位名：精细 / 均衡 / 完整 */
  label: string;
  /** 按钮里的极小注解，2-3 字 */
  short: string;
  /** 适合什么资料 */
  examples: string;
  /** 这一档换来什么 */
  benefit: string;
  size: number;
  overlap: number;
}

export const CHUNK_PROFILES: readonly ChunkProfile[] = [
  {
    id: "fine",
    label: "精细",
    short: "更准",
    examples: "名词解释 · 提纲 · 讲义",
    benefit: "切得细，答得更准",
    size: 300,
    overlap: 60,
  },
  {
    id: "balanced",
    label: "均衡",
    short: "推荐",
    examples: "教材 · 论文 · 通用文档",
    benefit: "多数资料选这个就行",
    size: 500,
    overlap: 50,
  },
  {
    id: "full",
    label: "完整",
    short: "更连贯",
    examples: "小说 · 访谈 · 连续文稿",
    benefit: "切得整，上下文更连贯",
    size: 1000,
    overlap: 100,
  },
];

export const DEFAULT_PROFILE_ID: ChunkProfileId = "balanced";

export function getProfile(id: string | null | undefined): ChunkProfile {
  return (
    CHUNK_PROFILES.find((p) => p.id === id) ??
    (CHUNK_PROFILES.find((p) => p.id === DEFAULT_PROFILE_ID) as ChunkProfile)
  );
}

export function profileParams(id: ChunkProfileId): ChunkParams {
  const p = getProfile(id);
  return { size: p.size, overlap: p.overlap };
}

/** 反查：给定数字正好属于哪一档；对不上任何档位返回 null（= 用户自定义过） */
export function matchProfileId(size: number, overlap: number): ChunkProfileId | null {
  const hit = CHUNK_PROFILES.find((p) => p.size === size && p.overlap === overlap);
  return hit ? hit.id : null;
}

/** 给任意数字找最接近的档位（只用于按钮高亮，不改用户设置） */
export function nearestProfileId(size: number, overlap: number): ChunkProfileId {
  const exact = matchProfileId(size, overlap);
  if (exact) return exact;
  let best: ChunkProfile = CHUNK_PROFILES[1];
  let bestGap = Number.POSITIVE_INFINITY;
  for (const p of CHUNK_PROFILES) {
    // 重叠字数对结果影响比分段长度小，按 2 倍缩放里对齐量纲
    const gap = Math.abs(p.size - size) + Math.abs(p.overlap - overlap) * 2;
    if (gap < bestGap) {
      bestGap = gap;
      best = p;
    }
  }
  return best.id;
}

/** 这套参数怎么称呼：「均衡」/「自定义 800/80」，用于库列表标签和 toast */
export function describeParams(size: number, overlap: number): string {
  const id = matchProfileId(size, overlap);
  return id ? getProfile(id).label : `自定义 ${size}/${overlap}`;
}
