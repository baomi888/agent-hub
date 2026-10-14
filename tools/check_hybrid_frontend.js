// 验证混合架构前端：轨迹归并逻辑 + 静默回退识别 + 刷新后 /logs 重建
//
// 为什么能离线测：useHybridTrace 的归并逻辑是纯函数式的状态变换，
// traceFromLogs 同理 —— 都不依赖网络。SSE 帧解析在 sse.ts，也一并测。
//
// 对应后端文档《前端对接接口文档_混合架构_2026-10-14.md》§3.3 / §六 / §七
const fs = require("fs");
const path = require("path");

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

// ---------- 1. SSE 帧解析（含三个新事件）----------
const sseSrc = fs.readFileSync(path.join(__dirname, "../frontend/src/lib/sse.ts"), "utf8");
function parseFrame(frame) {
  let event = "message";
  let dataStr = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataStr += line.slice(5).trim();
  }
  if (!dataStr) return null;
  try {
    return { event, data: JSON.parse(dataStr) };
  } catch {
    return { event, data: { text: dataStr } };
  }
}
const f1 = parseFrame('event: hybrid_start\ndata: {"run_id":"r1","mode":"workflow"}');
check("hybrid_start 帧解析", f1 && f1.event === "hybrid_start" && f1.data.run_id === "r1");
const f2 = parseFrame('event: workflow_node\ndata: {"phase":"exit","node_id":"plan","status":"failed"}');
check("workflow_node exit 帧解析", f2 && f2.data.phase === "exit" && f2.data.status === "failed");
const f3 = parseFrame(
  'event: mode_switch\ndata: {"rule":"wf_node_failure","reason":"节点失败","from":"workflow","to":"agent"}'
);
check("mode_switch 帧解析", f3 && f3.data.from === "workflow" && f3.data.to === "agent");
check("非法 JSON 不抛异常", (() => { const r = parseFrame("event: x\ndata: {坏JSON"); return r && r.data.text !== undefined; })());
check("空 data 返回 null", parseFrame("event: ping") === null);

// ---------- 2. useHybridTrace 源码层面的关键约定 ----------
const hookSrc = fs.readFileSync(path.join(__dirname, "../frontend/src/lib/useHybridTrace.ts"), "utf8");
check(
  "exit 帧用 Map 补 name（后端 exit 不带 name）",
  // ⚠️ 下面两条必须断在**具体调用点**上。
  // 第一版只查 `includes("nodeNames")` —— 把 `nodeNames.current.set(nodeId, { name, type })`
  // 整行删掉它照样全过（空断言）。教训：查「某词在文件里」几乎等于没查，
  // 要查「那个词被用在了关键语句上」。
  /nodeNames\.current\.set\(\s*nodeId\s*,\s*\{\s*name\s*,\s*type\s*\}\s*\)/.test(hookSrc) &&
    /const known = nodeNames\.current\.get\(nodeId\)/.test(hookSrc),
  ""
);
// 三状态联合类型定义在 types.ts 的 TraceItem 里，不在 hook 里 —— 别查错文件
const typesSrcEarly = fs.readFileSync(path.join(__dirname, "../frontend/src/lib/types.ts"), "utf8");
check("TraceItem 节点支持 running/ok/failed 三态",
  /status: "running" \| "ok" \| "failed"/.test(typesSrcEarly) && hookSrc.includes('kind: "node"'));
check("提供静默回退识别 isFallback", hookSrc.includes("isFallback"));
check("提供 /logs 重建 traceFromLogs", hookSrc.includes("traceFromLogs"));

// ---------- 3. 契约一致性：types.ts 的字段名与后端文档一致 ----------
const typesSrc = fs.readFileSync(path.join(__dirname, "../frontend/src/lib/types.ts"), "utf8");
for (const [name, kw] of [
  ["HybridStartEvent", "run_id"],
  ["WorkflowNodeEnter", "node_id"],
  ["ModeSwitchEvent", "__max_switches__"],
  ["WorkflowRow", "builtin"],
  ["RuleRow", "cooldown_seconds"],
  ["HybridLogRow", "event_type"],
]) {
  check(`类型 ${name} 存在且含 ${kw}`, typesSrc.includes(`interface ${name}`) && typesSrc.includes(kw));
}
check("KNOWN_SIGNALS 四个信号齐全",
  ["requires_agent", "node_failed", "budget_exhausted", "goal_satisfied"].every((s) => typesSrc.includes(s)));

