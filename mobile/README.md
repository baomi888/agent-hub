# 知穗 App（iOS / Android）

> **状态：暂停（2026-11-14 决策）。不要删除本目录。**
>
> 决定：**暂不投入 iOS 客户端**，移动端继续用 PWA（Safari 添加到主屏幕）承载。
> 原因是 PWA 已经能覆盖当下的核心需求，而 iOS 客户端的唯一增量价值是
> 「给不认识的人分发」+「不依赖 Safari」，这两件事要等 Web 端成熟后再做。
>
> 保留价值：工程**已验证可构建**（`tsc` 通过、`expo-doctor` 20/20、
> iOS bundle 612 模块 977KB），Apple 账号到位后三条命令就能出包。
> 删掉等于将来重做一遍选型、踩坑和验证。
>
> 恢复的判据见文末「十、什么时候重启」。

Expo + `react-native-webview` 的外壳，里面装的是 `https://myzhisui.cn`。
**业务代码只有一份（Web），App 是它的原生容器。**

---

## 一、为什么是壳，不是重写

团队 5 个人，Web 上已经有 14 个控制台页面 + 论坛（发图 / 举报 / 楼中楼 / 单皮肤）。
重写一遍意味着**永久双份维护**：以后每加一个功能都要写两遍、验两遍。

壳的方案只有一个真相：Web 部署完，用户下次打开 App 就是新版，**不用发版、不用等审核**。
这是当前人力和迭代速度下唯一撑得住的形态。

将来想把「对话 / 学习路径 / 打卡」这几个高频页做成原生，加新屏幕即可，
现有结构不用推翻 —— 这也是选 Expo 而不是 Capacitor 的原因。

---

## 二、目录

| 文件 | 作用 |
|---|---|
| `App.tsx` | 唯一一屏：WebView + 加载态 + 错误重试 + 返回键 + 外链拦截 |
| `app.json` | 包名、图标、权限描述、启动图 |
| `eas.json` | 云构建配置（`preview` 出内测包） |
| `assets/` | 图标与启动图，由 `tools/make_app_icons.py` 生成 |

`app.json` 是**严格 JSON，不能写注释**。每个字段为什么这么填，看下面第三节。

---

## 三、几个不能改错的字段

| 字段 | 值 | 改错的后果 |
|---|---|---|
| `ios.bundleIdentifier` | `cn.myzhisui.zhisui` | **上传过一次就改不了**，改了等于另一个 App，测试员要重新装 |
| `userInterfaceStyle` | `automatic` | 写成 `light` 的话，系统开深色时 App 里永远浅色，和浏览器不一致 |
| `ios.infoPlist.NSPhotoLibraryUsageDescription` | 有 | **没有就闪退**（论坛发图点「选图片」那一刻进程被 kill，不报错） |
| `ios.buildNumber` | 每次上传 +1 | 重复会被 App Store Connect 拒收（EAS 生产 profile 已开 `autoIncrement`） |
| `ios.supportsTablet` | `false` | 开真的话审核会要 iPad 截图，而布局没在 iPad 上验过 |

---

## 四、本地预览（不需要 Mac）

```bash
cd mobile
npm install
npx expo start
```

手机装 **Expo Go**，扫终端里的二维码。

⚠️ Expo Go 里能看界面和交互，但**安全区、相册权限、启动屏**这三项与正式包行为不同，
最终以 TestFlight 装出来的包为准。

---

## 五、出包（不需要 Mac，在 Windows 上就能跑）

### 前提

1. **Expo 账号**（免费）：https://expo.dev/signup
2. **Apple Developer Program**（$99/年，个人账号即可）—— 走 Ad Hoc 或 TestFlight 都绕不过它。
   没有它，包构建出来也签不了名、装不上任何一台真机

### 步骤

```bash
cd mobile

# 1. 登录 Expo
npx eas login

# 2. 首次运行，会创建 EAS 项目并写 projectId
npx eas build:configure

# 3. 构建 iOS 内测包（云端 Mac 编译，约 15–25 分钟）
npx eas build --platform ios --profile preview
```

第 3 步会问你：

- **要不要用 Apple 账号登录** → 选是，输 Apple ID + 密码 + **双重验证码**（手机会弹）
- **是否让 EAS 管理证书** → 选是。**不要自己去开发者后台手动建证书**，那是最容易搞崩的一步

构建完会给一个 `.ipa` 下载链接。

### 传上 TestFlight

```bash
npx eas submit --platform ios --profile production
```

或者把 `.ipa` 下载下来，用 **Transporter**（Mac App Store 里那个）手动上传。

上传后 App Store Connect → TestFlight 里等 **10–30 分钟**处理，状态变成「可供测试」就能装。

---

