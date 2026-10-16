// /flow 页面「后端已上线」真实数据端到端验证
//
// 与 check_flow_page.js 的分工：
//   - check_flow_page.js  验「后端 404 / 未上线」的降级兜底表现（演示数据 + 接口待上线标记）；
//   - 本脚本            验「后端已上线」的正路：真实数据渲染 + 编辑保存闭环 + 规则/执行记录 Tab。
//
// ★ 本轮核心断言：编辑工作流定义后，保存的必须是真的改过的内容。
//   page.tsx 的编辑器是「非受控 textarea + 失焦只校验」写法。如果保存时从
//   state（editing.definition，从未被 textarea 更新过）取值而不是从 DOM 现读，
//   用户改了半天的 JSON 会被静默丢弃：界面弹「已保存」，落库的还是旧内容。
//   脚本用「带时间戳的节点名」做探针（每次运行都不同，旧内容蒙混不过去）：
//   改 → 保存 → 刷新 → 重开编辑器，探针必须在。
//
// 端口说明：本机 3000 被 VMware NAT 服务（vmnat.exe）独占
//   （SO_EXCLUSIVEADDRUSE → EACCES，不能共存也不该杀），前端 dev 用 3100。
//
// 前置条件：后端 :8000 已起；测试账号已注册（经 BAOMI_USER / BAOMI_PASS 环境变量传入），
//   且已有 1 个自定义工作流「UI 验证·三段式研究流」（本脚本用名称定位它）。
const puppeteer = require("puppeteer-core");
const path = require("path");

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const BASE = process.env.BAOMI_BASE || "http://localhost:3100";
const USER = process.env.BAOMI_USER || "";
const PASS = process.env.BAOMI_PASS || "";
if (!USER || !PASS) { console.error("缺少 BAOMI_USER / BAOMI_PASS 环境变量（测试账号）"); process.exit(1); }
const SHOT_DIR = path.join(__dirname, "..", "docs", "实机截图", "2026-10-14");

