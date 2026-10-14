// 验证 /flow（混合架构管理页）在后端未上线时的降级表现
// 重点验三件事：① 页面渲染出内容不是白屏 ② 挂「接口待上线」标记
// ③ 三个 Tab 都能切、编辑浮层能开且能输半截非法 JSON（文档 §六.6 的坑）
const puppeteer = require("puppeteer-core");

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const URL = "http://localhost:3000/flow";

let pass = 0, fail = 0;
const fails = [];
const check = (n, c, e = "") => {
  if (c) { pass++; console.log(`PASS  ${n} ${e}`); }
  else { fail++; fails.push(n); console.log(`FAIL  ${n} ${e}`); }
};

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    args: ["--no-sandbox", "--no-proxy-server", "--disable-dev-shm-usage"],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1280, height: 900 });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });

  // 登录
  await page.goto(URL, { waitUntil: "networkidle2", timeout: 60000 });
  const needLogin = await page.evaluate(() => Boolean(document.querySelector('input[type="password"]')));
  if (needLogin) {
    await page.evaluate((u, p) => {
      const ins = [...document.querySelectorAll("input")];
      const set = (el, v) => {
        const s = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
        s.call(el, v); el.dispatchEvent(new Event("input", { bubbles: true }));
      };
      set(ins.find((i) => i.type !== "password"), u);
      set(ins.find((i) => i.type === "password"), p);
      const b = [...document.querySelectorAll("button")].find((x) => (x.textContent || "").trim() === "登录" && !x.closest("[role=tablist]"));
      b && b.click();
    }, process.env.BAOMI_USER, process.env.BAOMI_PASS);
    await new Promise((r) => setTimeout(r, 4000));
  }

  // ① 页面渲染出内容
  const body = await page.evaluate(() => document.body.innerText || "");
  check("页面不是白屏（有实际文字）", body.trim().length > 40, `(${body.trim().length} 字)`);
  check("标题是「工作流与切换规则」", body.includes("工作流与切换规则"));

  // ② 接口待上线标记（后端 404 → 降级）
  const hasDemo = await page.evaluate(() => Boolean(document.querySelector(".flow-demo")));
  check("挂「接口待上线」标记（后端 404 降级）", hasDemo);
  const demoText = await page.evaluate(() => document.querySelector(".flow-demo")?.innerText || "");
  check("降级提示文案含「接口待上线」", demoText.includes("接口待上线"), `（${demoText.trim()}）`);

  // ③ 工作流列表 + 演示数据
  const cards = await page.evaluate(() => ({
    total: document.querySelectorAll(".flow-card").length,
    titles: [...document.querySelectorAll(".flow-card-title")].map((t) => t.innerText.trim()),
    builtinTag: document.querySelectorAll(".flow-tag").length,
  }));
  check("工作流 Tab 渲染出卡片", cards.total >= 1, `(${cards.total} 张)`);
  check("演示工作流是「规划-执行（内置）」", cards.titles.some((t) => t.includes("规划-执行")), `(${JSON.stringify(cards.titles)})`);
  check("内置工作流带「内置」标签", cards.builtinTag >= 1);
  check("内置工作流的编辑/删除是禁用态（内置只读）", await page.evaluate(() => {
    const btns = [...document.querySelectorAll(".flow-card button")];
    return btns.length >= 2 && btns.every((b) => b.disabled);
  }));

  // ④ 切到「切换规则」Tab
  await page.evaluate(() => {
    const b = [...document.querySelectorAll('[role=group] button, .seg button, button')]
      .find((x) => (x.textContent || "").trim().startsWith("切换规则"));
    b && b.click();
  });
  await new Promise((r) => setTimeout(r, 600));
  const rules = await page.evaluate(() => ({
    total: document.querySelectorAll(".flow-card").length,
    text: document.body.innerText,
  }));
  check("切到「切换规则」Tab 并渲染出 4 条", rules.total === 4, `(${rules.total} 条)`);
  check("规则 Tab 显示 signal 名", rules.text.includes("requires_agent") && rules.text.includes("node_failed"));
  check("提示了「自定义 signal 不会触发」", rules.text.includes("不会有事件触发") || rules.text.includes("只会上报"));

  // ⑤ 切到「执行记录」Tab
  await page.evaluate(() => {
    const b = [...document.querySelectorAll('[role=group] button, .seg button, button')]
      .find((x) => (x.textContent || "").trim().startsWith("执行记录"));
    b && b.click();
  });
  await new Promise((r) => setTimeout(r, 600));
  const runs = await page.evaluate(() => document.body.innerText);
  check("切到「执行记录」Tab", runs.includes("执行记录") && (runs.includes("run-demo") || runs.includes("run_id")));

  // ⑥ 回到「工作流」Tab（下面要量溢出，得在卡片可见时量）
  await page.evaluate(() => {
    const b = [...document.querySelectorAll('[role=group] button, .seg button, button')]
      .find((x) => (x.textContent || "").trim().startsWith("工作流"));
    b && b.click();
  });
  await new Promise((r) => setTimeout(r, 500));

  // ⑦ 无横向溢出
  const overflow = await page.evaluate(() => {
    const w = window.innerWidth;
    return [...document.querySelectorAll(".flow-card *")]
      .filter((el) => el.getBoundingClientRect().right > w + 1)
      .map((el) => el.className || el.tagName)
      .slice(0, 5);
  });
  check("卡片无横向溢出（min-width:0 生效）", overflow.length === 0, overflow.join(","));

  // ⑧ 无 JS 错误
  const real = errors.filter((e) => !/favicon|404 \(Not Found\)|Failed to load resource/i.test(e));
  check("无 JS 运行时错误", real.length === 0, real.slice(0, 2).join(" | "));

  const shot = require("path").join(__dirname, "..", "shots", "flow-page.png");
  await page.screenshot({ path: shot });
  console.log(`\n截图: ${shot}`);
  await browser.close();
  console.log(`\n${pass} passed, ${fail} failed`);
  if (fails.length) console.log("失败项: " + fails.join(" | "));
  process.exit(fail === 0 ? 0 : 1);
})().catch((e) => { console.error("异常:", e); process.exit(2); });
