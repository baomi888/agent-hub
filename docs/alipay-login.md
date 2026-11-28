# 支付宝网站登录：离线闭环

更新时间：2026-11-27。本轮完成开发与隔离测试，不部署、不访问真实支付宝网关，不证明真实应用权限已经开通。

## 先看这条流程

本站按钮 → 后端创建支付宝 state → 生成授权链接 → 接收 `auth_code` → SDK 签名请求并验签响应 → 获得 `ExternalIdentity` → 共享账号业务找到／创建内部用户 → `_issue_session()` 写入本站 HttpOnly Cookie。

先读 [登录架构图与代码审查手记](login-architecture-review.md)。共用的 `callback()` 在 `api/external_login.py`，支付宝的 `authenticate()` 在 `core/alipay.py`。

## 和微信的差异

| 项目 | 微信网站登录 | 本次支付宝实现 |
| --- | --- | --- |
| 回调授权码参数 | `code` | `auth_code` |
| 后端请求认证 | AppID、AppSecret | AppID、应用私钥的 RSA2 签名 |
| 平台结果验证 | 微信服务端接口返回与身份一致性检查 | SDK 使用支付宝公钥验签，再检查令牌接口与资料接口身份一致 |
| 身份适配结果 | `ExternalIdentity("wechat", ...)` | `ExternalIdentity("alipay", ...)` |
| 内部账号、登录态 | 共用本站账号与 `_issue_session()` | 同左 |

签名的应用私钥和验签的支付宝公钥不是一对本地密钥，不要求两者互相匹配。SDK 需要 PKCS1 私钥，本模块会将 PEM 或裸 Base64 的 PKCS8／PKCS1 应用私钥规范化后交给 SDK。

## 账号规则

- 首次使用自动注册，不要求邮箱，本站生成 `ali_随机串` 登录名，不设置默认密码。
- 使用有效的支付宝昵称；缺少昵称时显示“支付宝用户”。头像继续使用本站默认头像／上传功能。
- 重复登录复用已有内部用户，不按昵称或邮箱合并账号。
- 绑定必须先登录本站，不能抢占他人绑定；更换时先解绑原支付宝再绑定新的。
- 解绑前必须设置本站密码。注销删除整个账号时不需要先设置密码；当前完整注销仍是停机维护入口。
- 停用账号不能通过支付宝登录；绑定／解绑回调中切换账号或鉴权版本变化也会被拒绝。
- 支付宝和微信 state、浏览器流程 Cookie、平台标识均隔离；共享注册总量限制。

## 配置和密钥

先安装项目依赖；本轮使用的官方 SDK 为 `alipay-sdk-python==3.7.1467`。

```dotenv
ALIPAY_LOGIN_ENABLED=0
ALIPAY_APP_ID=
ALIPAY_APP_PRIVATE_KEY_FILE=
ALIPAY_PUBLIC_KEY_FILE=
ALIPAY_REDIRECT_URI=https://myzhisui.cn/api/auth/alipay/callback
ALIPAY_SUBJECT_FIELD=open_id
```

密钥配置填本地文件路径，不粘贴密钥内容，不发给浏览器，不写进测试。项目已忽略 `docs/*私钥*` 文件；更推荐生产密钥放在项目目录之外。

`ALIPAY_SUBJECT_FIELD` 只能固定选择 `open_id` 或 `user_id`，取决于正式应用实际开放、返回的字段。没有所选字段就拒绝登录，不自动回退；关联中保存 `open_id:值` 或 `user_id:值`，避免标识种类碰撞。已有用户后不要直接切换，需要先迁移关联。

默认关闭开关。应用 ID、HTTPS 回调、密钥或身份字段配置无效时，不开放登录按钮。真实 AppID 与密钥、支付宝权限、回调域名是否匹配，留待后续正式联调确认；本轮测试不依赖真实配置。

## 接口

| 接口 | 用途 |
| --- | --- |
| `POST /api/auth/alipay/start` | `action=login/bind/unlink`，服务端固定平台来源，返回授权地址 |
| `GET /api/auth/alipay/callback` | 不带查询参数时返回 `200 ok`，供平台检查地址可达性；带参数时消费 state、验证身份并执行账号业务 |
| `GET /api/auth/alipay/status` | 已登录用户查询是否开放、是否绑定 |
| `GET /api/auth/config/` | 新增 `alipay_enabled` |

失败使用固定本站地址和有限错误码返回，不在 URL 中传本站 JWT 或支付宝令牌。SDK 不启用记录请求／响应正文的 logger，SDK 原始异常不向前端透传。浏览器不能直接提交第三方身份来注册。

平台登记地址固定为 `https://myzhisui.cn/api/auth/alipay/callback`。服务端 `ALIPAY_REDIRECT_URI` 应配置为这个完整值，末尾无需斜杠。直接访问的探测响应在登录开关关闭时也可用。部署后检查此地址的首个响应为 HTTP 200、正文 `ok`，再由同事保存平台配置。

## 测试方法

```powershell
python tools/run_isolated_tests.py tests/test_alipay_login.py tests/test_external_accounts.py tests/test_wechat_login.py -q
```

测试临时生成两对 RSA2048 密钥，分别模拟应用和支付宝；只替换 SDK 的 `do_post` 网络传输。SDK 请求签名、响应验签、路由、state、数据库事务和本站 Cookie 都真实执行。无需真实密钥、AppID、扫码或服务器。

首次接通时，上述组合通过 59 项，前端 TypeScript 与相关文件 ESLint 检查通过。后续回调重构增加了用例，复测范围和结果见[登录架构审查](login-architecture-review.md)。

覆盖自动注册与重复登录、无邮箱／默认密码、缺少昵称、固定身份字段、签名篡改、缺失签名、资料身份不一致、错误授权码、超时、state 重放／过期／跨平台／浏览器变化、应用不匹配、绑定冲突与更换、解绑密码保护、旧会话失效、停用检查、注册开关、限流、完整注销后重新开户。

## 核对依据

- [支付宝官方 Python SDK](https://github.com/alipay/alipay-sdk-python-all)
- [官方 SDK 请求签名和响应验签实现](https://github.com/alipay/alipay-sdk-python-all/blob/master/alipay/aop/api/DefaultAlipayClient.py)
- [官方 SDK 身份响应字段模型](https://github.com/alipay/alipay-sdk-python-all/blob/master/alipay/aop/api/response/AlipaySystemOauthTokenResponse.py)
- [平台用户信息授权文档，含 PC 场景](https://developer.alibaba.com/docs/doc.htm?articleId=105656&docType=1&treeId=193)
- [授权码换取令牌接口](https://iopenhome.alipay.com/docs/ac/solution_api/oauth_token)

平台文档中部分页面属于存量文档。本轮据此实现并验证协议处理；正式应用的可用产品、字段和权限需要在真实联调时再次核对。
