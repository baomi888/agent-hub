// 混合架构执行轨迹的**端到端渲染验证**（不是查源码，是真跑浏览器）
//
// 为什么需要：check_hybrid_frontend.js 里大半断言是「查源码里有没有某个字符串」，
// 它能防回归，但证明不了「轨迹真的显示出来了」。后端 hybrid 还没上线，
// 正好用 request interception 注入一段**照文档 §3.4 抄的真实事件序列**，
// 验证 UI 真的会把它渲染成时间轴。
//
// 注入的事件序列（含文档里三个最容易踩的点）：
//   1. workflow_node 的 exit 帧**只带 node_id 不带 name** ⇒ 界面上必须还能显示节点名
//   2. 中途有 mode_switch ⇒ 界面必须出现「工作流 → 智能体」和决策依据
//   3. 某个节点 failed ⇒ 失败原因必须显示出来
const puppeteer = require("puppeteer-core");
const fs = require("fs");
const path = require("path");

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const URL = "http://localhost:3000/";
const OUT = path.join(__dirname, "..", "shots");

// 照文档 §3.4「典型事件序列（hybrid，字段真实）」抄的
const FAKE_SSE = [
  'event: hybrid_start\ndata: {"run_id":"run-e2e-001","mode":"workflow"}\n\n',
  'event: workflow_node\ndata: {"phase":"enter","node_id":"plan","type":"llm","name":"任务规划"}\n\n',
  'event: status\ndata: {"text":"节点 plan 第 1 次失败，重试中：…"}\n\n',
  'event: workflow_node\ndata: {"phase":"exit","node_id":"plan","status":"ok"}\n\n',
  'event: workflow_node\ndata: {"phase":"enter","node_id":"execute","type":"agent","name":"自主执行"}\n\n',
  'event: tool\ndata: {"name":"kb_search","input":"期末复习范围"}\n\n',
  'event: token\ndata: {"text":"根据你的笔记，"}\n\n',
  'event: token\ndata: {"text":"第一步应该先过第三章。"}\n\n',
  // ★ 关键：exit 不带 name，UI 必须靠 Map 补回「自主执行」
  'event: workflow_node\ndata: {"phase":"exit","node_id":"execute","status":"failed","on_fail":null,"error":"工具调用超时"}\n\n',
  // ★ 关键：模式切换 + 决策依据
  'event: mode_switch\ndata: {"rule":"wf_node_failure","reason":"工作流节点失败且无补偿分支，智能体降级接管","from":"workflow","to":"agent"}\n\n',
  'event: workflow_node\ndata: {"phase":"enter","node_id":"recover","type":"agent","name":"降级接管"}\n\n',
  'event: workflow_node\ndata: {"phase":"exit","node_id":"recover","status":"ok"}\n\n',
  'event: done\ndata: {"answer":"根据你的笔记，第一步应该先过第三章。","refs":"","title":"期末复习规划","mode":"agent","message_id":901,"run_id":"run-e2e-001"}\n\n',
].join("");

