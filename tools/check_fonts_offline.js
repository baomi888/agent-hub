// 验证中文 Web 字体已离线化：去掉外链 + 本地 woff2 可用 + LXGW 真的生效
const puppeteer = require("puppeteer-core");

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const URL = "http://localhost:3000/";

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    args: ["--no-sandbox", "--no-proxy-server", "--disable-dev-shm-usage"],
  });
  const page = await browser.newPage();
  const requests = [];
  page.on("request", (r) => requests.push(r.url()));

  await page.goto(URL, { waitUntil: "networkidle2", timeout: 60000 });

  // 主动加载三个字族，确认真实可从本地取到字形
  const fontResult = await page.evaluate(async () => {
    await document.fonts.load('400 48px "LXGW WenKai Screen"');
    await document.fonts.load('400 16px "Noto Sans SC"');
    await document.fonts.load('400 16px "Noto Serif SC"');
    await document.fonts.ready;
    const check = {
      lxgw: document.fonts.check('400 48px "LXGW WenKai Screen"'),
      notoSans: document.fonts.check('400 16px "Noto Sans SC"'),
      notoSerif: document.fonts.check('400 16px "Noto Serif SC"'),
    };
    // canvas 实测：LXGW 手写体字形宽度必须不同于系统 serif fallback，才证明真用上了
    const ctx = document.createElement("canvas").getContext("2d");
    const text = "知穗Agent知识库场景";
    ctx.font = '48px "LXGW WenKai Screen", serif';
    const wL = ctx.measureText(text).width;
    ctx.font = "48px serif";
    const wF = ctx.measureText(text).width;
    return { check, wL: Math.round(wL), wF: Math.round(wF), lxgwActive: Math.abs(wL - wF) > 0.5 };
  });

  // head 里的 stylesheet link 是否换成 local fonts.css，且无外链字体
  const headLink = await page.evaluate(() => {
    const links = [...document.querySelectorAll('link[rel="stylesheet"]')].map((l) => l.href);
    return {
      hasLocalFonts: links.some((h) => h.includes("/fonts/fonts.css")),
      externalFontLinks: links.filter((h) => h.includes("jsdelivr") || h.includes("feishu")),
    };
  });

  const externalReq = requests.filter((u) => u.includes("jsdelivr") || u.includes("feishu"));
  const fontReqOk = requests.filter((u) => u.includes("/fonts/") && u.endsWith(".woff2"));

  const pass =
    headLink.hasLocalFonts &&
    headLink.externalFontLinks.length === 0 &&
    externalReq.length === 0 &&
    fontResult.check.lxgw &&
    fontResult.check.notoSans &&
    fontResult.check.notoSerif &&
    fontResult.lxgwActive;

  console.log(
    JSON.stringify(
      {
        pass,
        headLink,
        externalReqCount: externalReq.length,
        localWoff2Requested: fontReqOk.length,
        fontResult,
        totalRequests: requests.length,
      },
      null,
      2
    )
  );
  await browser.close();
  process.exit(pass ? 0 : 1);
})().catch((e) => {
  console.error("验证脚本异常:", e);
  process.exit(2);
});
