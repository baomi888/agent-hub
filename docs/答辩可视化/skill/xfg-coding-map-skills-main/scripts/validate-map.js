#!/usr/bin/env node
/**
 * validate-map.js — 校验生成的代码地图 HTML 数据一致性
 *
 * 用法: node validate-map.js <生成的html路径>
 *
 * 检查项:
 *   1. MAP-DATA 数据区存在且包含 6 个必需数据结构
 *   2. TREE 每个节点的 doc id 都能在 DOCS 中找到
 *   3. DOCS 中未被任何树节点引用的孤儿卡（警告）
 *   4. TREE 每个 kw 关键词至少出现在一个 mermaid 图源码中（防联动断链）
 *   5. TOUR.tree 名称片段能在 TREE 某节点名称中找到
 *   6. TOUR.tab 的取值都在页面 .tab 定义中
 *   7. 页签 wrap / box / src 元素齐全
 *
 * 退出码: 0=通过(可含警告)  1=有错误
 */
const fs = require('fs');
const path = require('path');

const file = process.argv[2];
if (!file) { console.error('用法: node validate-map.js <生成的html路径>'); process.exit(1); }
const html = fs.readFileSync(path.resolve(file), 'utf8');

let errors = 0, warnings = 0;
const err = msg => { console.error('  ✗ [错误] ' + msg); errors++; };
const warn = msg => { console.log('  ⚠ [警告] ' + msg); warnings++; };
const ok = msg => console.log('  ✓ ' + msg);

/* ── 提取模块脚本 ── */
const scriptMatch = html.match(/<script type="module">([\s\S]*?)<\/script>/);
if (!scriptMatch) { console.error('未找到 module 脚本'); process.exit(1); }
const script = scriptMatch[1];

/* ── 提取 MAP-DATA 区间 ── */
const ds = script.indexOf('MAP-DATA-START');
const de = script.indexOf('MAP-DATA-END ═', ds);  /* 匹配带边框的结束标记，避免误命中注释提及 */
if (ds < 0 || de < 0) { console.error('未找到 MAP-DATA-START / MAP-DATA-END 数据区标记'); process.exit(1); }
const dataRegion = script.slice(ds, de);

/* 从数据区提取 const NAME = ... 块（数组做括号深度扫描，字符串做反引号配对） */
function extractBlock(name) {
  const m = dataRegion.match(new RegExp('\\nconst ' + name + ' ='));
  if (!m) return null;
  let i = m.index + m[0].length;
  while (i < dataRegion.length && /\s/.test(dataRegion[i])) i++;  /* 跳过 = 后空白 */
  const first = dataRegion[i];
  if (first === '`') {
    const end = dataRegion.indexOf('`', i + 1);
    return end < 0 ? null : dataRegion.slice(i, end + 1);
  }
  let depth = 0, j = i, inStr = null;
  while (j < dataRegion.length) {
    const c = dataRegion[j];
    if (inStr) {
      if (c === '\\') { j += 2; continue; }
      if (c === inStr) inStr = null;
    } else if (c === '\'' || c === '"' || c === '`') { inStr = c; }
    else if (c === '[' || c === '{') { depth++; }
    else if (c === ']' || c === '}') { depth--; if (depth === 0) return dataRegion.slice(i, j + 1); }
    j++;
  }
  return null;
}
function evalLiteral(lit) {
  try { return new Function('return (' + lit + ')')(); }
  catch (e) { return undefined; }
}

console.log('\n□ 数据结构检查');
const tree = evalLiteral(extractBlock('TREE'));
const docs = evalLiteral(extractBlock('DOCS'));
const tour = evalLiteral(extractBlock('TOUR'));
const sources = {};
for (const name of ['OVER_SRC', 'SEQ_SRC', 'FLOW_SRC']) {
  const block = extractBlock(name);
  if (!block) continue;
  const m = block.match(/^`([\s\S]*)`$/);
  sources[name] = m ? m[1] : null;
}
if (!Array.isArray(tree)) err('TREE 缺失或不是数组');
else ok('TREE：' + tree.length + ' 个顶层节点');
if (!Array.isArray(docs)) err('DOCS 缺失或不是数组');
else ok('DOCS：' + docs.length + ' 张说明卡');
if (!Array.isArray(tour)) err('TOUR 缺失或不是数组');
else ok('TOUR：' + tour.length + ' 个导览站点');
const srcCount = Object.values(sources).filter(Boolean).length;
if (srcCount === 0) err('未找到任何 mermaid 图源码（OVER_SRC / SEQ_SRC / FLOW_SRC）');
else ok('图源码：' + srcCount + ' 张');

if (errors) { console.log('\n数据结构缺失，终止后续检查。'); process.exit(1); }

/* ── 展平树 ── */
const flat = [];
(function walk(list, depth) {
  list.forEach(it => {
    flat.push({ ...it, depth });
    if (it.children) walk(it.children, depth + 1);
  });
})(tree, 0);

