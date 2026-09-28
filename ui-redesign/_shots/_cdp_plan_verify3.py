# -*- coding: utf-8 -*-
"""CDP mock 后端 · v3 事件泵：真实前端全链路验证 PlanCard 渲染"""
import base64, json, os, socket, struct, time

WS_URL = "ws://localhost:9333/devtools/page/1DC35E5F281D69CD8A931F2E93853F4B"
OUT = r"C:\Users\HUAWEI\Desktop\苞米agent\ui-redesign\_shots\计划卡片-实机验证-浅色.png"

PLAN_TEXT = (
    "新品发布会策划方案\n"
    "第1周 筹备期\n"
    "1. 组建团队\n确定核心成员与分工\n"
    "2. 场地与预算\n确认场地档期与费用\n"
    "第2周 制作期\n"
    "3. 物料制作\n主视觉与宣传物料设计\n"
    "4. 媒体对接\n邀约媒体与嘉宾名单\n"
    "第3周 执行期\n"
    "5. 现场执行\n发布会彩排与现场落地\n"
    "6. 收尾复盘\n数据统计与总结优化"
)

class WS:
    def __init__(self, url):
        host, path = url.split("://")[1].split("/", 1)
        h, _, port = host.partition(":"); port = int(port or 80)
        self.s = socket.create_connection((h, port), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET /{path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.s.sendall(req.encode())
        resp = self._read_until(b"\r\n\r\n")
        if b" 101 " not in resp: raise RuntimeError("handshake")
        self.buf = b""
    def _read_until(self, m):
        d = b""
        while m not in d:
            c = self.s.recv(4096)
            if not c: break
            d += c
        return d
    def _recv_exact(self, n):
        while len(self.buf) < n:
            c = self.s.recv(65536)
            if not c: raise ConnectionError("closed")
            self.buf += c
        o, self.buf = self.buf[:n], self.buf[n:]
        return o
    def send_frame(self, op, p):
        mask = os.urandom(4); n = len(p)
        h = bytes([0x80 | op])
        if n < 126: h += bytes([0x80 | n])
        elif n < 65536: h += bytes([0x80 | 126]) + struct.pack(">H", n)
        else: h += bytes([0x80 | 127]) + struct.pack(">Q", n)
        self.s.sendall(h + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(p)))
    def recv_frame(self):
        h = self._recv_exact(2)
        op, mk, ln = h[0] & 0x0F, h[1] >> 7, h[1] & 0x7F
        if ln == 126: ln = struct.unpack(">H", self._recv_exact(2))[0]
        elif ln == 127: ln = struct.unpack(">Q", self._recv_exact(8))[0]
        m = self._recv_exact(4) if mk else None
        p = self._recv_exact(ln)
        return op, bytes(b ^ m[i % 4] for i, b in enumerate(p)) if m else p
    def send_text(self, t): self.send_frame(1, t.encode())
    def recv_text(self):
        while True:
            op, p = self.recv_frame()
            if op == 1: return p.decode()
            if op == 8: raise ConnectionError("closed")
            if op == 9: self.send_frame(10, p)

ws = WS(WS_URL)
_seq = [0]
_resp = {}

def send(method, params=None):
    _seq[0] += 1
    mid = _seq[0]
    ws.send_text(json.dumps({"id": mid, "method": method, "params": params or {}}))
    return mid

def pump(seconds, on_paused=None):
    """事件泵：处理 paused（立即响应，不等待），收集命令响应。返回期间收集到的响应 dict"""
    ws.s.settimeout(0.25)
    got = {}
    end = time.time() + seconds
    while time.time() < end:
        try:
            m = json.loads(ws.recv_text())
        except (socket.timeout, ConnectionError, ValueError):
            continue
        if m.get("method") == "Fetch.requestPaused":
            if on_paused:
                on_paused(m["params"])
            else:
                # 默认：非 /api 放行，/api mock
                pass
        elif m.get("id") is not None:
            got[m["id"]] = m
            _resp[m["id"]] = m
    ws.s.settimeout(None)
    return got

def wait_id(mid, timeout=10):
    if mid in _resp: return _resp.pop(mid)
    end = time.time() + timeout
    ws.s.settimeout(0.25)
    while time.time() < end:
        try:
            m = json.loads(ws.recv_text())
        except (socket.timeout, ConnectionError, ValueError):
            continue
        if m.get("method") == "Fetch.requestPaused":
            handle_paused(m["params"])
        elif m.get("id") == mid:
            ws.s.settimeout(None)
            if "error" in m: raise RuntimeError(f"cmd error: {m['error']}")
            return m
        elif m.get("id") is not None:
            _resp[m["id"]] = m
    ws.s.settimeout(None)
    raise TimeoutError(f"no response for {mid}")

def handle_paused(params):
    rid = params["requestId"]
    url = params["request"].get("url", "")
    method = params["request"].get("method", "")
    path = url.split("localhost:3100")[-1].split("?")[0]
    if "/api/" not in path:
        send("Fetch.continueRequest", {"requestId": rid})
        return
    ctype = "application/json"
    if path == "/api/sessions/" and method == "GET":
        body = json.dumps({"sessions": []}, ensure_ascii=False)
    elif path == "/api/sessions/" and method == "POST":
        body = json.dumps({"id": "mock-session-1", "title": "新对话", "kb_id": None, "created_at": 1, "updated_at": 1}, ensure_ascii=False)
    elif path == "/api/config/defaults":
        body = json.dumps({"chunk_size": 500, "chunk_overlap": 50, "top_k": 3, "supported_extensions": [".txt", ".md", ".pdf"]}, ensure_ascii=False)
    elif path == "/api/kb/":
        body = json.dumps({"kbs": []}, ensure_ascii=False)
    elif path == "/api/chat/stream":
        ctype = "text/event-stream"
        parts = [PLAN_TEXT[:40], PLAN_TEXT[40:120], PLAN_TEXT[120:240], PLAN_TEXT[240:]]
        acc = ""
        frames = []
        for seg in parts:
            acc += seg
            frames.append(f"event: token\ndata: {json.dumps({'text': acc}, ensure_ascii=False)}\n\n")
        frames.append('event: done\ndata: {"refs": ""}\n\n')
        body = "".join(frames)
    else:
        body = json.dumps({"detail": "not mocked"}, ensure_ascii=False)
    print("  [mocked]", method, path)
    send("Fetch.fulfillRequest", {
        "requestId": rid, "responseCode": 200,
        "responseHeaders": [{"name": "Content-Type", "value": ctype}],
        "body": base64.b64encode(body.encode("utf-8")).decode(),
    })

def ev(expr):
    mid = send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    r = wait_id(mid)
    return r.get("result", {}).get("result", {}).get("value")

def shot(path):
    mid = send("Page.captureScreenshot", {"format": "png"})
    r = wait_id(mid)
    with open(path, "wb") as f: f.write(base64.b64decode(r["result"]["data"]))
    print("saved", path)

# ---------- 流程 ----------
send("Runtime.enable"); send("Page.enable"); send("Fetch.enable", {"patterns": [{"urlPattern": "*"}]})
pump(1)
print("welcome before:", ev("!!document.querySelector('.ability')"))
send("Page.reload", {"ignoreCache": True})
pump(6)
print("welcome:", ev("!!document.querySelector('.ability')"))

# 发送计划请求
ev("document.querySelector('textarea').value = '帮我做一份新品发布会策划方案'; document.querySelector('textarea').dispatchEvent(new Event('input', {bubbles:true}))")
pump(0.3)
ev("document.querySelector('.send').click()")
print("sent, pumping stream...")
pump(10)

print("plan-card:", bool(ev("!!document.querySelector('.plan-card')")))
print("plan-title:", ev("(document.querySelector('.plan-title')||{}).textContent || 'none'"))
print("tl-nodes:", ev("document.querySelectorAll('.plan-tl-node').length"))
print("phases:", ev("document.querySelectorAll('.plan-phase').length"))
print("export:", ev("(document.querySelector('.plan-export')||{}).textContent || 'none'"))
shot(OUT)
print("DONE")
