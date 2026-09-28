# -*- coding: utf-8 -*-
"""打开历史会话 → 滚动到消息流底部 → 截图（验证 Kimi 式新排版）"""
import base64, json, os, socket, struct, time, urllib.request

TARGETS_URL = "http://localhost:9334/json"
OUT = r"C:\Users\HUAWEI\Desktop\苞米agent\ui-redesign\_shots\kimi-消息流-新排版.png"

class WS:
    def __init__(self, url):
        host, path = url.split("://")[1].split("/", 1)
        h, _, port = host.partition(":"); port = int(port or 80)
        self.s = socket.create_connection((h, port), timeout=20)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET /{path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.s.sendall(req.encode())
        resp = self._read_until(b"\r\n\r\n")
        if b" 101 " not in resp: raise RuntimeError("handshake failed")
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

def find_page():
    with urllib.request.urlopen(TARGETS_URL, timeout=10) as r:
        for t in json.loads(r.read().decode()):
            if t.get("type") == "page":
                return t["webSocketDebuggerUrl"]
    raise RuntimeError("no page target")

ws = WS(find_page()); _id = 0
def cmd(method, params=None, timeout=25):
    global _id
    _id += 1; mid = _id
    ws.send_text(json.dumps({"id": mid, "method": method, "params": params or {}}))
    end = time.time() + timeout
    while time.time() < end:
        m = json.loads(ws.recv_text())
        if m.get("id") == mid:
            if "error" in m: raise RuntimeError(f"{method}: {m['error']}")
            return m.get("result", {})
    raise TimeoutError(method)

def ev(expr):
    return cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True}).get("result", {}).get("value")

cmd("Runtime.enable"); cmd("Page.enable")
time.sleep(1.5)
print("sessions:", ev("document.querySelectorAll('.session').length"))
print("click:", ev("(() => { const el = document.querySelector('.session'); if (!el) return 'none'; el.click(); return 'clicked ' + el.textContent.trim().slice(0,20); })()"))
time.sleep(3.0)
print("bubbles:", ev("document.querySelectorAll('.bubble-user, .bubble-ai').length"))
print("scroll:", ev("(() => { const sc = document.querySelector('.book-spine .overflow-y-auto'); if (!sc) return 'no scroller'; sc.scrollTop = sc.scrollHeight; return 'scrolled to ' + sc.scrollHeight; })()"))
time.sleep(1.2)

r = cmd("Page.captureScreenshot", {"format": "png"})
with open(OUT, "wb") as f: f.write(base64.b64decode(r["data"]))
print("saved", OUT)
print("DONE")
