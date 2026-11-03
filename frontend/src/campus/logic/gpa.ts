/**
 * 校园工具集 · GPA 计算（纯函数，无副作用、不依赖 React）
 *
 * 算法必须与线上 tools.html 逐条一致，否则用户换了新页面会发现绩点变了。
 * 对照源码：frontend/public/tools.html 第 826–836 行（换算表）与 recalc()。
 */

import type { GpaAlgo, GpaRow, LegacyGpaRow } from "../types";

/** 换算表：[分数下限, 对应绩点]，从高到低排，命中第一个「分数 ≥ 下限」的档 */
const TABLES: Record<Exclude<GpaAlgo, "wavg">, ReadonlyArray<readonly [number, number]>> = {
  std4: [
    [90, 4.0],
    [85, 3.7],
    [82, 3.3],
    [78, 3.0],
    [75, 2.7],
    [72, 2.3],
    [68, 2.0],
    [64, 1.5],
    [60, 1.0],
    [0, 0.0],
  ],
  pku: [
    [90, 4.0],
    [85, 3.7],
    [83, 3.3],
    [80, 3.0],
    [77, 2.7],
    [73, 2.3],
    [70, 2.0],
    [67, 1.5],
    [63, 1.0],
    [60, 1.0],
    [0, 0.0],
  ],
};

/** 单科换算：加权均分档直接返回原始分，不折算 */
export function scoreToGpa(score: number, algo: GpaAlgo): number {
  if (algo === "wavg") return score;

  for (const [min, point] of TABLES[algo]) {
    if (score >= min) return point;
  }
  // 理论上到不了这里（表末尾有 [0, 0]），兜底防脏数据
  return 0;
}

/** 单行明细，point 为 null 表示成绩未填 */
export interface GpaRowDetail extends GpaRow {
  point: number | null;
}

/** 计算结果 */
export interface GpaSummary {
  /** 加权 GPA；总学分为 0 时为 null（对应旧版显示「—」） */
  gpa: number | null;
  /** 原始成绩加权均分；总学分为 0 时为 null */
  weightedAvg: number | null;
  /** 有效学分合计（含成绩空行之外的所有行，与旧版一致） */
  totalCredits: number;
  rows: GpaRowDetail[];
}

/**
 * 汇总计算。
 *
 * 口径与旧版 recalc() 严格对齐，两条容易写错的规则：
 *  - 成绩为空的行**完全不参与**统计，连学分也不累加（旧版遇到空成绩直接 return）；
 *  - 学分只要求是有限数值，不过滤负数（旧版 `parseFloat(...) || 0` 就是这个行为）。
 *    负数属于脏数据，但改了口径会让老用户看到的 GPA 与旧版不一致，得不偿失。
 */
export function computeGpa(rows: readonly GpaRow[], algo: GpaAlgo): GpaSummary {
  let weightedPoint = 0;
  let weightedScore = 0;
  let totalCredits = 0;
  const detail: GpaRowDetail[] = [];

  for (const row of rows) {
    const score = row.score !== null && Number.isFinite(row.score) ? row.score : null;
    if (score === null) {
      detail.push({ ...row, point: null });
      continue;
    }

    const credit = Number.isFinite(row.credit) ? row.credit : 0;
    const point = scoreToGpa(score, algo);

    weightedPoint += point * credit;
    weightedScore += score * credit;
    totalCredits += credit;

    detail.push({ ...row, point });
  }

  return {
    gpa: totalCredits > 0 ? weightedPoint / totalCredits : null,
    weightedAvg: totalCredits > 0 ? weightedScore / totalCredits : null,
    totalCredits,
    rows: detail,
  };
}

/** 旧版存储结构 → 新结构；不可用返回 null */
export function parseLegacyGpa(raw: unknown): { algo: GpaAlgo; rows: GpaRow[] } | null {
  if (raw === null || typeof raw !== "object") return null;

  const source = raw as { algo?: unknown; rows?: unknown };
  if (!Array.isArray(source.rows)) return null;

  const algo: GpaAlgo =
    source.algo === "pku" || source.algo === "wavg" ? source.algo : "std4";

  const rows: GpaRow[] = source.rows.map((item) => {
    const row = (item ?? {}) as LegacyGpaRow;

    // c / s 都是字符串，空串要落到 null 而不是 0
    const creditText = String(row.c ?? "").trim();
    const scoreText = String(row.s ?? "").trim();

    const credit = Number.parseFloat(creditText);
    const score = scoreText === "" ? null : Number.parseFloat(scoreText);

    return {
      name: String(row.n ?? ""),
      credit: Number.isFinite(credit) ? credit : 0,
      score: score !== null && Number.isFinite(score) ? score : null,
    };
  });

  return { algo, rows };
}

/** 示例数据（首次打开时铺一份，与旧版「载入示例」一致） */
export const SAMPLE_ROWS: GpaRow[] = [
  { name: "高等数学", credit: 5, score: 88 },
  { name: "大学英语", credit: 3, score: 76 },
  { name: "数据结构", credit: 4, score: 92 },
  { name: "线性代数", credit: 3, score: 81 },
  { name: "思想道德", credit: 2, score: 69 },
];
