// 用 headless Edge + CDP 渲染 Mermaid → PNG + SVG。
// mermaid 库内联自本机 node_modules（CDN 在本网络不可达），全程离线。
// 用法: node render_mmd.mjs <输入.mmd> <输出前缀>
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { spawn } from 'node:child_process';

const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const MERMAID = 'C:\\biancheng\\mewcode\\mewcode-java(1)\\node_modules\\mermaid\\dist\\mermaid.min.js';
const WORK = 'C:\\biancheng\\.scratch\\diagram';
const PROFILE = path.join(WORK, 'edge-profile');
const PORT = 9336;

const [, , inPath, outPrefix] = process.argv;
if (!inPath || !outPrefix) {
  console.error('用法: node render_mmd.mjs <输入.mmd> <输出前缀>');
  process.exit(2);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const src = fs.readFileSync(inPath, 'utf8');

async function getJson(p) {
  return new Promise((res, rej) => {
    http.get({ host: '127.0.0.1', port: PORT, path: p }, (r) => {
      let b = '';
      r.on('data', (d) => (b += d));
      r.on('end', () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } });
    }).on('error', rej);
  });
}

function buildHtml(src) {
  const lib = fs.readFileSync(MERMAID, 'utf8');
  return `<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>mermaid</title>
<style>html,body{margin:0;padding:0;background:#fff}#app{padding:24px;display:inline-block}</style>
</head><body>
<div id="app">渲染中…</div>
<script>window.__status='boot';window.__error='';</script>
<script>${lib}</script>
<script>window.__SRC = ${JSON.stringify(src)};</script>
<script>
(async function(){
  try {
    if (typeof mermaid === 'undefined') { window.__status='error'; window.__error='mermaid 未加载'; return; }
    window.__status='loading';
    mermaid.initialize({
      startOnLoad:false, theme:'base', securityLevel:'loose',
      themeVariables:{
        fontFamily:'-apple-system,"Segoe UI","Microsoft YaHei","PingFang SC",sans-serif',
        fontSize:'15px', primaryColor:'#ffffff', primaryTextColor:'#1a1a1a',
        primaryBorderColor:'#666666', lineColor:'#3d3d3d', tertiaryColor:'#f7f7f7'
      },
      flowchart:{ htmlLabels:true, curve:'basis', nodeSpacing:50, rankSpacing:60, padding:14 }
    });
    const out = await mermaid.render('g1', window.__SRC);
    document.getElementById('app').innerHTML = out.svg;
    const el = document.querySelector('#app svg');
    el.removeAttribute('height'); el.style.maxWidth='none';
    const bb = el.getBBox();
    window.__size = { w: Math.ceil(bb.width), h: Math.ceil(bb.height) };
    window.__status='ok';
  } catch(e) {
    window.__status='error';
    window.__error = String(e && e.message ? e.message : e);
  }
})();
</script>
</body></html>`;
}

const child = spawn(EDGE, [
  '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
  '--hide-scrollbars', `--remote-debugging-port=${PORT}`, `--user-data-dir=${PROFILE}`,
  '--window-size=2400,1800', 'about:blank',
], { stdio: 'ignore' });

let ws, id = 0;
const pending = new Map();
const send = (method, params = {}) => new Promise((res, rej) => {
  const i = ++id;
  pending.set(i, { res, rej });
  ws.send(JSON.stringify({ id: i, method, params }));
});

try {
  const tmpHtml = path.join(WORK, '_render_tmp.html');
  fs.writeFileSync(tmpHtml, buildHtml(src), 'utf8');
  console.log('页面大小:', (fs.statSync(tmpHtml).size / 1048576).toFixed(1), 'MB');

  let ver = null;
  for (let i = 0; i < 60; i++) {
    try { ver = await getJson('/json/version'); break; } catch { await sleep(250); }
  }
  if (!ver) throw new Error('devtools 未就绪');

  const targets = await getJson('/json/list');
  const page = targets.find((t) => t.type === 'page');
  ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });

  const logs = [];
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const { res, rej } = pending.get(m.id);
      pending.delete(m.id);
      m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
      return;
    }
    if (m.method === 'Runtime.exceptionThrown') {
      logs.push('EXC ' + (m.params.exceptionDetails?.exception?.description || m.params.exceptionDetails?.text));
    }
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
      logs.push('ERR ' + m.params.args.map((a) => a.value || a.description || '').join(' '));
    }
  };

  await send('Runtime.enable');
  await send('Page.enable');
  await send('Page.navigate', { url: 'file:///' + tmpHtml.replace(/\\/g, '/') });

  const evalJs = async (expr) => {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.text);
    return r.result.value;
  };

  let status = '';
  for (let i = 0; i < 160; i++) {
    status = await evalJs('window.__status || ""');
    if (status === 'ok' || status === 'error') break;
    await sleep(250);
  }
  if (status !== 'ok') {
    console.error('渲染失败，status =', status, '| error =', await evalJs('window.__error || "(空)"'));
    if (logs.length) console.error('日志:', logs.join('\n'));
    process.exit(1);
  }

  const size = await evalJs('window.__size');
  console.log('图形尺寸:', JSON.stringify(size));
  const svgRaw = await evalJs('document.querySelector("#app svg").outerHTML');
  fs.writeFileSync(outPrefix + '.svg', svgRaw, 'utf8');

  // 关键：把 svg 的 width/height 显式设为图形尺寸，否则 mermaid 留 white-space，
  // 截图会出现大片空白（第一版就踩了）。
  const W = Math.min(9000, Math.max(500, (size?.w || 1600) + 90));
  const H = Math.min(9000, Math.max(400, (size?.h || 1200) + 90));
  await evalJs(`(()=>{const s=document.querySelector('#app svg');
    s.setAttribute('width', ${size?.w || 1600});
    s.setAttribute('height', ${size?.h || 1200});
    s.style.width = '${size?.w || 1600}px';
    s.style.height = '${size?.h || 1200}px';
    const b=document.body; b.style.margin='0';
    document.getElementById('app').style.padding='24px';
  })()`);
  await send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: 2, mobile: false });
  await sleep(600);
  const shot = await send('Page.captureScreenshot', { format: 'png', fromSurface: true });
  fs.writeFileSync(outPrefix + '.png', Buffer.from(shot.data, 'base64'));
  console.log(`写出 ${outPrefix}.png (${W}x${H} @2x)`);
  if (logs.length) console.log('页面日志:', logs.join(' | '));
} catch (e) {
  console.error('FATAL', e.message);
  process.exitCode = 1;
} finally {
  try { ws && ws.close(); } catch {}
  try { child.kill(); } catch {}
  await sleep(300);
}
