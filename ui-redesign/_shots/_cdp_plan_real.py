# -*- coding: utf-8 -*-
"""实机验证：真实后端 8000 + 真实前端 3100，发计划请求 -> PlanCard 渲染"""
import base64, json, os, socket, struct, time

WS_URL = None  # 启动时获取
OUT = r"C:\Users\HUAWEI\Desktop\苞米agent\ui-redesign\_shots\计划卡片-实机触发-真实后端.png"

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

import urllib.request
def get_ws_url():
    with urllib.request.urlopen("http://localhost:9333/json/list", timeout=5) as r:
        data = json.loads(r.read().decode())
    for page in data:
        if page.get("type") == "page" and "3100" in page.get("url", ""):
            return page["webSocketDebuggerUrl"]
    # 兜底：取第一个 page
    for page in data:
        if page.get("type") == "page":
            return page["webSocketDebuggerUrl"]
    raise RuntimeError("no page")

WS_URL = get_ws_url()
print("ws:", WS_URL)
ws = WS(WS_URL); _id = 0; _resp = {}

def send(method, params=None):
    global _id
    _id += 1
    ws.send_text(json.dumps({"id": _id, "method": method, "params": params or {}}))
    return _id

def wait_id(mid, timeout=20):
    if mid in _resp: return _resp.pop(mid)
    end = time.time() + timeout
    ws.s.settimeout(0.25)
    while time.time() < end:
        try:
            m = json.loads(ws.recv_text())
        except (socket.timeout, ConnectionError, ValueError):
            continue
        if m.get("id") == mid:
            ws.s.settimeout(None)
            if "error" in m: raise RuntimeError(f"cmd error: {m['error']}")
            return m
        elif m.get("id") is not None:
            _resp[m["id"]] = m
    ws.s.settimeout(None)
    raise TimeoutError(f"no response for {mid}")

def ev(expr):
    mid = send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    r = wait_id(mid)
    return r.get("result", {}).get("result", {}).get("value")

def shot(path):
    mid = send("Page.captureScreenshot", {"format": "png"})
    r = wait_id(mid)
    with open(path, "wb") as f: f.write(base64.b64decode(r["result"]["data"]))
    print("saved", path)

send("Runtime.enable"); send("Page.enable")
# 回到欢迎页
try:
    send("Page.reload", {"ignoreCache": True})
    time.sleep(5)
except Exception:
    pass
print("welcome:", ev("!!document.querySelector('.ability')"))

# 发计划请求（React 受控输入：用原生 value setter 绕过 value tracker）
q = "给我个考研学习计划"
ev(f"""(() => {{
  const el = document.querySelector('textarea');
  const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
  setter.call(el, {json.dumps(q)});
  el.dispatchEvent(new Event('input', {{bubbles: true}}));
  return el.value;
}})()""")
time.sleep(0.4)
ev("document.querySelector('.send').click()")
print("sent:", q)
print("input now:", ev("document.querySelector('textarea').value"))

# 等待流式完成（真实后端可能较慢，最长 90 秒）
end = time.time() + 90
card = False
while time.time() < end:
    time.sleep(3)
    card = bool(ev("!!document.querySelector('.plan-card')"))
    streaming = bool(ev("!!document.querySelector('.cursor')"))
    if card or not streaming:
        break
print("plan-card:", card)
print("streaming:", streaming)
print("plan-title:", ev("(document.querySelector('.plan-title')||{}).textContent || 'none'"))
print("eyebrow:", ev("(document.querySelector('.plan-eyebrow')||{}).textContent || 'none'"))
print("tl-nodes:", ev("document.querySelectorAll('.plan-tl-node').length"))
print("phases:", ev("document.querySelectorAll('.plan-phase').length"))
# 助手原始文本（若未渲染卡片）
print("asst text:", ev("(() => { const els = document.querySelectorAll('.whitespace-pre-wrap'); for (const e of els) { const t = e.textContent || ''; if (t.length > 40 && t.includes('计划')) return t.slice(0, 300); } return 'none'; })()"))
time.sleep(0.5)
shot(OUT)
print("DONE")
