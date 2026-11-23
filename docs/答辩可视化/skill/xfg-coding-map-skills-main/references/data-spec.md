# 代码地图数据规范（MAP-DATA 区间）

生成时只动两处：**MAP-DATA 数据区间**（模板中 `MAP-DATA-START` 到 `MAP-DATA-END ═` 之间整体替换）和 **MAP-META 元信息**（HTML 里标注的 title / h1 / 副标题 / stats / 欢迎语 / 包名 / 说明标题）。引擎区一行不改。

## 页签（tab）

模板默认 3 个页签：`over`（全景图）/ `seq`（时序图）/ `flow`（流程图），引擎从 DOM 自动发现页签数量。

- **裁剪页签**：删除对应 `.tab`、`#wrap-x`、`#box-x`、`details.src #src-x` 四组元素 + 数据区里对应 `X_SRC` 常量即可，引擎零改动（最少保留 1 个页签）。
- **页签 → 图源约定**：`over → OVER_SRC`、`seq → SEQ_SRC`、`flow → FLOW_SRC`。新增第四个页签需要同步改引擎启动渲染映射，不推荐。

## 数据结构

### OVER_SRC / SEQ_SRC / FLOW_SRC（mermaid 图源码，模板字符串）

- **OVER_SRC（flowchart LR）**：模块地图。subgraph 按架构分层（app / domain / infrastructure…），节点 = 类/组件。职责：鸟瞰"东西放在哪、依赖方向"。
- **SEQ_SRC（sequenceDiagram）**：核心运行链路。participant = 参与类，`autonumber` 必开。职责：讲清"一次调用怎么流"。
- **FLOW_SRC（flowchart TB）**：方法级主流程 + 分支。职责：讲清"关键方法的内部逻辑与异常路径"。
- 每个图中**新增节点标签必须带 🆕**（时序图 participant 也一样）——既是视觉标记，也是时序图渲染后自动补色的依据（含 🆕 的 actor 涂绿，否则涂灰虚线）。

**mermaid 语法红线（validate-map.js 会静态拦截）**：
- **flowchart 禁止嵌套 subgraph**：打包版 mermaid.min.js 解析不了 subgraph 里再套 subgraph（报 Syntax error）。需要分层分组时拍平为同层 subgraph，用命名前缀区分（如 `domain · trial 规则树节点`）。
- **时序图 actor 标识符禁止用独立 `END`/`end`**（不分大小写）：会与 par/loop 块的 end 关键字冲突，lexer 报 "Expecting ACTOR, got 'end'"。改用 ENDN、EndNode 等更长标识符（别名 `as` 后的显示名不受限）。

**五色 classDef 体系（保持一致，不要自创颜色）**：

```
newc  绿色实线粗边  fill:#e6f6ec,stroke:#17a45f,stroke-width:2px,color:#0b5c3a   本节/本次新增
oldc  灰色虚线      fill:#f1f3f6,stroke:#9aa3af,stroke-width:1.2px,stroke-dasharray:5 3  基线复用/未动
extc  蓝色          fill:#eaf1fe,stroke:#3574f0                                     外部系统/网关
warnc 橙色          fill:#fff4e0,stroke:#e6a23c                                     分支/兜底/警告路径
errc  红色          fill:#fdecec,stroke:#e5484d                                     错误路径
```

### TREE（左侧工程树，数组）

```js
{ t:'module'|'pkg'|'class'|'iface'|'method',   // 节点类型（决定图标）
  n:'名称',                                     // 显示名；method 可带中文说明，如 'main()：装配 + 提问'
  b:'new'|'mod'|'old',                          // 变更徽标，可省略；method 不显示徽标
  doc:'说明卡id' | null,                        // 关联右侧 DOCS
  kw:['图源码文本关键词', ...],                 // 图内联动锚点
  children:[...] }                              // 嵌套子节点
```

层级约定：`module → pkg → class/iface → method`。class/iface 的 `n` 用真实代码名（mono 字体），method 可写"签名：一句职责"。

**kw 规则（最重要，防止联动断链）**：
- 每个关键词必须是某张图源码文本的**子串**（`validate-map.js` 会逐一校验）。
- 优先用**类全名/方法全名**（`DeepSeekAdapter`、`translateSse`），避免宽泛短词（`AUTH`、`options` 会误命中多节点）。
- 类节点 kw = 类名；method 节点 kw = 方法名 + 1~2 个图内相关文本；分组节点（module/pkg）不写 kw。
- 树节点在图里没有对应物时（如仅注释改动的类），**在 OVER_SRC 里补一个小节点**（oldc 灰色）作为锚点，而不是留空 kw。

### DOCS（右侧说明卡，数组）

```js
{ id:'唯一id',            // 树节点 doc 字段引用它；重复 id 是错误
  b:'新增'|'调整'|'测试'|'复用',   // 决定徽标颜色（绿/橙/紫/灰）和右栏过滤 chip
  t:'标题 — 一句话定位',    // 如 'ILlmRuntimePort — 运行时端口（领域插座）'
  f:'文件路径 · 行数统计',   // mono 风格显示，如 'domain/…/llm/adapter/port/ILlmRuntimePort.java · +98 行'
  body:`<ul><li><b>要点</b>：…<code>代码</code>…</li>…</ul>` }   // HTML 片段，4-6 条 li 最佳
```

卡片内容三问：**它是什么（定位）→ 关键设计/规则（2-4 条，加粗关键词）→ 容易忽略的细节/坑**。避免把代码大段贴进卡片。

### TOUR（导览站点，数组）

```js
{ tab:'over'|'seq'|'flow',  // 站点所在图页
  tree:'树行名称片段'|null,  // 要联动的树节点（名称 includes 匹配）；null = 纯总览站
  sl:'导航条短标题',          // 2-6 字，如 '装配起点'
  title:'第 N 站 · 标题',
  text:'讲解文案' }           // 支持 <b>/<code> HTML；2-4 句
```

动线设计（6-10 站最佳）：
1. **首站**：`tree:null` 总览站，讲地图结构；
2. **中间站**：沿"装配 → 核心抽象 → 主链路 → 分支/兜底 → 组装收尾"的开发叙事顺序，每站联动一个类/方法；
3. **末站**：`tree:null` 总结站，引导自由探索。

每站 text 结构建议：**这站是什么 → 在图上看什么（指路）→ 设计动机/易错点**。

## 元信息替换清单（MAP-META 注释处）

| 位置 | 内容 |
|---|---|
| `<title>` / `header h1` | 地图标题，如 "xfg-2-2 开发串联全景图" |
| `header p` | 基线 → 本节（或需求/分支名）+ 主题 |
| `.stats` | 新增文件数 / 调整文件数 / +行数 / -行数（来自 git diff 或需求估算） |
| `.tree-head .proj` | 顶层包名/项目坐标 |
| `#docHead .cap` | 右栏标题，如 "变更说明 · vs xfg-1-3" |
| 欢迎浮层 `w-sub` 与列表 | 与本图场景匹配的三条引导 |
| localStorage key `xfg21-welcomed` | **每张新图必须换唯一 key**（如 `<slug>-welcomed`），否则老用户看不到新图的欢迎层 |

## 生成后必须校验

```bash
node <技能目录>/scripts/validate-map.js <输出的html路径>
```

0 错误才算完成；警告逐条评估处理。校验通过后再 `present_files` 交付。
