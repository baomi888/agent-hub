# 知穗 Android 端

agent-hub 的移动客户端。**当前是离线演示版**：所有回答由本地语料生成，不发任何网络请求。

## 装到手机上

产物在 `app/build/outputs/apk/debug/app-debug.apk`。

方式一（推荐）：把 APK 传到手机（微信文件传输 / 数据线 / 网盘），点开安装，允许「未知来源」。

方式二：手机开 USB 调试并连电脑，然后

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

## 打开工程

用 Android Studio 直接 Open 本目录（`app-android/`），点 Run 即可。

命令行构建（需要 Gradle 9.x + JDK 17+，本工程未带 wrapper）：

```bash
gradle assembleDebug
```

本机的 Gradle 不在 PATH，用已解压的发行版：

```powershell
& "$env:USERPROFILE\.gradle\wrapper\dists\gradle-9.5.0-bin\bvnork1r7n8i6kp5cnkibsc9q\gradle-9.5.0\bin\gradle.bat" assembleDebug
```

工具链：AGP 9.3.1 / Kotlin 2.2.10（AGP 内置）/ Gradle 9.5 / JDK 24 / compileSdk 37 / minSdk 26。

> **AGP 9 注意**：不能再 apply `org.jetbrains.kotlin.android`，AGP 已内置 Kotlin 编译，
> 重复应用会报 `Cannot add extension with name 'kotlin'`。但 Compose 编译器插件
> `org.jetbrains.kotlin.plugin.compose` 仍须显式声明。

## 分层

严格按 DDD 三层，依赖只允许向下：

```
ui/          Compose Screen · ViewModel · UiState
domain/      Conversation 聚合 · 仓储接口 · 值对象      ← 纯 Kotlin，不 import android
data/        Mock 数据源（将来：Remote SSE + Room）
```

装配点只有一处：`AppContainer.kt`。

### 为什么 domain 里没有 Android

`domain/` 全部是 pure Kotlin，不引用任何 `androidx.*`。好处是将来把 `domain` + `data` 抽成
KMP `commonMain` 时，iOS 可以直接复用；坏处是拿不到 `Context`，所以持久化只能靠仓储实现方注入。

### Conversation 聚合守的两条不变式

1. 同一时刻最多一路 `STREAMING`；
2. 已结束（DONE / ABORTED / FAILED）的轮次不再接受 token。

第 2 条是客户端特有的：移动网络乱序与重连是常态，迟到的 delta 必须被安静丢弃，而不是抛异常打断 UI。
服务端的聚合守的是事务不变式（额度不能为负），客户端守的是交互状态机 —— 这是同一套 DDD 思想在两端的差别。

## 接入真实后端要改什么

### 1. 服务端先补三个缺口

| 缺口 | 现状 | 必须先改成 |
|---|---|---|
| 不返回 token | `AUTH_RETURN_TOKEN=0`，只下发 HttpOnly Cookie | App 没有 Cookie 容器，登录必须能拿到 token（或加 refresh 端点） |
| SSE 是 POST | `POST /api/chat/stream` | 客户端不能用 okhttp-sse 的 `EventSource`（它 `require(method == "GET")`），需手写解析或改用 Ktor client |
| 只有 HTTP | `http://8.163.62.25:3000` | Android 9+ 默认禁明文；要么等 HTTPS 就绪，要么 debug 包临时开 `usesCleartextTraffic` |

### 2. 客户端新增一个实现

新建 `data/remote/RemoteConversationRepository.kt`，实现 `ConversationRepository`：

```kotlin
override fun send(id: ConversationId, text: String): Flow<ChatEvent> = flow {
    // OkHttp Call 拿到 ResponseBody.source()，按行读：
    //   "event: token" → 下一行 "data: {...}"
    //   空行 = 一帧结束
    // 逐帧翻译成 ChatEvent 后 emit
}.flowOn(Dispatchers.IO)
```

然后在 `AppContainer` 里把 `MockConversationRepository()` 换成它。UI、ViewModel、聚合一行都不用动。

### 3. 事件映射表（已确认的真实协议）

| SSE event | data 字段 | 对应 `ChatEvent` |
|---|---|---|
| `status` | `text` | `Status` |
| `token` | `text`（增量） | `Token` |
| `sources` | `items[]` | `Sources` |
| `grounding` | `level` / `note` / `hitCount` | `Grounding` |
| `done` | `answer` / `title` / `mode` / `message_id` | `Done` |
| `error` | `detail` | `Failed` |
| `tool` / `hybrid_start` / `mode_switch` / `approval` | 见 `api/chat.py` | 首版暂不处理，扩展 `ChatEvent` 即可 |

## 目录速查

```
app/src/main/java/com/zhisui/app/
├── AppContainer.kt            依赖装配（唯一接线点）
├── ZhisuiApp.kt / MainActivity.kt
├── domain/
│   ├── model/                 Conversation · Turn · ChatEvent · UsageQuota
│   └── repository/            ConversationRepository · StudyRepository · ProfileRepository
├── data/mock/                 ReplyLibrary · FakeStreamingClient · Mock*Repository · SeedData
└── ui/
    ├── theme/                 深/浅两套色板
    ├── components/            SoftCard · ProgressRing · MiniBars · StreamingDots
    ├── chat/                  列表 + 聊天 + ViewModel
    ├── study/                 学习路径 / 打卡 / 周统计
    └── profile/               配额 / 偏好 / 外观
```

## 已知限制

- 数据不持久化，杀进程后回到预置的三条示例会话；
- 没有登录页，身份是写死的演示用户；
- 没有 `tool` / `approval` 事件的可视化（需要在 `ChatEvent` 里加分支）。
