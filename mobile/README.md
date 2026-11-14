# 知穗 App（iOS / Android）

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
2. **Apple Developer Program**（$99/年）—— 没有它，包构建出来也传不上 TestFlight

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

## 六、把 5 个人拉进来

**内部测试员不需要苹果审核**，这是下周能出结果的关键。

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
