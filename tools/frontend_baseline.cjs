const { spawn } = require('node:child_process');
const assert = require('node:assert/strict');
const path = require('node:path');
(async () => {
  const cwd = path.resolve(__dirname, '../frontend');
  const child = spawn(process.execPath, ['node_modules/next/dist/bin/next', 'start', '--hostname', '127.0.0.1', '--port', '43127'], {
    cwd, windowsHide: true, env: {...process.env, NEXT_TELEMETRY_DISABLED:'1'}, stdio: ['ignore','pipe','pipe']
  });
  let log = '';
  child.stdout.on('data', b => log += b);
  child.stderr.on('data', b => log += b);
  try {
    let ready = false;
    for (let i = 0; i < 100; i++) {
      if (child.exitCode !== null) throw new Error(log);
      if (log.includes('Ready in')) { ready = true; break; }
      await new Promise(r => setTimeout(r, 200));
    }
    assert.ok(ready, log);
    for (const route of ['/login','/settings']) {
      const response = await fetch('http://127.0.0.1:43127' + route);
      assert.equal(response.status, 200);
      const html = await response.text();
      assert.ok(html.includes('知穗'), route + ' missing application content');
      console.log('PASS HTTP page smoke: ' + route);
    }
  } finally {
    child.kill();
    await new Promise(r => child.once('exit', r));
  }
})().catch(e => { console.error(e); process.exitCode = 1; });
