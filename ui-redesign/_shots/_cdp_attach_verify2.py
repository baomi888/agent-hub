# -*- coding: utf-8 -*-
"""CDP 实机验证·第二轮：先进入会话，再验证附件发送链路"""
import base64, hashlib, json, os, socket, struct, time

WS_URL = "ws://localhost:9333/devtools/page/1DC35E5F281D69CD8A931F2E93853F4B"
TEST_IMG = r"C:\Users\HUAWEI\Desktop\苞米agent\ui-redesign\_shots\_upload-ref.png"
OUT1 = r"C:\Users\HUAWEI\Desktop\苞米agent\ui-redesign\_shots\聊天附件-验证3-会话内附件条.png"
OUT2 = r"C:\Users\HUAWEI\Desktop\苞米agent\ui-redesign\_shots\聊天附件-验证4-会话内已发送.png"

class WS:
    def __init__(self, url):
        host, path = url.split("://")[1].split("/", 1)
        h, _, port = host.partition(":")
        port = int(port or 80)
        self.s = socket.create_connection((h, port), timeout=15)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET /{path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.s.sendall(req.encode())
        resp = self._read_until(b"\r\n\r\n")
        if b" 101 " not in resp:
            raise RuntimeError("ws handshake failed")
        self.buf = b""

    def _read_until(self, marker):
        data = b""
        while marker not in data:
            c = self.s.recv(4096)
            if not c: break
            data += c
        return data

    def _recv_exact(self, n):
        while len(self.buf) < n:
            c = self.s.recv(65536)
            if not c: raise ConnectionError("closed")
            self.buf += c
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send_frame(self, opcode, payload):
        mask = os.urandom(4); n = len(payload)
        h = bytes([0x80 | opcode])
        if n < 126: h += bytes([0x80 | n])
        elif n < 65536: h += bytes([0x80 | 126]) + struct.pack(">H", n)
        else: h += bytes([0x80 | 127]) + struct.pack(">Q", n)
        self.s.sendall(h + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def recv_frame(self):
        h = self._recv_exact(2)
        opcode, masked, ln = h[0] & 0x0F, h[1] >> 7, h[1] & 0x7F
        if ln == 126: ln = struct.unpack(">H", self._recv_exact(2))[0]
        elif ln == 127: ln = struct.unpack(">Q", self._recv_exact(8))[0]
        mask = self._recv_exact(4) if masked else None
        p = self._recv_exact(ln)
        return opcode, bytes(b ^ mask[i % 4] for i, b in enumerate(p)) if mask else p

    def send_text(self, t): self.send_frame(1, t.encode())
    def recv_text(self):
        while True:
            op, p = self.recv_frame()
            if op == 1: return p.decode()
            if op == 8: raise ConnectionError("ws closed")
            if op == 9: self.send_frame(10, p)

ws = WS(WS_URL); _id = 0
def cmd(method, params=None, timeout=20):
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
    r = cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    return r.get("result", {}).get("value")

def shot(path):
    r = cmd("Page.captureScreenshot", {"format": "png"})
    with open(path, "wb") as f: f.write(base64.b64decode(r["data"]))
    print("saved", path)

cmd("Runtime.enable"); cmd("DOM.enable"); cmd("Page.enable")
doc = cmd("DOM.getDocument", {"depth": 2})
root_id = doc["root"]["nodeId"]

# 0) 点第一个会话进入（使 activeSid 非空，避免新建会话依赖后端）
print("sessions:", ev("document.querySelectorAll('.session').length"))
ev("(document.querySelectorAll('.session')[0]||{}).click ? document.querySelectorAll('.session')[0].click() : null")
time.sleep(0.8)
print("activeSid set, chatArea visible:", ev("!!document.querySelector('textarea')"))

# 1) 设置附件文件
res = cmd("DOM.querySelector", {"nodeId": root_id, "selector": "input[type=file]"})
node_id = res.get("nodeId")
cmd("DOM.setFileInputFiles", {"nodeId": node_id, "files": [TEST_IMG]})
time.sleep(0.8)
print("strip after set:", ev("!!document.querySelector('.attach-strip')"))
print("send disabled:", ev("document.querySelector('.send').disabled"))
shot(OUT1)

# 2) 发送
ev("document.querySelector('.send').click()")
time.sleep(1.5)
print("strip after send:", ev("!!document.querySelector('.attach-strip')"))
print("msg-img count:", ev("document.querySelectorAll('.msg-img').length"))
print("user bubble count:", ev("document.querySelectorAll('.msg-user').length"))
shot(OUT2)
print("DONE")
