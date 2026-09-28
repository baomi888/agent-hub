# -*- coding: utf-8 -*-
"""CDP 实机验证：聊天附件（选文件 -> 附件条 -> 发送 -> 消息流图片）"""
import base64, hashlib, json, os, socket, struct, time

WS_URL = "ws://localhost:9333/devtools/page/1DC35E5F281D69CD8A931F2E93853F4B"
TEST_IMG = r"C:\Users\HUAWEI\Desktop\苞米agent\ui-redesign\_shots\_upload-ref.png"
OUT1 = r"C:\Users\HUAWEI\Desktop\苞米agent\ui-redesign\_shots\聊天附件-验证1-附件条.png"
OUT2 = r"C:\Users\HUAWEI\Desktop\苞米agent\ui-redesign\_shots\聊天附件-验证2-已发送.png"

# ---- 最小 WebSocket 客户端（RFC6455，text/binary） ----
class WS:
    def __init__(self, url):
        host, path = url.split("://")[1].split("/", 1)
        h, _, port = host.partition(":")
        port = int(port or 80)
        self.s = socket.create_connection((h, port), timeout=15)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET /{path} HTTP/1.1\r\nHost: {host}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.s.sendall(req.encode())
        resp = self._read_until(b"\r\n\r\n")
        if b" 101 " not in resp:
            raise RuntimeError("ws handshake failed: " + resp[:120].decode(errors="replace"))
        self.buf = b""

    def _read_until(self, marker):
        data = b""
        while marker not in data:
            chunk = self.s.recv(4096)
            if not chunk:
                break
            data += chunk
        return data

    def _recv_exact(self, n):
        while len(self.buf) < n:
            chunk = self.s.recv(65536)
            if not chunk:
                raise ConnectionError("socket closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send_frame(self, opcode, payload):
        mask = os.urandom(4)
        n = len(payload)
        header = bytes([0x80 | opcode])
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.s.sendall(header + mask + masked)

    def recv_frame(self):
        h = self._recv_exact(2)
        fin, opcode = h[0] >> 7, h[0] & 0x0F
        masked, ln = h[1] >> 7, h[1] & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", self._recv_exact(2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", self._recv_exact(8))[0]
        mask = self._recv_exact(4) if masked else None
        payload = self._recv_exact(ln)
        if mask:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return opcode, payload

    def send_text(self, text):
        self.send_frame(1, text.encode())

    def recv_text(self):
        while True:
            op, payload = self.recv_frame()
            if op == 1:
                return payload.decode()
            if op == 8:
                raise ConnectionError("ws closed")
            if op == 9:  # ping -> pong
                self.send_frame(10, payload)

# ---- CDP ----
ws = WS(WS_URL)
_id = 0
pending = {}

def cmd(method, params=None, timeout=20):
    global _id
    _id += 1
    mid = _id
    ws.send_text(json.dumps({"id": mid, "method": method, "params": params or {}}))
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = json.loads(ws.recv_text())
        if msg.get("id") == mid:
            if "error" in msg:
                raise RuntimeError(f"{method}: {msg['error']}")
            return msg.get("result", {})
        pending.setdefault(mid, msg)  # 事件忽略
    raise TimeoutError(method)

cmd("Runtime.enable"); cmd("DOM.enable"); cmd("Page.enable")
doc = cmd("DOM.getDocument", {"depth": 2})
root_id = doc["root"]["nodeId"]
res = cmd("DOM.querySelector", {"nodeId": root_id, "selector": "input[type=file]"})
node_id = res.get("nodeId")
assert node_id and node_id != 0, "input[type=file] not found"

# 1) 设置文件 -> 触发 change -> 附件条
cmd("DOM.setFileInputFiles", {"nodeId": node_id, "files": [TEST_IMG]})
time.sleep(0.8)

def shot(path):
    r = cmd("Page.captureScreenshot", {"format": "png"})
    with open(path, "wb") as f:
        f.write(base64.b64decode(r["data"]))
    print("saved", path)

shot(OUT1)

# 2) 点击发送
cmd("Runtime.evaluate", {"expression": "document.querySelector('.send').click()"})
time.sleep(1.2)
shot(OUT2)

# 3) 回读关键状态（附件条是否清空、消息流中是否出现 msg-img）
st = cmd("Runtime.evaluate", {
    "expression": "JSON.stringify({"
        "strip: !!document.querySelector('.attach-strip'),"
        "imgs: document.querySelectorAll('.msg-img').length,"
        "files: document.querySelectorAll('.msg-file').length,"
        "msgs: document.querySelectorAll('.msg').length,"
        "userText: (document.querySelectorAll('.msg-user .msg-text')||[]).length,"
        "placeholder: (document.querySelector('textarea')||{}).placeholder||''"
    "})",
    "returnByValue": True,
})
print("STATE", st["result"]["value"])
print("DONE")