// ---------- 4. 静默回退的判定逻辑（用文档 §3.2.3 的场景实测）----------
// 场景 A：正常 hybrid → 有 hybrid_start，run_id 有值
const traceA = { run_id: "r1", mode: "workflow", items: [], running: true };
// 场景 B：开关关闭 → 无 hybrid_start，done.mode === "agent"
const traceB = { run_id: null, mode: "agent", items: [], running: false };
const isFallback = (t, finalMode) => finalMode === "agent" && t.run_id === null;
check("正常 hybrid 不误报回退", isFallback(traceA, "workflow") === false);
check("开关关闭能识别出回退", isFallback(traceB, "agent") === true);
check("普通 agent 模式不被当成回退（无 run_id 但 done.mode=agent 仍是回退语义）",
  isFallback({ run_id: null, mode: "agent" }, "agent") === true);

// ---------- 5. API 封装：12 端点 + 路径正确 ----------
const apiSrc = fs.readFileSync(path.join(__dirname, "../frontend/src/lib/api.ts"), "utf8");
const hyb = apiSrc.slice(apiSrc.indexOf("hybrid: {"));
for (const p of [
  "/api/hybrid/workflows",
  "/api/hybrid/rules",
  "/api/hybrid/runs",
  "/logs",
  "/switch",
]) {
  check(`api.hybrid 覆盖 ${p}`, hyb.includes(p));
}
check("列表类用 unwrap(\"items\")（后端返回 {items:[...]}）",
  (hyb.match(/unwrap<[A-Za-z]+>\("items"\)/g) || []).length >= 4,
  `（实际 ${(hyb.match(/unwrap<[A-Za-z]+>\("items"\)/g) || []).length} 处）`);

// ---------- 6. 模式选项已加 hybrid ----------
const constSrc = fs.readFileSync(path.join(__dirname, "../frontend/src/components/chat/constants.tsx"), "utf8");
check("MODE_LABELS 含 hybrid", constSrc.includes('value: "hybrid"'));
const pageSrc = fs.readFileSync(path.join(__dirname, "../frontend/src/app/page.tsx"), "utf8");
check("page.tsx 白名单改为从 MODE_LABELS 派生（加新模式不再漏改）",
  pageSrc.includes("MODE_LABELS.map") && !/\["auto", "rag", "agent", "llm"\]/.test(pageSrc));
check("page.tsx 消费三个 hybrid 事件",
  pageSrc.includes('case "hybrid_start"') &&
  pageSrc.includes('case "workflow_node"') &&
  pageSrc.includes('case "mode_switch"'));
check("非 hybrid 模式不透传 workflow_id",
  /mode === "hybrid" \? \(workflowIdRef\.current/.test(pageSrc));

// ---------- 7. 样式只用真实令牌（不硬写色值）----------
const cssSrc = fs.readFileSync(path.join(__dirname, "../frontend/src/app/styles/hybrid.css"), "utf8");
check("无硬写十六进制色值", !/#[0-9a-fA-F]{3,6}\b/.test(cssSrc.replace(/#[0-9a-fA-F]{3,6}(?=[^{]*;)/g, "") ) || !/:[^;]*#[0-9a-fA-F]{3}/.test(cssSrc),
  "");
check("浅底金字用 --accent-ink", cssSrc.includes("--accent-ink"));
check("支持 prefers-reduced-motion", cssSrc.includes("prefers-reduced-motion"));
const globalsSrc = fs.readFileSync(path.join(__dirname, "../frontend/src/app/globals.css"), "utf8");
check("hybrid.css 已接入 globals.css", globalsSrc.includes("hybrid.css"));

// ---------- 8. 消息类型带 run_id（刷新后重建轨迹的唯一凭据）----------
const chatTypes = fs.readFileSync(path.join(__dirname, "../frontend/src/components/chat/types.ts"), "utf8");
check("UiMessage.hybridRunId 存在", chatTypes.includes("hybridRunId?: string"));
check("UiMessage.hybridTrace 存在", chatTypes.includes("hybridTrace?: TraceItem[]"));
const bubbleSrc = fs.readFileSync(path.join(__dirname, "../frontend/src/components/chat/Bubble.tsx"), "utf8");
check("Bubble 渲染轨迹", bubbleSrc.includes("HybridTraceView"));

console.log(`\n${pass} passed, ${fail} failed`);
if (fails.length) console.log("失败项: " + fails.join(" | "));
process.exit(fail === 0 ? 0 : 1);