let pass = 0, fail = 0;
const fails = [];
const check = (n, c, e = "") => {
  if (c) { pass++; console.log(`PASS  ${n} ${e}`); }
  else { fail++; fails.push(n); console.log(`FAIL  ${n} ${e}`); }
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// 探针：每次运行都不同，无法被「保存了旧内容」蒙混过关
const PROBE = `起草·改@${String(Date.now()).slice(-6)}`;

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    args: ["--no-sandbox", "--no-proxy-server", "--proxy-bypass-list=*", "--disable-dev-shm-usage"],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1280, height: 900 });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });

  // ================= 登录 =================
  // 「降级标记曾经出现过吗」监听器：在每次页面加载时注入。
  // 比快照断言强 —— loading / 未登录窗口里的降级闪现也逃不掉：
  // 修复前未登录时页面会发 401 请求 → isDemo → .flow-demo 渲染（虽然被登录门盖着）；
  // 修复后未登录不发请求、加载期显示「正在加载…」，全程 0 次。
  await page.evaluateOnNewDocument(() => {
    window.__flowDemoSeen = 0;
    // ⚠️ evaluateOnNewDocument 跑在 document 刚创建时：documentElement 可能还不存在，
    // 直接 observe 会抛 TypeError（跑错会被「无 JS 错误」断言抓个正着）——
    // 先探测，没有就等 DOMContentLoaded（一次性）
    const install = () => {
      if (!document.documentElement) {
        document.addEventListener("DOMContentLoaded", install, { once: true });
        return;
      }
      new MutationObserver(() => {
        if (document.querySelector(".flow-demo")) window.__flowDemoSeen++;
      }).observe(document.documentElement, { childList: true, subtree: true });
    };
    install();
  });

  await page.goto(`${BASE}/flow`, { waitUntil: "networkidle2", timeout: 90000 });
  // 等登录门就位再填表：auth 探活完成前页面显示「正在确认登录状态」，没有输入框；
  // goto 返回时可能还在探活中，直接检测会漏掉登录门
  const needLogin = await page.waitForSelector('input[type="password"]', { timeout: 20000 })
    .then(() => true).catch(() => false);
  if (needLogin) {
    await page.evaluate((u, p) => {
      const ins = [...document.querySelectorAll("input")];
      const set = (el, v) => {
        const s = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
        s.call(el, v); el.dispatchEvent(new Event("input", { bubbles: true }));
      };
      set(ins.find((i) => i.type !== "password"), u);
      set(ins.find((i) => i.type === "password"), p);
      const b = [...document.querySelectorAll("button")].find(
        (x) => (x.textContent || "").trim() === "登录" && !x.closest("[role=tablist]")
      );
      b && b.click();
    }, USER, PASS);
  }
  // 等工作流卡片出现（登录成功后 AuthGate 才挂载页面）
  await page.waitForFunction(() => document.querySelectorAll(".flow-card").length > 0, { timeout: 30000 })
    .catch(() => {});
  await sleep(500);

  // ================= A. 真实数据模式 =================
  const bodyText = await page.evaluate(() => document.body.innerText || "");
  check("页面不是白屏", bodyText.trim().length > 40, `(${bodyText.trim().length} 字)`);
  check("标题是「工作流与切换规则」", bodyText.includes("工作流与切换规则"));

  const demoState = await page.evaluate(() => ({
    now: Boolean(document.querySelector(".flow-demo")),
    ever: window.__flowDemoSeen || 0,
  }));
  check("★ 真实数据模式：无「接口待上线」降级标记", !demoState.now);
  check("★ 降级标记从未出现过（登录 / 加载窗口期也没有闪现）", demoState.ever === 0, `出现过 ${demoState.ever} 次`);

  const wf = await page.evaluate(() => {
    const cards = [...document.querySelectorAll(".flow-card")];
    return {
      n: cards.length,
      items: cards.map((c) => ({
        title: (c.querySelector(".flow-card-title")?.innerText || "").trim(),
        btns: [...c.querySelectorAll("button")].map((b) => ({ t: b.textContent.trim(), d: b.disabled })),
      })),
    };
  });
  const customCard = wf.items.find((c) => c.title.includes("UI 验证"));
  const builtinCard = wf.items.find((c) => c.title.includes("规划-执行"));
  check("工作流列表 = 2 张（1 内置 + 1 自定义）", wf.n === 2, `(${wf.n} 张)`);
  check("自定义工作流在列表里（真实数据）", !!customCard, customCard ? "" : JSON.stringify(wf.items.map((i) => i.title)));
  check("内置工作流也在列表", !!builtinCard);
  check("内置只读：编辑/删除均 disabled", !!builtinCard && builtinCard.btns.every((b) => b.d));
  check("自定义可操作：编辑 enabled", !!customCard && customCard.btns.some((b) => b.t === "编辑" && !b.d));

  const tabCounts = await page.evaluate(() =>
    [...document.querySelectorAll("button")]
      .map((b) => (b.textContent || "").replace(/\s+/g, " ").trim())
      .filter((t) => /^(工作流|切换规则|执行记录)\s*\d+$/.test(t))
  );
  check("Tab 计数用真实数字（工作流 2 = 内置+自定义）", tabCounts.includes("工作流 2"), JSON.stringify(tabCounts));

  await page.screenshot({ path: path.join(SHOT_DIR, "flow-live-wf.png") });

  // ================= B. 编辑保存闭环（核心） =================
  const openEditor = async () => {
    const found = await page.evaluate(() => {
      const cards = [...document.querySelectorAll(".flow-card")];
      const c = cards.find((x) => (x.querySelector(".flow-card-title")?.innerText || "").includes("UI 验证"));
      if (!c) return false;
      const btn = [...c.querySelectorAll("button")].find((b) => b.textContent.trim() === "编辑");
      if (!btn) return false;
      btn.click();
      return true;
    });
    if (!found) throw new Error("找不到「UI 验证·三段式研究流」卡片或它的编辑按钮");
    await page.waitForSelector(".flow-modal", { timeout: 5000 });
  };

  await openEditor();
  const editorOk = await page.evaluate(() => {
    const t = document.querySelector(".flow-editor");
    if (!t) return { has: false };
    try { const d = JSON.parse(t.value); return { has: true, nodes: (d.nodes || []).length }; }
    catch { return { has: true, bad: true }; }
  });
  check("编辑器打开且内容是合法 JSON（3 节点）", editorOk.has && !editorOk.bad && editorOk.nodes === 3, JSON.stringify(editorOk));

  // ① 输半截非法 JSON → 失焦 → 报错 + 保存禁用（文档 §六.6 的坑）
  await page.evaluate(() => {
    const t = document.querySelector(".flow-editor");
    t.focus(); t.value = '{"nodes": ['; t.blur();
  });
  await sleep(500);
  const afterBad = await page.evaluate(() => ({
    err: document.querySelector(".flow-err")?.innerText || "",
    saveDisabled: (() => {
      const b = [...document.querySelectorAll(".flow-modal-actions button")].find((x) => x.textContent.trim() === "保存");
      return b ? b.disabled : null;
    })(),
  }));
  check("非法 JSON：显示「JSON 不合法」", afterBad.err.includes("JSON 不合法"), `(${afterBad.err.slice(0, 40)})`);
  check("非法 JSON：保存按钮禁用（防止落库非法定义）", afterBad.saveDisabled === true);

  // ② 改成合法且带探针的 definition → 保存
  await page.evaluate((probeName) => {
    const t = document.querySelector(".flow-editor");
    const d = {
      id: "ui_check_flow",
      name: "UI 验证·三段式研究流",
      version: 1,
      entry: "collect",
      nodes: [
        { id: "collect", type: "llm", name: "信息收集", config: { prompt: "收集资料：{question}", output_key: "notes" } },
        { id: "draft", type: "llm", name: probeName, config: { prompt: "基于 {shared.notes} 起草", output_key: "draft" } },
        { id: "polish", type: "agent", name: "润色收尾", config: { goal: "润色 {shared.draft}", output_key: "final" } },
      ],
      edges: [
        { source: "collect", target: "draft" },
        { source: "draft", target: "polish" },
      ],
    };
    t.focus(); t.value = JSON.stringify(d, null, 2); t.blur();
  }, PROBE);
  await sleep(500);
  const afterGood = await page.evaluate(() => ({
    err: document.querySelector(".flow-err")?.innerText || "",
    saveDisabled: (() => {
      const b = [...document.querySelectorAll(".flow-modal-actions button")].find((x) => x.textContent.trim() === "保存");
      return b ? b.disabled : null;
    })(),
  }));
  check("修正后：错误清除且保存可用", !afterGood.err && afterGood.saveDisabled === false, JSON.stringify(afterGood));

  await page.evaluate(() => {
    [...document.querySelectorAll(".flow-modal-actions button")].find((x) => x.textContent.trim() === "保存").click();
  });
  await page.waitForFunction(() => !document.querySelector(".flow-modal"), { timeout: 10000 }).catch(() => {});
  await sleep(800);
  const savedOk = await page.evaluate(() => document.body.innerText.includes("已保存"));
  check("保存后弹「已保存」且浮层关闭", savedOk);

  // ③ 刷新 + 重开编辑器 → 探针必须在（核心断言）
  await page.reload({ waitUntil: "networkidle2", timeout: 90000 });
  await page.waitForSelector(".flow-card", { timeout: 30000 });
  await sleep(500);
  const demoAfterReload = await page.evaluate(() => ({
    now: Boolean(document.querySelector(".flow-demo")),
    ever: window.__flowDemoSeen || 0,
  }));
  check("刷新后同样无降级标记（含加载窗口期）", !demoAfterReload.now && demoAfterReload.ever === 0,
    `now=${demoAfterReload.now} ever=${demoAfterReload.ever}`);
  await openEditor();
  const persisted = await page.evaluate(() => document.querySelector(".flow-editor")?.value || "");
  check("★ 编辑保存真的持久化了（刷新后探针仍在）", persisted.includes(PROBE), `探针=${PROBE}`);
  await page.evaluate(() => {
    [...document.querySelectorAll(".flow-modal-actions button")].find((x) => x.textContent.trim() === "取消")?.click();
  });
  await sleep(400);

  // ================= C. 切换规则 Tab =================
  await page.evaluate(() => {
    [...document.querySelectorAll("button")].find((x) => (x.textContent || "").trim().startsWith("切换规则"))?.click();
  });
  await sleep(900);
  const ruleState = await page.evaluate(() => {
    const cards = [...document.querySelectorAll(".flow-card")];
    const custom = cards.find((c) => c.innerText.includes("ui_check_rule"));
    return {
      n: cards.length,
      custom: !!custom,
      customBtns: custom ? [...custom.querySelectorAll("button")].map((b) => b.textContent.trim()) : [],
      builtinWithDelete: cards.filter(
        (c) => c.innerText.includes("内置") && [...c.querySelectorAll("button")].some((b) => b.textContent.includes("删覆盖"))
      ).length,
      text: document.body.innerText,
    };
  });
  check("规则 Tab = 5 条（4 内置 + 1 自定义）", ruleState.n === 5, `(${ruleState.n} 条)`);
  check("自定义规则有「删覆盖」按钮", ruleState.custom && ruleState.customBtns.includes("删覆盖"));
  check("内置规则没有「删覆盖」（覆盖机制语义正确）", ruleState.builtinWithDelete === 0);
  check("提示文案：自定义 signal 不会有事件触发", ruleState.text.includes("不会有事件触发"));
  await page.screenshot({ path: path.join(SHOT_DIR, "flow-live-rule.png") });

  // ================= D. 执行记录 Tab =================
  await page.evaluate(() => {
    [...document.querySelectorAll("button")].find((x) => (x.textContent || "").trim().startsWith("执行记录"))?.click();
  });
  await sleep(900);
  const runState = await page.evaluate(() => ({
    text: document.body.innerText,
    cards: document.querySelectorAll(".flow-card").length,
    hasRealRunId: /[0-9a-f]{32}/.test(document.body.innerText),
  }));
  check("执行记录：不出现演示数据 run-demo-001", !runState.text.includes("run-demo-001"));
  check("执行记录：≥1 条真实 run（32 位 hex run_id）", runState.cards >= 1 && runState.hasRealRunId, `(${runState.cards} 条)`);

  const logClicked = await page.evaluate(() => {
    const b = [...document.querySelectorAll(".flow-card button")].find((x) => x.textContent.trim() === "看日志");
    if (b) { b.click(); return true; }
    return false;
  });
  await sleep(1500);
  const logToast = await page.evaluate(() => document.body.innerText);
  check("「看日志」返回真实结果（非「取日志失败」）",
    logClicked && !logToast.includes("取日志失败") && /共 \d+ 条日志|没有日志/.test(logToast));
  await page.screenshot({ path: path.join(SHOT_DIR, "flow-live-run.png") });

  // ================= E. 横向溢出 + JS 错误 =================
  const overflow = await page.evaluate(() => {
    const w = window.innerWidth;
    return [...document.querySelectorAll(".flow-card *, .flow-tabs *")]
      .filter((el) => el.getBoundingClientRect().right > w + 1)
      .map((el) => el.className || el.tagName)
      .slice(0, 5);
  });
  check("执行记录 Tab 无横向溢出", overflow.length === 0, overflow.join(","));

  const real = errors.filter((e) => !/favicon|404 \(Not Found\)|Failed to load resource/i.test(e));
  check("无 JS 运行时错误", real.length === 0, real.slice(0, 2).join(" | "));

  console.log(`\n截图目录: ${SHOT_DIR}`);
  await browser.close();
  console.log(`\n${pass} passed, ${fail} failed`);
  if (fails.length) console.log("失败项: " + fails.join(" | "));
  process.exit(fail === 0 ? 0 : 1);
})().catch((e) => { console.error("异常:", e); process.exit(2); });
