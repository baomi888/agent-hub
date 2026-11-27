# 微信扫码自动注册：实现与接入准备

更新时间：2026-11-27。当前范围：PC 网站应用微信登录；共享账号层支持平台隔离，支付宝适配器、回调和前端已完成离线接线与测试，见 [支付宝登录说明](alipay-login.md)。两个平台的真实扫码联调仍待后续进行。

## 先学三个文件

1. `core/wechat.py`：后端用授权码向微信换取凭证、获取身份；输出 `ExternalIdentity`。
2. `core/external_accounts.py`：用 `(provider, app_id, subject)` 查绑定；创建内部用户或处理绑定、解绑。
3. `api/external_login.py` 串起浏览器请求、一次性 state 和本站会话。`api/wechat_login.py` 为它指定微信适配器和回调字段。

先理解这三个职责，再看前端按钮和其余配套代码。

## 当前这一小步：平台来源隔离

只补一件事：授权流程必须明确属于哪个平台。相同的应用 ID 或第三方用户标识，不能跨平台复用。

三处核对：`begin_flow(..., provider="wechat")` 保存来源 → `consume_flow(..., provider="wechat")` 核对回调所属平台 → `complete(identity, flow)` 再核对适配器返回身份的平台和应用。微信路由里的平台由服务端固定，不接受浏览器提交 `provider`；身份标识仍只能来自后端适配器验证结果。

读代码时只看 `core/external_accounts.py` 的这三个函数。它们的参数用途是：`provider` 区分平台，`app_id` 区分该平台应用，`subject` 区分该应用中的第三方账号。共享账号业务继续负责查找、创建内部用户以及绑定、解绑；成功后仍由路由调用原来的 `_issue_session()`。

新用户前缀和缺省显示名按平台选择：微信为 `wx_`／“微信用户”，支付宝为 `ali_`／“支付宝用户”。这只是本站命名规则，不是平台返回的用户名。两者都不要求邮箱、不设置默认密码，都检查内部账号是否停用，并遵守先设置本站密码再解绑的规则。

升级时幂等地给旧流程表增加 `provider`。旧版本只有微信，因此原有流程补成 `wechat`，保留有效 state、账号及绑定。迁移采用写锁并在锁内复查列是否存在，已用并发迁移测试验证。

`tests/test_external_accounts.py` 用测试代码构造两种身份，验证跨平台 state 拒绝、适配器结果不匹配拒绝、同名同标识不合并、多平台绑定、停用账号拒绝以及旧表迁移。这些测试没有增加公开模拟登录端点，也不代表已经接通真实支付宝。

```powershell
python tools/run_isolated_tests.py tests/test_external_accounts.py tests/test_wechat_login.py tests/test_account_deletion.py -q
```

## 已实现的账号规则

| 字段／行为 | 本次处理 |
| --- | --- |
| 内部 `users.id` | 本站生成 UUID，与第三方 ID 分开 |
| `username` | 本站生成 `wx_` 加随机串，保留唯一索引，避开管理员保留名 |
| `nickname` | 使用有效的微信昵称，去控制字符并按现有长度限制截断；缺失用“微信用户” |
| `password_hash` | 使用随机的不可用标记，任何密码均不能通过校验；绝不设置 `123456` |
| 邮箱 | 微信注册暂不要求；之后通过现有安全邮箱模块验证并绑定 |
| 首次设置密码 | 先绑定安全邮箱，再走已有邮箱验证码验证及设置密码流程；旧会话失效 |
| 头像 | 本轮沿用本站默认头像和上传功能，不直接下载或渲染远程微信头像 |
| 关联账号 | 以当前应用的 openid 为 subject；不按昵称／相同邮箱自动合并账号 |
| 已有本站账号 | 先登录本站，在设置中主动扫码绑定；不自动合并，也不能抢占别人的绑定 |
| 更换微信 | 先用原微信确认解绑，再绑定新微信；这是两次明确操作，不是回调自动覆盖 |
| 解绑 | 当前已绑定微信重新授权确认；必须先设置本站密码，只有安全邮箱不够 |
| 删除账号 | 新增 `core/account_deletion.py` 后端维护流程，清理账号、全部平台关联及可追溯的在线业务数据；不要求先设置密码 |

本阶段先跑通停机维护的清理闭环，尚未接公开自助注销按钮；范围、历史文件限制和重试方式见 [注销实现说明](account-deletion.md)。既有 `account.delete_user()` 仍只是账号层删除，不能代替完整清理服务。

普通账号注册继续要求用户名、密码和邮箱验证。微信注册复用 `ALLOW_SIGNUP` 开关、注册限流和每日额度；关掉注册后，已关联用户仍能微信登录。

同一个微信身份重复扫码，不重复创建账号。停用账号或孤儿绑定不能被当成新用户创建。

## state 与事务

- 开始时生成随机 state，数据库仅保存其摘要；另以 HttpOnly Cookie 关联当前浏览器。
- 回调时原子检查平台、应用、用途、浏览器、到期时间、目标内部用户及鉴权版本，然后消费 state。
- 再请求微信：失败必须重新扫码，不能重放已经消费的 state/code。
- 创建内部用户和创建关联在同一个事务中完成，唯一约束防止并发重复开户。
- 绑定、解绑要求回调时仍是发起操作的用户，并在写入前再次检查账号版本和状态。
- 当前选择是自动注册，因此没有“等待用户填写注册资料”的待完成记录。以后如果增加必填资料，再设计该阶段的临时凭证。
- state 消费与网络请求、账号写入不构成跨网络的大事务；不在数据库写锁中等待微信响应。

## 接口与前端