## 五之二、不上 TestFlight，直接浏览器装（Ad Hoc）

`eas.json` 里 `preview` 的 `distribution: "internal"` 就是这条路：**产物是 Ad Hoc 签名的 ipa，
浏览器点链接直接装到桌面，全程没有 Apple 审核、没有 TestFlight、不进 App Store。**

### 但它**不会**出现「信任企业级开发者」

这一点必须说清，否则装的时候会以为失败了：

| | Ad Hoc（`internal`） | 企业证书（In-House） |
|---|---|---|
| 账号 | Developer Program $99 | **Enterprise Program $299** |
| 门槛 | 身份证 | D-U-N-S + 公司实体 + 苹果人工审核 + 年营收/员工规模 |
| 设备上限 | **100 台/年，按 UDID 白名单** | 不限 |
| 设置里「信任」 | **不需要** | 必须（设置 → 通用 → VPN与设备管理） |
| 掉签风险 | 无 | **有，滥用被封是常态**（国内「掉签」就是它） |

那个「改设置同意信任」的步骤是**企业证书**的特征。企业账号学生团队基本申不下来，
而且苹果对滥用查得很紧——一旦封证书，**所有用户的 App 在同一秒内全部打不开**，
只能等重新签名发版。所以这条路不走，也不该走。

Ad Hoc 是苹果官方给的正规做法，做的是同一件事，只是不需要那一步信任。

### 步骤

```bash
cd mobile

# 1. 收集设备：生成注册链接，发给每个人
npx eas device:create
```

它会给你一个 `https://expo.dev/register-device/...` 链接。**用 Safari 打开**，会下载一个
描述文件 → 去「设置」顶部点「已下载描述文件」→ 安装 → 输锁屏密码 → UDID 自动上报到
Expo 和你的 Apple 账号。**不用装 Apple Configurator，不用连电脑。**

```bash
# 2. 看设备登记上了没（必须出现他们的 UDID 才能继续）
npx eas device:list

# 3. 构建（profile preview 就是 ad-hoc）
npx eas build --platform ios --profile preview
```

构建完在 https://expo.dev 的构建详情页有 **Install** 按钮（带二维码）。
把它发给测试的人，**Safari 打开 → 点 Install → 弹窗点「安装」→ 桌面出现图标，直接能开**。

### 三个必须知道的硬约束

1. **设备位是消耗品。** 100 台上限，**同一个 UDID 占位后当年不释放**（次年续费时可清理）。
   别拿它做公开分发，只给内测这几个人。
2. **证书 1 年到期，到期那天所有已装的 App 立刻打不开**，要重新签一遍再发。
   **在手机日历里存一条「11 个月后」的提醒**，这是 Ad Hoc 唯一的运维负担。
3. **协议上限是「测试」。** 给团队成员/老师内测没问题；拿它给不认识的用户长期用，
   严格说违反开发者协议。真要对外开放，还是得上 App Store 或 TestFlight。

### 装不上时按这个顺序查

| 症状 | 原因 |
|---|---|
| 「无法安装此 App，因为无法验证其完整性」 | 这台设备的 UDID **没登记**，或登记后没重新构建 |
| 点链接没反应 / 一直在转 | 用了**微信或 QQ 浏览器**打开的。必须 Safari |
| 装完点开闪退 | 证书被吊销（检查 developer.apple.com → Certificates） |
| 提示「已到达设备上限」 | 100 台满了，去后台删掉不用的设备再重建 |

---

## 六、把 5 个人拉进来（TestFlight 路线，二选一）

**内部测试员不需要苹果审核**，这是下周能出结果的关键。
但 Ad Hoc（上面那节）更直接：**不用建 App Store Connect 记录、不用装 TestFlight App**。
两条路选一条即可，别都做。

1. App Store Connect → **用户和访问** → 邀请 5 个人（需要他们的 Apple ID 邮箱），
   角色给「开发者」或「营销」都行，**不需要给 Admin**
2. 回到 **TestFlight → 内部测试 → 添加测试员**，勾上他们
3. 他们手机会收到 TestFlight 的邀请邮件，装 **TestFlight** App，点接受就能装知穗

内部测试员上限 100 人。外部测试员（不审核就能加陌生人）要走 Beta 审核 1–2 天。

---

## 七、上架 App Store 之前必须补的（这周不做）

1. **Guideline 4.2（最低功能要求）**：纯 WebView 壳有被拒风险。对策是至少补一两项
   原生能力（推送、离线缓存、系统分享），或把 3 个高频页做成原生。**TestFlight 不受此限。**
2. **ICP 备案（App 备案）+ 软件著作权**：中国区上架硬门槛，各要 2–4 周。
3. **隐私政策 URL**：App Store 强制要求，要有可访问的页面。
4. **App 隐私标签**：在 App Store Connect 里填「收集哪些数据」。
5. **收费只能走苹果内购**（Guideline 3.1.1）：不能用微信/支付宝绕，苹果抽 15–30%。

