// 验证 unwrap 对各种后端壳层都能正确取出列表（契约联调前的护栏）
// 直接复用 api.ts 里的 unwrap 语义：裸数组 / primary key / items 兜底 / 元信息排除
function unwrap(key) {
  return (v) => {
    if (v === null || v === undefined) return null;
    if (Array.isArray(v)) return v;
    const rec = v;
    const primary = rec[key];
    if (Array.isArray(primary)) return primary;
    for (const k of Object.keys(rec)) {
      if (k === "next_cursor" || k === "total") continue;
      const val = rec[k];
      if (Array.isArray(val)) return val;
    }
    return [];
  };
}

const cases = [
  // [说明, 输入, 期望长度, 期望null]
  ["裸数组（后端 list_delegations 实际返回）", [{ id: "a" }], 1, false],
  ["items 壳层 + next_cursor（schedule/runs 实际）", { items: [{ id: "a" }, { id: "b" }], next_cursor: "123|run1" }, 2, false],
  ["items 壳层 + total（agent/tools 实际）", { items: [{ n: 1 }], total: 1 }, 1, false],
  ["旧 key 仍认（防回归）", { runs: [{ id: "x" }] }, 1, false],
  ["delegates 旧壳层仍认（防回归）", { delegates: [{ id: "d" }] }, 1, false],
  ["null → null（表示 absent，走演示数据）", null, 0, true],
  ["undefined → null", undefined, 0, true],
  ["空 items 数组", { items: [] }, 0, false],
  ["只有 next_cursor 无数据（不该把字符串当数组）", { next_cursor: "1|x" }, 0, false],
];

let pass = 0;
let fail = 0;
for (const [name, input, expLen, expNull] of cases) {
  const key = name.includes("runs") ? "items" : name.includes("delegates") ? "delegates" : "items";
  const r = unwrap(key)(input);
  const isNull = r === null;
  const len = isNull ? 0 : r.length;
  const ok = isNull === expNull && len === expLen;
  if (ok) pass++;
  else fail++;
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}  → ${isNull ? "null" : "len=" + len}`);
}
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
