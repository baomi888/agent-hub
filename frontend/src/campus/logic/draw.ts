/**
 * 校园工具集 · 群抽签（纯函数）
 *
 * 抽签看着简单，实际有两个容易翻车的点：
 *  1. 用 sort(() => Math.random() - 0.5) 洗牌是错的 —— 分布不均，人多了明显偏；
 *     这里用 Fisher-Yates，每一步都是等概率。
 *  2. 多人中签时不能重复点同一个人 —— 洗牌后取前 N 个天然不重复。
 */

/** Fisher-Yates 洗牌，返回新数组，不改入参 */
export function shuffle<T>(items: readonly T[], random: () => number = Math.random): T[] {
  const result = items.slice();
  for (let i = result.length - 1; i > 0; i -= 1) {
    const j = Math.floor(random() * (i + 1));
    const tmp = result[i];
    result[i] = result[j];
    result[j] = tmp;
  }
  return result;
}

/** 一次抽签的完整结果：中签者与剩下的人 */
export interface DrawOutcome {
  winners: string[];
  rest: string[];
}

/**
 * 抽签并保留「未中」名单。
 *
 * 只洗一次牌再切片 —— 这样 winners 和 rest 天然互补、不重不漏。
 * 分开抽两次会出错：可能同一个人既中签又出现在未中名单里。
 */
export function drawOutcome(
  names: readonly string[],
  count: number,
  random: () => number = Math.random,
): DrawOutcome {
  if (names.length === 0) return { winners: [], rest: [] };

  const wanted = Math.max(0, Math.floor(count));
  const take = Math.min(wanted, names.length);
  const pool = shuffle(names, random);

  return { winners: pool.slice(0, take), rest: pool.slice(take) };
}

/**
 * 抽取中签者。
 *
 * @param names  参与名单（调用方先去重）
 * @param count  中签人数；超过名单长度时按名单长度取，不报错
 * @returns 中签名单，顺序即抽中顺序
 */
export function drawWinners(
  names: readonly string[],
  count: number,
  random: () => number = Math.random,
): string[] {
  return drawOutcome(names, count, random).winners;
}

/**
 * 解析名单文本。
 *
 * 旧版按换行切分（`split('\n')`），这里**优先保留这个口径**：
 * 名单是「每行一个」的接龙格式，若按空白切分，`Li Ming` 会被拆成两个人，
 * 而抽签结果一旦多出不存在的人，比少几个更难看。
 *
 * 只有在整段**没有换行**、却写了逗号/顿号/分号时，才按这些分隔符切分 ——
 * 这样「张三,李四,王五」这种一行黏贴也能用。
 *
 * 去空、去重，但保持原顺序：用户按学号或座位顺序输入，打乱会让结果看着莫名其妙。
 */
export function parseNames(text: string): string[] {
  const trimmed = text.trim();
  if (trimmed === "") return [];

  const lines = trimmed.split(/\r?\n/);
  const pieces =
    lines.length === 1 && /[,，、;；]/.test(trimmed) ? trimmed.split(/[,，、;；]+/) : lines;

  const seen = new Set<string>();
  const result: string[] = [];

  for (const piece of pieces) {
    const name = piece.trim();
    if (name === "" || seen.has(name)) continue;
    seen.add(name);
    result.push(name);
  }

  return result;
}