/* ── 页签检查 ── */
console.log('\n□ 页签结构检查');
const tabNames = [...html.matchAll(/class="tab[^"]*" data-tab="([^"]+)"/g)].map(m => m[1]);
if (!tabNames.length) err('页面中没有 .tab 页签');
else ok('页签：' + tabNames.join(', '));
tabNames.forEach(name => {
  ['wrap-' + name, 'box-' + name, 'src-' + name].forEach(id => {
    if (!html.includes('id="' + id + '"')) err('缺少 id="' + id + '" 元素');
  });
});
const tabToSrc = { over: 'OVER_SRC', seq: 'SEQ_SRC', flow: 'FLOW_SRC' };
tabNames.forEach(name => {
  if (tabToSrc[name] && !sources[tabToSrc[name]]) err('页签 ' + name + ' 对应的 ' + tabToSrc[name] + ' 未定义');
});

/* ── doc 引用一致性 ── */
console.log('\n□ 说明卡引用检查');
const docIds = new Set(docs.map(d => d.id));
const dup = docs.map(d => d.id).filter((id, i, a) => a.indexOf(id) !== i);
if (dup.length) err('DOCS 存在重复 id：' + [...new Set(dup)].join(', '));
else ok('DOCS id 无重复');
const usedDocs = new Set();
flat.forEach(n => {
  if (n.doc && !docIds.has(n.doc)) err('树节点「' + n.n + '」引用了不存在的 doc id: ' + n.doc);
  if (n.doc) usedDocs.add(n.doc);
});
ok('树 → 说明卡引用检查完成');
docs.forEach(d => {
  if (!usedDocs.has(d.id)) warn('说明卡「' + d.id + '」没有被任何树节点引用（孤儿卡，点击图/树时无法联动到它）');
});

/* ── kw ↔ 图源一致性（防断链） ── */
console.log('\n□ 关键词联动检查（kw 必须能在图源码文本中命中）');
const allSrc = Object.values(sources).filter(Boolean).join('\n');
let kwTotal = 0, kwMiss = 0;
flat.forEach(n => {
  (n.kw || []).forEach(k => {
    kwTotal++;
    if (!allSrc.includes(k)) {
      kwMiss++;
      err('节点「' + n.n + '」的 kw「' + k + '」在任何图源码中都找不到 → 联动会静默断链');
    }
  });
  if (n.doc && !(n.kw || []).length && n.t !== 'module' && n.t !== 'pkg') {
    warn('节点「' + n.n + '」有 doc 但没有 kw，点击它无法联动高亮图节点');
  }
});
if (!kwMiss) ok('全部 ' + kwTotal + ' 个 kw 均能命中图源码');

/* ── TOUR 检查 ── */
console.log('\n□ 导览站点检查');
tour.forEach((s, i) => {
  if (!tabNames.includes(s.tab)) err('TOUR[' + i + ']「' + (s.sl || s.title) + '」的 tab「' + s.tab + '」不是已定义页签');
  if (s.tree) {
    const hit = flat.some(n => n.n.includes(s.tree));
    if (!hit) err('TOUR[' + i + ']「' + (s.sl || s.title) + '」的 tree「' + s.tree + '」在树中找不到匹配节点');
  }
});
ok('TOUR tab/tree 引用检查完成');
if (tour.length < 5) warn('导览少于 5 站，主线叙事可能不完整（建议 6-10 站）');
if (tour.length > 12) warn('导览超过 12 站，跟随成本高（建议精简）');
if (tour.length && tour[tour.length - 1].tree !== null) warn('最后一站建议是 tree:null 的总结/自由探索站');

/* ── mermaid 语法隐患检查（静态可判定的两类高频坑） ── */
console.log('\n□ mermaid 语法隐患检查');
let mmIssues = 0;
for (const [name, src] of Object.entries(sources)) {
  if (!src) continue;
  const lines = src.split('\n');
  const first = (lines.find(l => l.trim()) || '').trim().toLowerCase();

  /* 坑 1：时序图 actor 标识符为独立 token `END`/`end`（不分大小写）会与 end 块关键字冲突，
     lexer 把它当关键字而非 ACTOR，报 "Expecting ACTOR, got 'end'"。
     只检查 actor 位置：participant/actor 声明与消息的收发双方（消息正文里的 end 不算） */
  if (first.startsWith('sequencediagram')) {
    const badEnd = [];
    lines.forEach((l, idx) => {
      let m = l.match(/^\s*(?:participant|actor)\s+([A-Za-z_][A-Za-z0-9_]*)/);
      if (m && /^end$/i.test(m[1])) { badEnd.push(idx + 1); return; }
      m = l.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:--?>?>|--?x|--?\)|-->|-x)\s*([A-Za-z_][A-Za-z0-9_]*)\s*:/);
      if (m && (/^end$/i.test(m[1]) || /^end$/i.test(m[2]))) badEnd.push(idx + 1);
    });
    if (badEnd.length) {
      mmIssues++;
      err(name + '：时序图 actor 标识符存在独立 END/end（行 ' + [...new Set(badEnd)].join(',') +
          '），与时序图 end 块关键字冲突导致解析失败 → 请改名为 ENDN / EndNode 等更长的标识符');
    }
  }

  /* 坑 2：flowchart 嵌套 subgraph（subgraph 里再写 subgraph）在打包的 mermaid.min.js 下报语法错误，
     需要「整体替换数据区」式生成时特别小心；规则：subgraph 深度不得超过 1 */
  if (first.startsWith('flowchart')) {
    let depth = 0, nested = [];
    lines.forEach((l, idx) => {
      const s = l.trim();
      if (/^subgraph\b/.test(s)) { depth++; if (depth > 1) nested.push(idx + 1); }
      else if (s === 'end' && depth > 0) depth--;
    });
    if (nested.length) {
      mmIssues++;
      err(name + '：flowchart 存在嵌套 subgraph（行 ' + [...new Set(nested)].join(',') +
          '），打包版 mermaid.min.js 无法解析 → 请拍平为同层 subgraph（命名前缀区分分组）');
    }
  }
}
if (!mmIssues) ok('无嵌套 subgraph / END 标识符冲突');

/* ── 汇总 ── */
console.log('\n──────────────────────────');
if (errors) { console.error('✗ 校验失败：' + errors + ' 个错误，' + warnings + ' 个警告'); process.exit(1); }
console.log('✓ 校验通过' + (warnings ? '（' + warnings + ' 个警告，建议处理）' : '，无警告'));
