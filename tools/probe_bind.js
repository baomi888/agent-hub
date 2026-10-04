// 线上「资料库勾选框点了没反应」复现探针。
// 用法：NODE_PATH=<workspace>/node_modules <node22> tools/probe_bind.js
// 只读 + 只改一个临时会话（跑完自动删），不碰用户的知识库。
const puppeteer = require('puppeteer-core');
const path = require('path');

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const BASE = 'http://8.163.62.25:3000';
const OUT = path.resolve(__dirname, '..', 'docs', 'screenshots');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// 记录所有打到 /api/sessions 的请求
function watch(page, bag) {
  page.on('request', (r) => {
    const u = r.url();
    if (u.includes('/api/sessions')) bag.push(`${r.method()} ${u}`);
    if (r.method() === 'PATCH') bag.push(`PATCH-BODY ${r.postData() || ''}`);
  });
  page.on('response', (r) => {
    const u = r.url();
    if (u.includes('/api/sessions') && r.request().method() === 'PATCH') {
      bag.push(`PATCH-RESP ${r.status()} ${u}`);
    }
  });
}

async function panelState(page) {
  return page.evaluate(() => {
    const rows = Array.from(document.querySelectorAll('.kb-row'));
    const checks = Array.from(document.querySelectorAll('.check'));
    const aside = document.querySelector('aside[aria-label="资料库"]');
    return {
      asideExists: !!aside,
      asideInert: aside ? aside.hasAttribute('inert') : null,
      asideClass: aside ? aside.className : null,
      rowCount: rows.length,
      checkCount: checks.length,
      onCount: checks.filter((c) => c.classList.contains('on')).length,
      firstCheckBox: checks[0] ? checks[0].getBoundingClientRect().toJSON() : null,
      firstCheckSvgBox: checks[0] && checks[0].querySelector('svg')
        ? checks[0].querySelector('svg').getBoundingClientRect().toJSON()
        : null,
      kbText: aside ? aside.innerText.replace(/\n+/g, ' | ').slice(0, 260) : null,
    };
  });
}

(async () => {
  const fs = require('fs');
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: 'new',
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
    defaultViewport: { width: 1440, height: 900, deviceScaleFactor: 1.5 },
  });
  const page = await browser.newPage();
  page.setDefaultTimeout(60000);
  const bag = [];
  watch(page, bag);

  await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 90000 });
  await sleep(8000);
  await page.keyboard.press('Escape').catch(() => {});
  await sleep(600);

  // ---------- A：刚进页面、没点任何会话时，直接勾资料库 ----------
  const before = await panelState(page);
  console.log('=== A. 刚进页面（未选中任何会话）===');
  console.log('右侧面板:', JSON.stringify(before, null, 1));

  if (before.checkCount > 0) {
    bag.length = 0;
    await page.click('.check').catch((e) => console.log('click 失败:', e.message));
    await sleep(2500);
    const after = await panelState(page);
    console.log('点击后 on 数量:', after.onCount);
    console.log('点击后发出的 /api/sessions 请求:', JSON.stringify(bag));
    console.log('面板文案:', after.kbText);
    await page.screenshot({ path: path.join(OUT, 'bind_bug_A_no_session.png') });
    console.log('截图: docs/screenshots/bind_bug_A_no_session.png');
  } else {
    console.log('!! 页面上找不到 .check —— 资料库面板没渲染出来');
    await page.screenshot({ path: path.join(OUT, 'bind_bug_A_nocheck.png') });
  }

  // ---------- B：先点一个会话，再勾 ----------
  console.log('\n=== B. 先点侧栏会话，再勾 ===');
  const clickedSession = await page.evaluate(() => {
    const s = document.querySelector('.session');
    if (!s) return false;
    s.click();
    return true;
  });
  console.log('点到会话:', clickedSession);
  await sleep(3000);
  bag.length = 0;
  const b0 = await panelState(page);
  console.log('面板 check 数:', b0.checkCount, ' 当前 on:', b0.onCount);
  if (b0.checkCount > 0) {
    await page.click('.check').catch((e) => console.log('click 失败:', e.message));
    await sleep(2500);
    const b1 = await panelState(page);
    console.log('点击后 on 数量:', b1.onCount);
    console.log('点击后发出的 /api/sessions 请求:', JSON.stringify(bag));
    await page.screenshot({ path: path.join(OUT, 'bind_bug_B_with_session.png') });
    console.log('截图: docs/screenshots/bind_bug_B_with_session.png');
    // 还原：取消绑定，避免污染线上状态
    if (b1.onCount > 0) {
      await page.click('.check.on').catch(() => {});
      await sleep(1500);
      console.log('已还原（解绑）');
    }
  }

  await browser.close();
  console.log('[done]');
})().catch((e) => {
  console.error('FAILED:', e.message);
  process.exit(1);
});
