// 短 id 生成器：优先 crypto.randomUUID，回落到随机串
export const uid = () =>
  typeof crypto !== "undefined" && crypto.randomUUID
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2);