---

## 八、已知未验（上真机要重点看的）

- **安全区**：Web 侧 2026-11-14 才补的 `viewport-fit` + 六个容器的 `safe-area-inset-top`，
  一次真机都没跑过。App 里任何一页顶部被刘海压住，去 **Web 侧**补，不要在这里加 padding。
- **论坛发图的相册权限**：`infoPlist` 已声明，但没实测过调起。
- **深色模式状态栏**：`black-translucent` 的实际观感。

---

## 九、常见坑

| 现象 | 原因 |
|---|---|
| 构建报「buildNumber 重复」 | `app.json` 里的 `buildNumber` 没递增；用 `--profile production`（已开自动递增） |
| 上传后 TestFlight 里一直「正在处理」 | 正常，等 10–30 分钟；超过 1 小时去 App Store Connect 看有没有邮件通知 |
| 测试员装不上 | 他没有接受 App Store Connect 的团队邀请，或者 Apple ID 和邀请邮箱不一致 |
| 点「选图片」App 直接退出 | `infoPlist` 里缺用途描述 |
| App 里滚动卡顿 | WebView 里的长列表；如果持续，考虑把该页做成原生 |
| 本机 `npx expo export` 报 `hermesc.exe ... code: 6` | 本机环境问题，**不是代码问题**：① hermesc 往系统 Temp 写临时文件被沙箱拒绝；② 项目路径含中文（`E:\项目资料\...`），LLVM 8 时代的 hermesc 处理不了非 ASCII 路径。**EAS 云端是 Linux，不受影响。** 本地要自证代码没问题，用 `npx expo export --platform ios --no-bytecode` —— 能打出 bundle（612 模块 / 977KB）就说明依赖图和导入都正确 |

---

## 十、什么时候重启

**暂停不等于放弃。** 下面是恢复的三个触发条件，任一成立就该重启这条线。
写在这里是为了避免「等 Web 成熟」变成一个永远不成立的模糊承诺。

### 触发条件（任一）

1. **出现非团队成员的真实用户想要装**。PWA 的死穴就在这里：你没法把
   「添加到主屏幕」教给一个陌生人，也没法让它出现在应用商店里被搜到。
   只要有人问「有 App 吗 / 在哪下载」，就说明分发需求真实存在了。
2. **推送的到达率不够用**。PWA 推送在 iOS 上依赖「已添加到主屏幕 + 用户授权
   + 未清理站点数据」三件事同时成立，任何一环断了就静默失效，而**你无法知道
   用户的订阅什么时候掉了**。App 的 APNs token 反而更稳定。
3. **有人愿意出这 688**（学校账号 / 经费 / 报销批下来）。成本一旦不是障碍，
   这条线的性价比立刻翻过来。

### 重启时要做的（按顺序，别跳）

```bash
# 1. 自检工程还是好的（几分钟，不用账号）
cd mobile
npx tsc --noEmit
npx expo-doctor
npx expo export --platform ios --no-bytecode   # 能出 bundle 就说明依赖没烂

# 2. 账号到位后的三条命令（见「五」与「五之二」）
npx eas login
npx eas device:create        # 收 UDID，100 台上限
npx eas build --platform ios --profile preview
```

### 重启前必须先确认的一件事

**核对 `app.json` 里的 `version` 与 `buildNumber`**，以及 `bundleIdentifier`
是否还是 `cn.myzhisui.zhisui`。这三个字段一旦上传过就不能改，改错了只能换新
bundle id 重新来过。

---

## 十一、这次暂停，哪些工作没有白做

暂停时最容易误判的是「前面做的都浪费了」。实际上这批改动里，**只有 Android/iOS
的原生壳是暂停的，其余全部服务于现在正在用的 PWA**：

| 改动 | 现在的用途 |
|---|---|
| `layout.tsx` 的 `viewportFit: "cover"` | PWA 全屏时安全区才生效，否则 iOS 上恒为 0 |
| 六处容器的 `safe-area-inset-top` | PWA 黑透状态栏下内容不被刘海压 |
| 9 张 `apple-touch-startup-image` 启动屏 | **PWA 从主屏幕启动同样用它** |
| `statusBarStyle: black-translucent` | PWA 的状态栏样式，同一份配置 |
| 学习路径挪进手机 tab | Web 端导航，与 App 无关 |
| `tools/make_ios_splash.py` / `make_app_icons.py` | 改尺寸表后重跑，PWA 与 App 共用素材 |

**唯一暂时用不上的是 `mobile/` 这个壳**，而它的恢复成本是「账号到位后跑三条命令」。