| 接口 | 用途 |
| --- | --- |
| `POST /api/auth/wechat/start` | JSON `action=login/bind/unlink`；返回微信授权 URL，并设置临时 Cookie |
| `GET /api/auth/wechat/callback` | 浏览器携带 code/state；后端校验身份，完成处理并 303 跳回本站 |
| `GET /api/auth/wechat/status` | 登录后查询微信是否已配置、当前账号是否已绑定 |
| `GET /api/auth/config/` | 新增 `wechat_enabled`；不暴露密钥 |
| `GET /api/auth/me/` | 用户信息新增 `has_password`，不暴露密码标记或哈希 |

前端登录卡增加微信入口；未配置时隐藏。设置页增加绑定／解绑入口及未设置密码的说明。
OAuth access_token、AppSecret 不返回浏览器，不入库，httpx 的微信查询串日志脱敏。头像等展示资料不充当账号关联凭证。

## 你已有的公众号测试号与网站应用不同

根据提供的“测试号、AppID、AppSecret、URL、Token”配置形式，判断它很可能是微信公众号接口测试号；最终请以管理页面名称核实。

| 配置 | 作用 |
| --- | --- |
| 公众号的服务器 URL、Token | 接收微信消息／事件和验证服务器；不是本次 OAuth 浏览器回调配置 |
| 网站应用的 AppID、AppSecret | 标识并认证微信开放平台的网站应用 |
| 网站应用授权回调域 | 限定登录后回跳的网站域名 |
| 本项目 `WECHAT_REDIRECT_URI` | 完整 HTTPS 浏览器回调地址，路径必须是 `/api/auth/wechat/callback` |

不要把公众号测试号 AppID 直接用于网站应用的 `qrconnect` / `snsapi_login` 流程。公众号网页授权可用于另一个学习场景，但不能拿它的成功结果当作网站应用已通过审核。
已经公开的 AppSecret 应先重置／更换；不要写进代码、测试、文档或聊天。本文没有保存任何真实密钥。

## 网站应用审核准备顺序

1. 打开微信开放平台 `https://open.weixin.qq.com/`，核实是否已有开发者账号。
2. 在账号中心查看开发者资质认证要求。核对当前允许的主体类型、证明材料、联系人及授权材料；不要在未核对前假定个人账号一定可申请。
3. 在管理中心创建“网站应用”，按当前表单准备名称、简介、图标、可访问官网及网站信息材料。网站备案／许可证、网站登记表和主体一致性等，以控制台要求为准。
4. 提交网站应用审核，核实“微信登录”能力是否已开通。开发者资质认证、网站应用审核、能力开通是需要分别检查的事项，不能只看到 AppID 就认为全部完成。
5. 配置与 Agent-Hub 实际前端域名一致的授权回调域。必须确认该域名的 `/api/` 能代理到本项目；原有公众号服务器接收地址不能简单替代。
6. 用网站应用的配置填写本地 `.env`，检查 HTTPS、域名匹配和回调路由后，才启用并重启服务。

本次未能直接读取微信官方文档正文；不据旧教程承诺费用、审核时限和主体资格。实际材料、费用和处理时限，以登录后的官方控制台及审核反馈为准。

```dotenv
# 这里都是占位值；未审核／未配置完成时保持 0。
WECHAT_LOGIN_ENABLED=0
WECHAT_APP_ID=网站应用的AppID
WECHAT_APP_SECRET=在本地填写重置后的密钥
WECHAT_REDIRECT_URI=https://实际本站域名/api/auth/wechat/callback
```

代码已经接线不等于真实微信联调通过。目前保持默认关闭；测试使用模拟微信 HTTP 响应。

## 平台字段的证据与边界

- 微信：Auth.js 官方仓库的微信 Provider 定义了 `openid`、`nickname`、`headimgurl`、`unionid`。本次用该开源实现交叉核对；昵称、头像不能视为必填或唯一，不把微信号设计成可取得字段。
- 支付宝：官方 `alipay.user.info.share` 说明包含 `user_id`、`nick_name`、`avatar`，须具备相应权限和用户授权。已读取的是存量文档；新应用使用哪种身份字段及权限范围，接入支付宝时还需按控制台／当前接口复核。
- 不论第三方返回多少展示资料，本站的内部 ID、唯一用户名和密码状态都由本站决定。

参考：

- [微信网站应用登录官方入口](https://developers.weixin.qq.com/doc/oplatform/Website_App/WeChat_Login/Wechat_Login.html)
- [微信应用审核规范官方入口](https://developers.weixin.qq.com/doc/oplatform/Website_App/operation.html)
- [Auth.js 微信 Provider 源码](https://github.com/nextauthjs/next-auth/blob/main/packages/core/src/providers/wechat.ts)
- [支付宝官方用户信息共享接口文档](https://iopenhome.alipay.com/docs/ac/solution_api/infoshare)
- [django-allauth 自动注册配置](https://docs.allauth.org/en/latest/socialaccount/configuration.html)
- [Authing 网站应用接入指引](https://docs.authing.cn/v2/guides/wechat-ecosystem/wechat-web-app.html)（对接方的实施资料，不能代替微信实时政策）

## 如何验证

必须使用隔离入口，测试不会访问已有用户数据库或真实微信：

```powershell
python tools/run_isolated_tests.py tests/test_wechat_login.py -q
```

运行此命令的 Python 环境需已安装项目依赖与 pytest。
覆盖：自动注册、无默认密码、重复扫码、缺失昵称、并发去重、失败回滚、state 过期／重放／浏览器切换、绑定冲突、最后登录方式保护、设置首次密码、账号停用及删除后重新注册、普通邮箱注册兼容、限流和日志脱敏。
