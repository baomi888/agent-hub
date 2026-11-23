// 数学公式渲染守卫：用线上真实回答的形态跑一遍 renderMarkdown。
//
// 覆盖两个曾经漏成原文的真实缺陷：
//   1. `$$` 紧跟正文行时被段落循环吞掉 → 块级公式分支永远走不到
//   2. `**...**` 里套 `$...$` 被 splitMath 从中间切断 → 两个 ** 漏成原文
// 另外守住「$5 到 $10」「$HOME 和 $PATH」不被误判成公式。
//
// 运行：node tools/verify_math_render.mjs   （退出码 0 = 全过）
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import ts from "../frontend/node_modules/typescript/lib/typescript.js";
import { createRequire } from "node:module";

const require = createRequire(new URL("../frontend/package.json", import.meta.url));
const root = path.resolve("frontend/src");
const cache = new Map();

function resolve(spec, from) {
  if (spec.startsWith("@/")) return path.join(root, spec.slice(2));
  if (spec.startsWith(".")) return path.resolve(path.dirname(from), spec);
  return null;
}

function load(file) {
  const base = [file, file + ".ts", file + ".tsx", path.join(file, "index.ts"), path.join(file, "index.tsx")]
    .find((p) => fs.existsSync(p) && fs.statSync(p).isFile());
  if (!base) throw new Error("找不到模块: " + file);
  if (cache.has(base)) return cache.get(base).exports;
  const mod = { exports: {} };
  cache.set(base, mod);
  const js = ts.transpileModule(fs.readFileSync(base, "utf8"), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      jsx: ts.JsxEmit.ReactJSX,
      esModuleInterop: true,
    },
    fileName: base,
  }).outputText;
  vm.runInNewContext(js, {
    module: mod,
    exports: mod.exports,
    console,
    require: (spec) => {
      const p = resolve(spec, base);
      return p ? load(p) : require(spec);
    },
  }, { filename: base });
  return mod.exports;
}

const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const { renderMarkdown } = load(path.join(root, "components/chat/content.tsx"));
const render = (text) => renderToStaticMarkup(React.createElement("div", null, renderMarkdown(text, false)));
// MathML 的 <annotation> 保留 tex 源码给屏幕阅读器；那不算用户可见的原文
const visible = (html) => html.replace(/<annotation[^>]*>[\s\S]*?<\/annotation>/g, "");

const answer = `✅ **可以**。
根据**拉格朗日中值定理**：对任意 $ x_1 < x_2 \\in [a,b] $，存在 $ \\xi \\in (x_1, x_2) $，使得
$$
f(x_2) - f(x_1) = f'(\\xi)(x_2 - x_1)
$$
若 $ f'(\\xi) < 0 $ 且 $ x_2 - x_1 > 0 $，则 $ f(x_2) - f(x_1) < 0 $，故 $ f $ 严格单调递减。
→ 所以 **$ f'(x) < 0 $ 是 $ f $ 单调减的充分条件**。

考虑函数
$$
f(x) = -x^3 \\quad \\text{在} \\ [-1, 1]
$$
`;

const html = render(answer);
const text = visible(html);
const katexCount = (html.match(/class="katex/g) || []).length;
const checks = [
  ["块级公式已排版", html.includes("katex-display")],
  ["不残留 $$ 原文", !text.includes("$$")],
  ["不残留 \\xi / \\quad 原文", !text.includes("\\xi") && !text.includes("\\quad")],
  ["不残留孤立 **", !text.includes("**")],
  ["加粗内的公式仍在 <strong> 内", /<strong>[\s\S]*?class="katex[\s\S]*?<\/strong>/.test(html)],
  ["公式节点数量达标（>=8）", katexCount >= 8],
  ["价格不误判", !render("价格 $5 到 $10 都可以").includes("katex")],
  ["Shell 变量不误判", !render("$HOME 和 $PATH 都在这").includes("katex")],
  ["带空格行内公式可渲染", render("$ f'(x) < 0 $ 成立").includes("katex")],
  ["行内 \\[..\\] 不回归", render("前面 \\[(x^2)\\] 后面").includes("katex")],
  ["行内 \\(..\\) 不回归", render("前面 \\(x^2\\) 后面").includes("katex")],
];

let failed = 0;
for (const [name, ok] of checks) {
  console.log(`${ok ? "PASS" : "FAIL"} ${name}`);
  if (!ok) failed++;
}
console.log(`katex 节点: ${katexCount}`);
if (failed) {
  console.error(`${failed} 项失败`);
  process.exit(1);
}
