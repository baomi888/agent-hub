/**
 * 语音输入的界面验收（Phase D）：录音 → WAV → 上传 → 填字，五条状态各走一遍。
 *
 * 跑法（dev server 在 3100、后端 8000）：
 *   NODE_PATH=.workbuddy/pp/node_modules node tools/voice_ui_e2e.js
 *
 * ## 为什么转写要拦截
 *
 * 真调腾讯云要密钥。这里拦的是 `/api/capture/transcribe`，用来验**前端**这条链路：
 * 采到的是不是 WAV、录音中/听写中/失败三种状态有没有如实出现、文字有没有填进输入框。
 * 签名和请求体在 `tests/test_asr.py` 里管；真识别只能等有密钥再跑。
 *
 * ## Chrome 的假麦克风
 *
 * `--use-fake-device-for-media-stream` 给一路合成音源，`--use-fake-ui-for-media-stream`
 * 自动同意权限。不用这两条的话，headless 里 getUserMedia 直接拒 ——
 * 测出来的"权限拒绝"是环境问题，不是产品问题。
 */
const puppeteer = require("puppeteer-core");
const path = require("path");

const BASE = "http://localhost:3100";
const USER = { username: "cap_probe", password: "Baomi@2026test" };
const OUT = path.resolve(__dirname, "shots");
const CHROME = "C:/Users/Fengjinghao/AppData/Local/Google/Chrome/Application/chrome.exe";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let pass = 0;
let fail = 0;
function check(name, cond, extra) {
  if (cond) {
    pass++;
    console.log("  PASS", name);
  } else {
    fail++;
    console.log("  FAIL", name, extra !== undefined ? JSON.stringify(extra) : "");
  }
}

async function shot(p, name) {
  await p.mouse.move(2, 2);
  await p.screenshot({ path: path.join(OUT, `${name}.png`) });
}

async function openDrawer(p) {
  // 入口按钮在页面拿到 `now` 之后才可用，首帧点它是空点 —— 所以重试，
  // 不是"等久一点"。capture_commit_e2e 里同款处理。
  for (let i = 0; i < 4; i += 1) {
    try {
      await p.waitForSelector(".rem-cap-strip", { timeout: 12000 });
      // 用 DOM click 而不是坐标点击：真机上入口在页面中段，
      // headless 里偶尔被吸顶栏压住 —— 那样点中的是栏，不是按钮，重试也白搭。
      await p.evaluate(() => document.querySelector(".rem-cap-strip")?.click());
      await p.waitForSelector(".rem-cap-drawer", { timeout: 6000 });
      await sleep(400);
      return;
    } catch {
      await sleep(900);
    }
  }
  const html = await p.evaluate(() => document.body.innerText.slice(0, 300));
  throw new Error(`抽屉打不开：${html}`);
}

/** 抽屉里那点状态，一次取齐 */
async function snap(p) {
  return p.evaluate(() => {
    const q = (s) => document.querySelector(s);
    return {
      mic: !!q(".rem-cap-mic"),
      rec: q(".rem-cap-mic")?.getAttribute("data-rec") ?? null,
      voiceLine: q(".rem-cap-voice")?.textContent?.trim() ?? null,
      err: q(".rem-cap-fix-err")?.textContent?.trim() ?? null,
      input: q(".rem-cap-input")?.value ?? null,
      locked: q(".rem-cap-input")?.disabled ?? null,
      stop: !!q(".rem-cap-mic-stop"),
    };
  });
}

