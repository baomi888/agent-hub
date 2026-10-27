# 修复说明 2026-10-20：登录 Cookie 缺少 Secure 标志

对应 issue：[#14](https://github.com/baomi888/agent-hub/issues/14)
影响面：登录 / 注册会话保持（全站身份入口）
结论：**代码缺陷已修复，8 条回归测试通过，全量 pytest 237 passed。**

---

## 1. 问题：注释写了 Secure，代码里没有

`core/auth.py::set_session_cookie` 的 docstring 原文：

> SameSite=Lax：本站跳转带着，跨站表单不带，够用且不会把 CSRF 面再开大。
> **Secure 只在 https 下加**，否则本地 http 调试会直接丢 Cookie。

但修复前的实现是：

```python
def set_session_cookie(resp: Response, token: str) -> None:   # ← 不接收 request
    kwargs = {
        "key": COOKIE_NAME,
        "value": token,
        "max_age": config.AUTH_TTL_DAYS * 86400,
        "httponly": True,
        "samesite": "lax",
        "path": "/",
    }                                                          # ← 没有 secure 字段
    resp.set_cookie(**kwargs)
```

两个缺口叠在一起，导致结论是唯一的：

1. 函数**不接收 `request`**，没有任何途径知道当前请求走的是 http 还是 https；
2. `kwargs` 里**根本没有 `secure` 键**。

所以 **Secure 永远不生效**——注释描述的行为从来没被执行过。这不是"上 HTTPS 才暴露的问题"，而是"注释和实现各说各话、且没有任何测试兜住"的典型静默缺陷。

### 为什么一直没被发现

- 本地全程 http 调试，`Secure` 本来就不该出现，行为"看起来完全正常"；
- 没有测试断言过 `Set-Cookie` 的内容，只有"能登录就行"的端到端路径；
- 该缺陷在 2026-10-20 的历史对话中已被标记为「必做、与工具无关」，但当时没有落地，随后被后续工作覆盖。

---

## 2. 影响

| 维度 | 后果 |
|---|---|
| 安全 | HttpOnly 有了，但缺 Secure，浏览器**不保证**只在加密通道发送会话 Cookie。若站点某处存在 http 可达路径（含被降级、混合内容、子域 http 页面），Cookie 会被明文带出。 |
| 功能 | 部分浏览器（严格策略 / 低版本）在 https 站点上对非 Secure 的 Cookie 处理更保守，可能拒存或清理，表现为**登录态丢失、反复登录**。 |
| 时序 | 当前站点还是 `http://8.163.62.25:3000`，问题不显现；**一旦切到 HTTPS 就会暴露**——正好卡在这次 HTTPS 改造的路径上，属于必须先修的 P0。 |

---

## 3. 修复

### 3.1 新增协议判定：优先读 `X-Forwarded-Proto`

关键点在于**不能只看 `request.url.scheme`**。生产链路上 TLS 由 nginx 终结，转发给 uvicorn 的是明文 http；而 `deploy.sh:307` 启动参数是：

```bash
nohup venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port "$BACK_PORT"
```

**没有 `--proxy-headers`**（也没配 `forwarded_allow_ips`），因此 uvicorn 不信任 `X-Forwarded-Proto`，`request.url.scheme` 在 HTTPS 站点上依然是 `http`。若只判 scheme，Secure 在真正的生产环境里**仍然加不上**——修了等于没修。

所以判定顺序是：**先读 `X-Forwarded-Proto`，没有再退回 scheme，两条都取不到按 http 处理**（保守：宁可不加 Secure，也不让本地 http 调试丢 Cookie）。

```python
def _is_secure_request(request: Request | None) -> bool:
    if request is None:
        return False
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if proto:
        return proto == "https"
    try:
        return request.url.scheme == "https"
    except Exception:
        return False
```

`.split(",")[0]` 处理多级代理拼接头（`https, http` 取第一段，即最靠近客户端的那一跳）。

### 3.2 `set_session_cookie` 补上 secure

```python
def set_session_cookie(resp: Response, token: str, request: Request | None = None) -> None:
    kwargs = {
        "key": COOKIE_NAME,
        "value": token,
        "max_age": config.AUTH_TTL_DAYS * 86400,
        "httponly": True,
        "samesite": "lax",
        "path": "/",
        "secure": _is_secure_request(request),   # ← 新增
    }
    resp.set_cookie(**kwargs)
```

`request` 默认 `None`：**保持向后兼容**，不传时按 http 处理，老调用点不会因为签名变化而报错。

### 3.3 调用点接上

`api/auth.py` 的 `register` / `login` 两个 handler 补 `request: Request` 并传入：

```python
def register(body: Credentials, request: Request, response: Response):
    ...
    set_session_cookie(response, create_token(user["id"]), request)

def login(body: Credentials, request: Request, response: Response):
    ...
    set_session_cookie(response, create_token(user["id"]), request)
```

> 注意别漏第二个入口：只改 `login` 不改 `register`，注册完自动登录就会掉回非 Secure——这正是本项目历史上反复踩的「同一个规则在这处遵守、在那处漏了」的模式。

---

## 4. 验证

新增 `tests/test_auth_cookie_secure.py`（8 条，纯单元，不依赖 DB / 网络），锁死四条主路径 + 三个边界：

| 测试 | 断言 |
|---|---|
| `test_http_has_no_secure` | http 请求的 `Set-Cookie` 不含 Secure |
| `test_direct_https_sets_secure` | `url.scheme=https` 时含 Secure |
| `test_reverse_proxy_https_sets_secure` | `X-Forwarded-Proto: https` 时含 Secure（**生产实际路径**） |
| `test_forwarded_proto_first_value_wins` | `https, http` 取第一段 → Secure |
| `test_forwarded_http_does_not_set_secure` | 头明确 `http` 时，不被 scheme=https 误导 |
| `test_no_request_defaults_insecure` | 不传 request 保守按 http（兼容旧调用） |
| `test_httponly_always_present` | HttpOnly 始终存在（http / https 都是） |
| `test_is_secure_request_helper` | 判定函数四种组合 |

实测输出（2026-10-20 16:4x 复跑）：

```
tests/test_auth_cookie_secure.py  ->  8 passed in 0.24s
全量 pytest                       ->  237 passed, 19 subtests passed in 24.82s
```

基线 229 → 237，**新增 8 条、零回归**。

---

## 5. 关联改动

| 文件 | 改动 |
|---|---|
| `core/auth.py` | 新增 `_is_secure_request`；`set_session_cookie` 增加 `request` 参数与 `secure` 字段；docstring 补注"此前漏写" |
| `api/auth.py` | `register` / `login` 补 `request: Request` 并传入 |
| `tests/test_auth_cookie_secure.py` | 新增 8 条回归测试 |

---

## 6. 后续（与 HTTPS 上线绑定）

1. **切 HTTPS 后必须实测一次登录**：DevTools → Network → `POST /api/auth/login/` → 响应头 `Set-Cookie` 应含 `Secure; HttpOnly; SameSite=Lax`。
2. 若将来给 uvicorn 加 `--proxy-headers`，本函数行为不变（两条判定路径仍成立），无需回改。
3. 同类隐患的通用做法：**凡是注释里描述了某种行为，就必须有一条测试断言它**。本次缺陷的本质是"文档承诺 + 无测试"，不是"写错了算法"。
