# 苞米Agent 前端设计迭代提示词

## 项目背景
这是一个基于 Next.js 15 + Tailwind CSS v4 的 AI Agent 聊天应用前端，位于 `frontend/` 目录。
产品名：**苞米Agent**，一个带 RAG 知识库和多工具调用的对话式 AI 工作台。

## 设计目标
把当前界面打磨成 **「苞米地笔记本」** 风格：温暖的糙纸质感、成熟玉米金色点缀、克制有品味，拒绝千篇一律的 AI 产品默认皮肤。

## 当前文件结构
```
frontend/src/
├── app/
│   ├── globals.css      # 全局样式 + CSS 变量（主题令牌都在这里）
│   ├── layout.tsx
│   └── page.tsx
├── components/
│   ├── Sidebar.tsx       # 左侧会话列表栏
│   ├── ChatArea.tsx      # 中间聊天主区
│   └── KnowledgePanel.tsx # 右侧知识库面板
└── public/
    └── corn-logo.png     # 玉米穗品牌图标（已存在）
```

## 设计 Token（CSS 变量，在 globals.css 的 :root 和 .dark 中）

### 浅色模式
```
--bg: #F4EDD8          /* 页面底：暖米黄糙纸 */
--panel: #F9F3E0       /* 面板底：稍亮的纸 */
--ink: #221C10         /* 正文：暖棕墨 */
--text-2: #6B5E42      /* 次级文字 */
--text-3: #9C8B66      /* 弱文字 */
--accent: #C8892A      /* 成熟玉米暖金 */
--accent-soft: rgba(200, 137, 42, 0.10)
--line: rgba(34, 28, 16, 0.12)
--line-strong: rgba(34, 28, 16, 0.35)
```

### 深色模式
```
--bg: #1A1610          /* 烤焦玉米芯深棕黑 */
--panel: #221C12
--ink: #EDE0C4         /* 暖米白 */
--accent: #E0A03A      /* 暗室玉米金 */
```

### 圆角
```
--radius-sm: 6px
--radius-md: 10px
--radius-lg: 14px
```

### 字体
- 标题/品牌名：`"Noto Serif SC", "Songti SC", serif`（衬线体）
- 正文：`"Noto Sans SC", "PingFang SC", system-ui, sans-serif`

## 需要完成的具体修改

### 1. 修复玉米穗 logo 的背景方块问题
当前 `corn-logo.png` 有米白色背景，放在页面上能看出一个方块。
- **方案**：用 CSS `mix-blend-mode: multiply` 让 logo 背景融入页面底色（浅色模式下）
- 深色模式下用 `mix-blend-mode: screen` 或 `filter: brightness(0) invert(0.85) sepia(1) saturate(2) hue-rotate(5deg)` 把金色线条反白
- 涉及文件：`Sidebar.tsx`（左上角）、`ChatArea.tsx`（欢迎页中央 + 助手头像）

### 2. 左侧栏（Sidebar.tsx）优化
- 左侧栏背景色用 `--bg`，与中间区用 1px `--line` 分隔，不要有阴影
- "新建对话"按钮：墨色底 `--solid`，圆角 `--radius-sm`，hover 变金色 `--accent`
- 会话列表项：hover 时背景 `--accent-soft`，选中项左侧有 3px 金色竖条
- 搜索框：下划线式，聚焦时下划线变金色
- 底部深色模式开关和在线状态保持简洁

### 3. 欢迎页（ChatArea.tsx 空状态）
- 中央玉米穗 logo：尺寸约 80px，金色线条，居中
- 眉题：`苞米地 · 智能体工作台`，10px，字距 0.22em，金色
- 大标题：`你好呀，我是苞米`，衬线体，26px，加粗
- 副标题：`多知识库检索 · 联网搜索 · 数学计算 · 天气查询，一次提问统一调度`，15px，次级色
- 四个能力卡片（查资料/联网搜索/数学计算/查天气）：
  - 2×2 网格，圆角 `--radius-md`
  - 背景 `--panel`，1px `--line` 边框
  - hover：边框变金色，轻微上浮 `translateY(-2px)`，柔和金色阴影
  - 序号 01-04 用金色衬线小字
  - 卡片间距 8px

### 4. 输入框区域
- 容器：圆角 `--radius-lg`，背景 `--panel`，1px `--line-strong` 边框
- 聚焦：边框变金色，外加 3px `--accent-soft` 光晕
- 发送按钮：圆形 38px，金色底 `--accent`，白色箭头图标，hover 放大 1.05 倍
- 附件回形针图标：弱色，hover 变金色
- 模式切换（自动/检索/智能体/纯对话）：文字按钮，选中态金色+底部 2px 金色下划线

### 5. 右侧知识库面板（KnowledgePanel.tsx）
- 与左侧栏对称，背景 `--bg`
- "导入资料"按钮：描边按钮，hover 金色填充
- 知识库条目：像贴在本子上的标签，细边框，选中态金色左边框
- 复选框：方形 15px，选中态金色填充

### 6. 聊天消息区
- 用户消息：右对齐，墨色底 `--solid`，纸色文字，圆角 `--radius-sm`
- 助手消息：左对齐，左侧放小玉米穗头像（28px），文字直接在背景上
- 引用角标 `[1]`：金色小方块，白色数字
- 工具调用折叠条：浅金底 `--accent-soft`，细边框
- 操作栏（复制/转发/重试/赞踩）：小描边按钮，hover 浅金底

### 7. 纸纹质感
- 保留 `.grain` 全局噪点覆盖层，浅色 opacity 0.9，深色 opacity 0.7
- 用 `mix-blend-mode: overlay`

### 8. 深色模式
- 所有上述组件在深色模式下用对应的 dark CSS 变量自动适配
- 深色模式下玉米穗 logo 用 filter 反色为浅色
- 输入框聚焦光晕在深色下用 `rgba(224, 160, 58, 0.2)`

## 注意事项
1. **不要改组件的业务逻辑**，只改样式和 className
2. **不要引入新的 npm 依赖**
3. **所有颜色必须用 CSS 变量**，不要硬编码色值（除了 corn-logo 的 filter）
4. **保留现有的 plan-card、附件上传、SSE 流式等功能不动**
5. 改完后确保 `npm run dev` 能正常编译，没有 CSS 报错
6. globals.css 不要加 UTF-8 BOM（用无 BOM UTF-8 保存）

## 参考感觉
想象一本摊开的、有轻微纸纹的笔记本，放在阳光斜照的木桌上。整体温暖、安静、有手作感，不是冰冷的 SaaS 后台。金色是成熟玉米的颜色，不是荧光黄。