let pass = 0;
let fail = 0;
const fails = [];
function check(name, cond, extra = "") {
  if (cond) {
    pass++;
    console.log(`PASS  ${name} ${extra}`);
  } else {
    fail++;
    fails.push(name);
    console.log(`FAIL  ${name} ${extra}`);
  }
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    args: ["--no-sandbox", "--no-proxy-server", "--disable-dev-shm-usage"],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1280, height: 900 });
  const errors = [];
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push(String(e)));

  // ---- 拦截 chat/stream，回放假 SSE ----
  await page.setRequestInterception(true);
  page.on("request", (req) => {
    if (req.url().includes("/api/chat/stream")) {
      req.respond({
        status: 200,
        contentType: "text/event-stream; charset=utf-8",
        headers: { "Cache-Control": "no-cache" },
        body: FAKE_SSE,
      });
      return;
    }
    req.continue();
  });

  // ---- 登录（复用既有回归脚本的做法）----
  await page.goto(URL, { waitUntil: "networkidle2", timeout: 60000 });

  const needLogin = await page.evaluate(() =>
    Boolean(document.querySelector('input[type="password"], input[name="username"]'))
  );
  if (needLogin) {
    const u = process.env.BAOMI_USER;
    const p = process.env.BAOMI_PASS;
    if (!u || !p) {
      console.log("SKIP  需要登录但未提供 BAOMI_USER / BAOMI_PASS");
      await browser.close();
      process.exit(2);
    }
    // ⚠️ 精确定位：**按 type=password 分组**再对应到用户名框。
    // 第一版是「把所有 input 顺序填一遍 + 找文本含登录/注册的按钮点」，
    // 结果点到了「登录/注册」那个切换 Tab，凭证填了但表单没提交，
    // 后面 4 条断言全部莫明其妙 FAIL —— 教训：端到端失败先看截图，别急着怀疑被测代码。
    const filled = await page.evaluate(
      (user, pass) => {
        const inputs = [...document.querySelectorAll("input")];
        const pw = inputs.find((i) => i.type === "password");
        const user_ = inputs.find((i) => i.type !== "password");
        if (!pw || !user_) return false;
        const set = (el, v) => {
          const setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype,
            "value"
          ).set;
          setter.call(el, v);
          el.dispatchEvent(new Event("input", { bubbles: true }));
        };
        set(user_, user);
        set(pw, pass);
        return true;
      },
      u,
      p
    );
    if (!filled) {
      console.log("SKIP  登录表单结构不匹配（找不到用户名/密码框）");
      await browser.close();
      process.exit(2);
    }
    // 点真正的提交按钮：在表单内、type=submit 或文本恰为「登录」/「注册」
    const submitted = await page.evaluate(() => {
      const form = document.querySelector("form") || document;
      const btn =
        form.querySelector('button[type="submit"]') ||
        [...form.querySelectorAll("button")].find((b) => {
          const t = (b.textContent || "").trim();
          return (t === "登录" || t === "注册") && b.closest("form") !== null;
        }) ||
        [...form.querySelectorAll("button")].find(
          (b) => (b.textContent || "").trim() === "登录" && !b.closest("[role=tablist]")
        );
      if (!btn) return false;
      btn.click();
      return true;
    });
    check("找到并点击了登录提交按钮", submitted);
    await new Promise((r) => setTimeout(r, 4000));
    const loggedIn = await page.evaluate(() => Boolean(document.querySelector("textarea")));
    check("登录成功（textarea 已出现）", loggedIn);
  }

  // ---- 选「混合」模式 ----
  const modePicked = await page.evaluate(() => {
    const btns = [...document.querySelectorAll("button, [role=button], .mode-item, .chip")];
    const hit = btns.find((b) => (b.textContent || "").trim() === "混合");
    if (hit) {
      hit.click();
      return true;
    }
    return false;
  });
  check("模式选项里有「混合」且可点击", modePicked);

  // ---- 发一条消息（会命中被拦截的 stream）----
  const ta = await page.$("textarea");
  check("找到输入框", Boolean(ta));
  if (ta) {
    await ta.click();
    await ta.type("先复习第三章再整理成清单，最后保存");
    await page.keyboard.down("Enter");
    await page.keyboard.up("Enter");
  }

  // 等轨迹渲染完
  await new Promise((r) => setTimeout(r, 5000));

  // ---- 断言轨迹真的渲染出来了 ----
  const view = await page.evaluate(() => {
    const box = document.querySelector(".hybrid-trace");
    if (!box) return null;
    return {
      head: (box.querySelector(".hybrid-trace-head")?.textContent || "").trim(),
      nodes: [...box.querySelectorAll(".hybrid-node")].map((n) => ({
        name: (n.querySelector(".hybrid-node-name")?.textContent || "").trim(),
        type: (n.querySelector(".hybrid-node-type")?.textContent || "").trim(),
        cls: n.className,
      })),
      switches: [...box.querySelectorAll(".hybrid-switch")].map((s) => (s.textContent || "").trim()),
      fallback: Boolean(box.querySelector(".hybrid-fallback")),
    };
  });

  check("执行轨迹区块已渲染", view !== null);
  if (view) {
    check("轨迹标题含「执行轨迹」", view.head.includes("执行轨迹"), `（实际「${view.head}」）`);
    // 注入的序列一共 3 个节点：plan(ok) / execute(failed) / recover(ok)
    // （第一版这里写成「4 个」是我数错了 —— 失败的是 execute 的**状态**，
    //  不是第 4 个节点。断言要按注入的真实序列数，别凭印象写。）
    check("渲染出 3 个节点（plan/execute/recover）", view.nodes.length === 3, `（实际 ${view.nodes.length}）`);
    // ★ 最关键的一条：exit 帧不带 name，UI 靠 Map 补回
    const names = view.nodes.map((n) => n.name);
    check("★ exit 帧不带 name 但 UI 仍显示节点名「自主执行」", names.includes("自主执行"), `（实际 ${JSON.stringify(names)}）`);
    check("显示「任务规划」节点", names.includes("任务规划"), "");
    check("显示「降级接管」节点", names.includes("降级接管"), "");
    check("失败节点带 is-failed 类", view.nodes.some((n) => n.cls.includes("is-failed")), "");
    // 失败是**状态**不是多一个节点：同一 node_id 的 enter/exit 必须合并成一条
    const idSet = await page.evaluate(() =>
      [...document.querySelectorAll(".hybrid-node")].map((n) => n.className)
    );
    check("同节点 enter/exit 合并成一条（没有重复条目）", idSet.length === 3, `（实际 ${idSet.length} 条）`);
    // 失败原因必须显示（藏起来的失败等于没失败）
    const errShown = await page.evaluate(() =>
      Boolean(document.querySelector(".hybrid-node-error"))
    );
    check("★ 失败原因「工具调用超时」显示出来了", errShown);
    check("模式切换点渲染了 1 条", view.switches.length === 1, `（实际 ${view.switches.length}）`);
    if (view.switches.length) {
      check("切换点显示「工作流 → 智能体」", view.switches[0].includes("工作流") && view.switches[0].includes("智能体"), `（${view.switches[0]}）`);
      check("★ 切换点显示决策依据", view.switches[0].includes("降级接管") || view.switches[0].includes("节点失败"), "");
    }
    check("本轮不误报静默回退（有 run_id）", view.fallback === false);
  }

  // 正文确实收到了 token
  const bodyText = await page.evaluate(() => document.body.innerText);
  check("正文渲染出了回答内容", bodyText.includes("第一步应该先过第三章"));

  // 无横向溢出（沿用四屏那次的教训：逐元素量）
  const overflow = await page.evaluate(() => {
    const w = window.innerWidth;
    return [...document.querySelectorAll(".hybrid-trace *")]
      .filter((el) => el.getBoundingClientRect().right > w + 1)
      .map((el) => el.className || el.tagName)
      .slice(0, 5);
  });
  check("轨迹无横向溢出", overflow.length === 0, overflow.length ? `溢出: ${overflow.join(",")}` : "");

  const realErrors = errors.filter((e) => !/favicon|404 \(Not Found\)|Failed to load resource/i.test(e));
  check("无 JS 运行时错误", realErrors.length === 0, realErrors.slice(0, 2).join(" | "));

  // ---- 防「文字溢出 / 图标压字」类视觉 bug ----
  // ⚠️⚠️⚠️ 这组断言我改了三轮才做成**真能证伪**的，前两版都是假断言。
  // 教训链条值得留着：
  //  ① 第一版跑在 1280px 宽屏 ⇒ 空间有富余，删了 min-width 也不折行 ⇒ 测不出；
  //  ② 第二版改窄屏(390px) + 短文本「自主执行」⇒ 4 个字本来就放得下 ⇒ 还是测不出；
  //  ③ 第三版用长文本 + Range 量行数，容器仍是 1280px ⇒ 338px 的文字放得下 ⇒ 仍测不出。
  // 根因：**容器不够窄，约束就不会被触发**。反证时「删掉修复仍全绿」正是这个原因，
  // 而我一度误判成「断言不敏感」，其实是我没把测试环境压到 bug 会发生的那一档。
  //
  // ④ 第四版才想明白：极窄容器下**正确行为是折行而不是单行**。
  // 我原来断言「仍单行」，方向就反了 —— 因为 `nowrap` + `flex: 0 0 auto`
  // 配合父级 `min-width: 0`，实测是**溢出容器**（liW=150px / 文字 338px）而不是折行。
  // 这也连带暴露 CSS 本身的缺陷：那个组合会溢出，已改成
  // `max-width: 100% + overflow-wrap: anywhere`（正常宽度仍单行，放不下才断行且不溢出）。
  const LONGTEXT = "把这个超长的节点名字故意写得很长很长用来验证换行行为";
  const probe = await page.evaluate((longText) => {
    const host = document.querySelector(".hybrid-steps");
    if (!host) return { skipped: true };
    const li = document.createElement("li");
    li.className = "hybrid-node";
    // ★ 关键：极窄宽度，逼出「文字放不下」这个前提
    li.style.width = "150px";
    li.innerHTML =
      '<span class="hybrid-dot"></span>' +
      '<span class="hybrid-node-name"></span>' +
      '<span class="hybrid-node-type">模型</span>';
    li.querySelector(".hybrid-node-name").textContent = longText;
    host.appendChild(li);
    const nameEl = li.querySelector(".hybrid-node-name");
    const typeEl = li.querySelector(".hybrid-node-type");
    const range = document.createRange();
    range.selectNodeContents(nameEl);
    const lineCount = range.getClientRects().length; // >1 即折行
    const textW = Math.round(nameEl.getBoundingClientRect().width);
    const typeH = Math.round(typeEl.getBoundingClientRect().height);
    li.remove();
    return { lineCount, textW, liW: 150, typeH };
  }, LONGTEXT);

  if (probe.skipped) {
    check("极窄容器探针（.hybrid-steps 未找到，跳过）", false);
  } else {
    check(
      "★ 极窄容器(150px)下长节点名会折行（行数>1）",
      probe.lineCount > 1,
      `(行数=${probe.lineCount}，文字宽 ${probe.textW}px / 容器 ${probe.liW}px)`
    );
    check(
      "★ 极窄容器下文字不溢出容器（换行而非溢出）",
      probe.textW <= probe.liW + 1,
      `(文字 ${probe.textW}px vs 容器 ${probe.liW}px)`
    );
    check("★ 极窄容器下类型标签自身不折行", probe.typeH < 30, `(高 ${probe.typeH}px)`);
  }

  // 窄屏复测：压到手机宽度看真实节点
  await page.setViewport({ width: 390, height: 844 });
  await new Promise((r) => setTimeout(r, 600));
  const layoutNarrow = await page.evaluate(() => {
    const one = (el) => {
      const r = el.getBoundingClientRect();
      return { h: r.height, text: (el.textContent || "").trim() };
    };
    return {
      names: [...document.querySelectorAll(".hybrid-node-name")].map(one),
      types: [...document.querySelectorAll(".hybrid-node-type")].map(one),
    };
  });
  const singleLine = (h) => h < 30; // 折行后至少两行 ≈ 44px
  const wrappedNarrowNames = layoutNarrow.names.filter((n) => !singleLine(n.h));
  check("★ 窄屏(390px)：真实节点名不折行", wrappedNarrowNames.length === 0,
    wrappedNarrowNames.length ? `折行: ${wrappedNarrowNames.map((n) => n.text).join(",")}` : "");
  const wrappedNarrowTypes = layoutNarrow.types.filter((n) => !singleLine(n.h));
  check("★ 窄屏(390px)：类型标签不折行", wrappedNarrowTypes.length === 0,
    wrappedNarrowTypes.length ? `折行: ${wrappedNarrowTypes.map((n) => n.text).join(",")}` : "");

  // 窄屏也不能横向溢出
  const overflowNarrow = await page.evaluate(() => {
    const w = window.innerWidth;
    return [...document.querySelectorAll(".hybrid-trace *")]
      .filter((el) => el.getBoundingClientRect().right > w + 1)
      .map((el) => el.className || el.tagName)
      .slice(0, 5);
  });
  check("★ 窄屏(390px)：轨迹无横向溢出", overflowNarrow.length === 0,
    overflowNarrow.length ? `溢出: ${overflowNarrow.join(",")}` : "");

  const dotSvg = await page.evaluate(() => {
    const d = document.querySelector(".hybrid-dot");
    const s = d && d.querySelector("svg");
    if (!d || !s) return null;
    const dr = d.getBoundingClientRect();
    const sr = s.getBoundingClientRect();
    return { dotW: dr.width, svgW: sr.width, overflow: sr.width > dr.width + 0.5 };
  });
  check("★ 状态图标不溢出圆点（不压到文字）", dotSvg && !dotSvg.overflow,
    dotSvg ? `(dot ${dotSvg.dotW}px / svg ${dotSvg.svgW}px)` : "(没找到 .hybrid-dot)");

  // 恢复宽屏再截图
  await page.setViewport({ width: 1280, height: 900 });
  await new Promise((r) => setTimeout(r, 400));

  const shot = path.join(OUT, "hybrid-trace-e2e.png");
  await page.screenshot({ path: shot, fullPage: false });
  console.log(`\n截图: ${shot}`);

  await browser.close();
  console.log(`\n${pass} passed, ${fail} failed`);
  if (fails.length) console.log("失败项: " + fails.join(" | "));
  process.exit(fail === 0 ? 0 : 1);
})().catch((e) => {
  console.error("E2E 异常:", e);
  process.exit(2);
});
