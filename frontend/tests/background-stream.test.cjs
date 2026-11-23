const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const ts = require("typescript");

function load(fetch) {
  const exports = {};
  const source = ts.transpileModule(fs.readFileSync("src/lib/sse.ts", "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInNewContext(source, {
    exports, require: () => ({ detailToMessage: (value, fallback) => value || fallback }),
    fetch, TextDecoder, setTimeout, clearTimeout,
    window: { dispatchEvent() {} }, Event,
  });
  return exports.streamChat;
}
function response(events) {
  return new Response(events.map(([event, data]) =>
    `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join(""), {
    headers: { "Content-Type": "text/event-stream" },
  });
}
test("disconnect resumes at the last cursor without another POST or duplicate events", async () => {
  const calls = [];
  const stream = load(async (url, options) => {
    calls.push([url, options.method]);
    return calls.length === 1
      ? response([["meta", { message_id: 9 }], ["token", { text: "a", cursor: 1 }]])
      : response([["token", { text: "a", cursor: 1 }], ["token", { text: "b", cursor: 2 }], ["done", { cursor: 3 }]]);
  });
  const received = [];
  for await (const event of stream({ sid: "s", question: "q" })) received.push(event);
  assert.deepEqual(calls, [["/api/chat/stream", "POST"], ["/api/chat/stream/s?after=1&message_id=9", "GET"]]);
  assert.equal(received.filter(e => e.event === "token").map(e => e.data.text).join(""), "ab");
  assert.equal(received.find(e => e.data.cursor === 2).data.recovered, true);
});
test("refresh attaches using GET and an absent retained task ends cleanly", async () => {
  const calls = [];
  const stream = load(async (url, options) => {
    calls.push([url, options.method]);
    return new Response(null, { status: 204 });
  });
  for await (const event of stream({ sid: "s", question: "" }, undefined, true)) assert.fail(event.event);
  assert.deepEqual(calls, [["/api/chat/stream/s?after=0", "GET"]]);
});
test("POST network failure probes GET rather than submitting twice", async () => {
  const methods = [];
  const stream = load(async (_, options) => {
    methods.push(options.method);
    if (methods.length === 1) throw new TypeError("connection lost");
    return response([["done", { cursor: 1 }]]);
  });
  for await (const event of stream({ sid: "s", question: "q" })) assert.equal(event.event, "done");
  assert.deepEqual(methods, ["POST", "GET"]);
});


test("refresh pins its initial GET to the persisted message ID", async () => {
  const calls = [];
  const stream = load(async (url) => {
    calls.push(url);
    return new Response(null, { status: 409 });
  });
  const received = [];
  for await (const event of stream({ sid: "s", question: "" }, undefined, true, 42)) received.push(event);
  assert.deepEqual(calls, ["/api/chat/stream/s?after=0&message_id=42"]);
  assert.equal(received.length, 0);
});

test("replay_end restores normal playback for live continuation", async () => {
  const stream = load(async () => response([
    ["meta", { message_id: 42 }],
    ["token", { text: "history", cursor: 1 }],
    ["replay_end", {}],
    ["token", { text: "live", cursor: 2 }],
    ["done", { cursor: 3 }],
  ]));
  const received = [];
  for await (const event of stream({ sid: "s", question: "" }, undefined, true, 42)) received.push(event);
  assert.equal(received.find(e => e.data.cursor === 1).data.recovered, true);
  assert.equal(received.find(e => e.data.cursor === 2).data.recovered, undefined);
  assert.equal(received.find(e => e.data.cursor === 3).data.recovered, undefined);
});

test("a changed run ID cannot replay events onto the previous answer", async () => {
  const stream = load(async () => response([
    ["meta", { message_id: 43 }],
    ["token", { text: "wrong answer", cursor: 1 }],
  ]));
  const received = [];
  for await (const event of stream({ sid: "s", question: "" }, undefined, true, 42)) received.push(event);
  assert.equal(received.length, 0);
});
