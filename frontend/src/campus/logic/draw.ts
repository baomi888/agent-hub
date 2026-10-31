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
  if (names.length === 0) return [];

  const wanted = Math.max(0, Math.floor(count));
  const take = Math.min(wanted, names.length);
  if (take === 0) return [];

  return shuffle(names, random).slice(0, take);
}

/**
 * 解析名单文本：按换行、中英文逗号、顿号、分号、空格切分。
 *
 * 去空、去重但**保持原顺序** —— 用户按学号或座位顺序输入，打乱顺序会让结果看着莫名其妙。
 */
export function parseNames(text: string): string[] {
  const seen = new Set<string>();
  const result: string[] = [];

  for (const piece of text.split(/[\n\r,，、;；\s]+/)) {
    const name = piece.trim();
    if (name === "" || seen.has(name)) continue;
    seen.add(name);
    result.push(name);
  }

  return result;
}
