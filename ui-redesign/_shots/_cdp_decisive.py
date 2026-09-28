# -*- coding: utf-8 -*-
"""决定性验证：初始态(资料库开) -> 会话 -> 附件 -> img 位置"""
import base64, json, os, socket, struct, time

WS_URL = "ws://localhost:9333/devtools/page/1DC35E5F281D69CD8A931F2E93853F4B"
TEST_IMG = r"C:\Users\HUAWEI\Desktop\苞米agent\ui-redesign\_shots\_upload-ref.png"
OUT = r"C:\Users\HUAWEI\Desktop\苞米agent\ui-redesign\_shots\聊天附件-验证7-资料库开启态.png"

class WS:
    def __init__(self, url):
        host, path = url.split("://")[1].split("/", 1)
        h, _, port = host.partition(":"); port = int(port or 80)
        self.s = socket.create_connection((h, port), timeout=15)
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

ws = WS(WS_URL); _id = 0
def cmd(method, params=None, timeout=30):
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

def shot(path):
    r = cmd("Page.captureScreenshot", {"format": "png"})
    with open(path, "wb") as f: f.write(base64.b64decode(r["data"]))
    print("saved", path)

cmd("Runtime.enable"); cmd("DOM.enable"); cmd("Page.enable")
# 刷新到初始态
cmd("Page.reload", {"ignoreCache": True})
time.sleep(4)
print("kb panel open:", ev("!!document.querySelector('[class*=\"kb\"]') || [...document.querySelectorAll('div')].some(d => (d.textContent||'').includes('资料库') && d.childElementCount < 5)"))
print("sessions:", ev("document.querySelectorAll('.session').length"))

# 点第一个会话
ev("(document.querySelectorAll('.session')[0]||{}).click ? document.querySelectorAll('.session')[0].click() : null")
time.sleep(1.2)
print("textarea:", ev("!!document.querySelector('textarea')"))

# 设附件 + 文字 + 发送
doc = cmd("DOM.getDocument", {"depth": 2})
res = cmd("DOM.querySelector", {"nodeId": doc["root"]["nodeId"], "selector": "input[type=file]"})
cmd("DOM.setFileInputFiles", {"nodeId": res["nodeId"], "files": [TEST_IMG]})
time.sleep(0.6)
ev("document.querySelector('textarea').value = '帮我看看这张图'; document.querySelector('textarea').dispatchEvent(new Event('input', {bubbles:true}))")
time.sleep(0.3)
ev("document.querySelector('.send').click()")
time.sleep(1.5)

diag = ev("""
(() => {
  const img = document.querySelector('.msg-img');
  if (!img) return 'no img';
  const r = img.getBoundingClientRect();
  const kbEl = [...document.querySelectorAll('div')].find(d => (d.textContent||'').trim().startsWith('资料库') && d.childElementCount < 3);
  const kb = kbEl ? kbEl.getBoundingClientRect() : null;
  const list = document.querySelector('.space-y-6');
  const lr = list ? list.getBoundingClientRect() : null;
  return JSON.stringify({
    img: {x:Math.round(r.x), y:Math.round(r.y), w:Math.round(r.width)},
    msgRight: lr ? Math.round(lr.right) : null,
    kbLeft: kb ? Math.round(kb.left) : null,
    overflow: lr && kb ? (r.x + r.width > kb.left) : 'n/a'
  });
})()
""")
print("diag:", diag)
shot(OUT)
print("DONE")