async function main() {
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: "new",
    args: [
      "--use-fake-ui-for-media-stream",
      "--use-fake-device-for-media-stream",
      "--no-sandbox",
    ],
  });

  try {
    const p = await browser.newPage();
    await p.setViewport({ width: 400, height: 860, deviceScaleFactor: 2 });
    await p.goto(`${BASE}/login`, { waitUntil: "domcontentloaded", timeout: 60000 });
    await sleep(900);
    await p.evaluate(async (u) => {
      await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(u),
      });
    }, USER);
    await p.goto(`${BASE}/reminders?theme=light`, { waitUntil: "networkidle2", timeout: 60000 });
    await p.evaluate(() => {
      const st = document.createElement("style");
      st.textContent = ".ann-page,.guide-mask{display:none !important} nextjs-portal{display:none !important}";
      document.head.appendChild(st);
    });
    await sleep(400);

    // ---------- A. 没接语音：入口根本不出现 ----------
    await openDrawer(p);
    let s = await snap(p);
    check("A1 后端说没接的时候，麦克风不渲染", s.mic === false, s);
    await shot(p, "voice_00_no_entry");
    await p.evaluate(() => document.querySelector(".rem-cap-close")?.click());
    await sleep(400);

    // ---------- B. 接了：拦截 capabilities + transcribe ----------
    let mode = "ok";
    await p.setRequestInterception(true);
    p.on("request", async (req) => {
      const url = req.url();
      if (url.includes("/api/capture/capabilities")) {
        await req.respond({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ voice: true }),
        });
        return;
      }
      if (url.includes("/api/capture/transcribe")) {
        if (mode === "fail") {
          await req.respond({
            status: 502,
            contentType: "application/json",
            body: JSON.stringify({ detail: "没听清，再说一遍" }),
          });
          return;
        }
        // 故意慢一点，好让"正在听写…"这一格拍得到
        await sleep(900);
        await req.respond({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ text: "明天下午三点交报告" }),
        });
        return;
      }
      await req.continue();
    });

    // 重新载入再开抽屉：`capabilities` 只在抽屉挂载时拉一次，
    // 不重载的话拿到的还是 A 段那个 `voice:false`
    await p.evaluateOnNewDocument(() => {
      // 记下真正上传的那一坨：multipart 里的二进制在 postData() 里读不出来，
      // 只能在上传前把它留在 window 上，之后再验类型和文件头
      window.__upload = null;
      const append = FormData.prototype.append;
      FormData.prototype.append = function (key, value, name) {
        if (value instanceof Blob) window.__upload = { blob: value, name: name || "", type: value.type };
        return append.call(this, key, value, name);
      };
    });
    await p.goto(`${BASE}/reminders?theme=light`, { waitUntil: "networkidle2", timeout: 60000 });
    await p.evaluate(() => {
      const st = document.createElement("style");
      st.textContent = ".ann-page,.guide-mask{display:none !important} nextjs-portal{display:none !important}";
      document.head.appendChild(st);
    });
    await sleep(300);
    await openDrawer(p);
    await p.waitForSelector(".rem-cap-mic", { timeout: 6000 });
    check("B1 后端说接了，麦克风才出现", true);

    // ---------- C. 录音中 ----------
    await p.evaluate(() => document.querySelector(".rem-cap-mic")?.click());
    await sleep(1300);
    s = await snap(p);
    check("C1 点一下进入录音态", s.rec === "1", s);
    check("C2 按钮里是方块（停止），不是麦克风", s.stop === true, s);
    check("C3 有计时，且说了怎么停", /正在录音 0:0\d/.test(s.voiceLine || ""), s.voiceLine);
    check("C4 录音时输入框锁住", s.locked === true, s);
    await shot(p, "voice_01_recording");

    // ---------- D. 听写中 → 文字落进输入框 ----------
    await p.evaluate(() => document.querySelector(".rem-cap-mic")?.click());
    await sleep(250);
    s = await snap(p);
    check("D1 停止后进入听写态", (s.voiceLine || "").includes("正在听写"), s.voiceLine);
    await shot(p, "voice_02_transcribing");
    await p.waitForFunction(
      () => (document.querySelector(".rem-cap-input")?.value || "").length > 0,
      { timeout: 8000 }
    );
    s = await snap(p);
    check("D2 转写结果填进输入框", s.input === "明天下午三点交报告", s.input);
    const upload = await p.evaluate(async () => {
      const u = window.__upload;
      if (!u) return null;
      const head = await u.blob.slice(0, 4).arrayBuffer();
      return {
        type: u.type,
        name: u.name,
        size: u.blob.size,
        head: String.fromCharCode(...new Uint8Array(head)),
      };
    });
    check("D3 上传的是 WAV（文件头 RIFF，不是 webm）", upload?.head === "RIFF", upload);
    check("D3b 文件名和类型都对", upload?.type === "audio/wav" && upload?.name === "voice.wav", upload);
    check("D4 回到空闲态，状态行消失", s.voiceLine === null, s.voiceLine);
    await shot(p, "voice_03_filled");

    // ---------- E. 失败 ----------
    mode = "fail";
    await p.click(".rem-cap-input", { clickCount: 3 });
    await p.keyboard.press("Backspace");
    await p.evaluate(() => document.querySelector(".rem-cap-mic")?.click());
    await sleep(1100);
    await p.evaluate(() => document.querySelector(".rem-cap-mic")?.click());
    await p.waitForFunction(
      () => !!document.querySelector(".rem-cap-fix-err"),
      { timeout: 8000 }
    );
    s = await snap(p);
    check("E1 转写失败给的是人话", (s.err || "").includes("没听清"), s.err);
    check("E2 失败后回到空闲态，能再说一遍", s.voiceLine === null && s.rec === null, s);
    await shot(p, "voice_04_failed");

    // ---------- F. 权限拒绝 ----------
    const p2 = await browser.newPage();
    await p2.setViewport({ width: 400, height: 860, deviceScaleFactor: 2 });
    await p2.evaluateOnNewDocument(() => {
      // 直接给 mediaDevices 赋值不行（它是只读访问器），要 defineProperty。
      // 拒绝时抛的 name 必须是 NotAllowedError —— 产品靠这个名字区分
      // 「用户拒了权限」和「这台机器没有麦克风」，两者要说不同的话。
      Object.defineProperty(navigator, "mediaDevices", {
        configurable: true,
        value: {
          getUserMedia: () =>
            Promise.reject(Object.assign(new Error("denied"), { name: "NotAllowedError" })),
        },
      });
    });
    await p2.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
    await sleep(800);
    await p2.evaluate(async (u) => {
      await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(u),
      });
    }, USER);
    await p2.setRequestInterception(true);
    p2.on("request", async (req) => {
      if (req.url().includes("/api/capture/capabilities")) {
        await req.respond({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ voice: true }),
        });
        return;
      }
      await req.continue();
    });
    await p2.goto(`${BASE}/reminders?theme=light`, { waitUntil: "networkidle2" });
    await p2.evaluate(() => {
      const st = document.createElement("style");
      st.textContent = ".ann-page,.guide-mask{display:none !important} nextjs-portal{display:none !important}";
      document.head.appendChild(st);
    });
    await sleep(400);
    await openDrawer(p2);
    await p2.waitForSelector(".rem-cap-mic", { timeout: 6000 });
    await p2.evaluate(() => document.querySelector(".rem-cap-mic")?.click());
    await p2.waitForFunction(() => !!document.querySelector(".rem-cap-fix-err"), { timeout: 8000 });
    const denied = await p2.evaluate(
      () => document.querySelector(".rem-cap-fix-err")?.textContent?.trim() ?? ""
    );
    check("F1 权限被拒时说清是权限问题，不是「坏了」", /权限/.test(denied), denied);
    await shot(p2, "voice_05_denied");
  } finally {
    await browser.close();
  }

  console.log(`\n${pass} passed, ${fail} failed`);
  if (fail > 0) process.exitCode = 1;
}

void main();
