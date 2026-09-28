# 设计令牌对照：苞米地笔记本 × LobeHub

对照对象：[`lobehub/lobehub`](https://github.com/lobehub/lobehub)（原 `lobe-chat`）的 `DESIGN.md` / `DESIGN.dark.md`。
它把设计系统写成了正式文档（各约 200 行，含 YAML 令牌表 + 散文规范），是同类项目里少见的、可直接对照的样本。

对照日期：2026-09-28。所有"现状"数据均为本机 `frontend/src` 实测，非估算。

---

## 1. 逐层对照

| 层 | LobeHub | 苞米现状 | 判定 |
|---|---|---|---|
| **表面** | 4 档 `colorBgLayout / Container / ContainerSecondary / Elevated` | `--step-0/1/2` 三档（ΔL\* +4.2 / +3.5）+ `--desk-*` | 已有。缺"浮层专用"一档，但浮层现在用 `--card-bg` + `--shadow-2`，够用 |
| **文本** | 4 档 `colorText / Secondary / Tertiary / Quaternary` | `--text-1/2/3` 三档 | 已有。缺第四档 disabled，现用 `opacity` 代替 |
| **边框** | 2 级 `colorBorder / colorBorderSecondary` | `--line` / `--line-strong` | 一致 ✓ |
| **填充** | 4 级 `colorFill / Secondary / Tertiary / Quaternary`（半透明，叠任何底色） | 只有 `--accent-soft` / `--bg-subtle` / `--accent-line` 几个零散值 | **缺一整层** |
| **功能色** | 5 个：Primary / Success / Warning / Error / Info，每个派生 Hover·Active·Bg·Border·Text·Fill 斜坡 | 只有 `--accent`(+`-ink`) 与 `--red`(+`-ink`) | **缺 Success / Warning / Info，且现为硬写** |
| **阴影** | 3 级 | `--shadow-1` / `--shadow-2` 两级 | 已有。纸质调性下两级更合适，不补第三级 |
| **字号** | 正文 12/14/16 + 标题 5 档（38→16），明令"没有 13px，13 是 drift" | 只有 `--msg-fs` / `--hero-fs` 两个变量 | **缺一整层** |
| **行高** | 2 档（1.5714 / 1.6667） | `--lh-tight/body/loose` 三档 | 已有，比它细 |
| **字距** | 无令牌 | `--ls-wide/mid/tight` 三档 | **苞米更强**，它没有 |
| **间距** | 4px 基础 7 级：4/8/12/16/20/24/32 | 无令牌 | **缺一整层** |
| **圆角** | 4 档 4/6/8/12 | 4 档 4/8/12/16 | 已有。值偏大是纸质"软"的需要，保留 |
| **控件高度** | 28 / 36 / 40 三级 | 无 | **缺** |
| **图标尺寸** | 12/14/16/18/20 五级，明确"是尺寸不是间距" | 无，硬写 10 种 | **缺** |
| **动效** | 状态 100–200ms、浮层 ~300ms | `--t-color` 140ms / `--t-move` 220ms | 一致 ✓ |

**结论：14 层里 8 层已有（其中 2 层比它更细），6 层缺失。缺的 6 层里，填充和字号是真痛。**

---

## 2. 三个真缺口

### 缺口 1 · 中性填充阶梯（最该先补）

hover 态现在的背景写法，实测分布：

```
var(--accent-soft)   12 处   ← 金色
var(--accent)        10 处   ← 金色
var(--panel)          5 处
var(--bg-subtle)      4 处   ← 也是金色（rgba(200,137,42,.08)）
var(--card-bg)        3 处
...
```

**22 处 hover 在刷金色。** 这正是 LobeHub 明文禁止的那条：

> Do keep solid `colorPrimary` for the single most important action and for state.
> **Don't spread brand color as decoration.**

一个列表项、一个卡片、一个 icon 按钮被指到，整块就泛金——品牌色在这里承担的是"我变了"这个中性信号，不是强调。

补法（四级，全部半透明，叠在任何纸面上都成立）：

```css
--fill-1: rgba(61, 47, 26, 0.12);  /* active / 按下 */
--fill-2: rgba(61, 47, 26, 0.06);  /* hover */
--fill-3: rgba(61, 47, 26, 0.03);  /* 极淡 hover */
--fill-4: rgba(61, 47, 26, 0.015); /* 选中底 */
```

实测安全性：最强的一档 α=0.12 叠在画布 `#F5F0E1` 上得到 `#DFD9C9`，其上的正文 `#3D2F1A` 仍有 **9.20:1**、次级 `#5A4C33` **5.92:1**，都不会破线。

替换策略：`--accent-soft` 的 12 处里，**只有主操作（`.send`、`.btn-new`）保留金色**，其余全部换成 `--fill-2`。

### 缺口 2 · 字号阶梯

`font-size` 实测 **14 种取值**：

```
12px ×16   11px ×15   10px ×9   13px ×8   14px ×5   12.5px ×5
11.5px ×4  var(--msg-fs) ×2  10.5px ×2  var(--hero-fs) ×1
20px ×1    14.5px ×1  13.5px ×1  inherit ×1
```

其中 **10 / 10.5 / 11 / 11.5 / 12 / 12.5 / 13 / 13.5 / 14 / 14.5 十个值挤在 4.5px 的区间里**——相邻两档差 0.5px，人眼分辨不出，只是"当初手写的那个数"。

LobeHub 的处理很直接：正文/标签只留 12 / 14 / 16，明确写 "there is no 13px token… treat that as drift and round to 12 or 14"。

建议五档（比它多一档，因为中文小字需求更密）：

```css
--fs-xs:  11px;  /* 角标、序号 */
--fs-sm:  12px;  /* 元信息、时间戳 */
--fs-base:13px;  /* 次要说明（它禁 13，但中文 12→14 跨度太大，保留） */
--fs-md:  14px;  /* UI 正文 */
--fs-lg:  16px;  /* 强调、大控件 */
```

`--msg-fs`（用户可调，13.5/15/16.5）独立保留，不并进去——它是运行时变量不是阶梯。

### 缺口 3 · 语义色 Success / Warning / Info

现在完全没有这三个令牌，硬写在两处：

```css
kb.css:527   .toast.success { background: rgba(30, 122, 70, 0.9); }
kb.css:532   .toast.error   { background: rgba(180, 71, 47, 0.92); }
layout.css:216               background: rgba(30, 122, 70, 0.92);
```

照 `--accent` / `--accent-ink`、`--red` / `--red-ink` 的同一套分工补全（本体给描边/图标/填充，`-ink` 给文字）：

```css
--success: #2A6B45;   --success-ink: #2A6B45;  /* 侧栏纸 5.03:1 */
--warning: #8A5A0A;   --warning-ink: #8A5A0A;  /* 侧栏纸 4.66:1 */
--info:    #2A5A8A;   --info-ink:    #2A5A8A;  /* 侧栏纸 5.65:1 */
```

三个 `-ink` 值都按"最暗的侧栏纸 `#EDE4C8`"验算过，全部 ≥4.5:1。三档纸面上的完整数据：

| | 侧栏 #EDE4C8 | 画布 #F5F0E1 | 卡片 #FDFAF2 |
|---|---|---|---|
| success-ink `#2A6B45` | 5.03 | 5.61 | 6.12 |
| warning-ink `#8A5A0A` | 4.66 | 5.20 | 5.68 |
| info-ink `#2A5A8A` | 5.65 | 6.30 | 6.88 |

深色三档已补（对最亮的卡片 `#292217` 验算，全部 ≥4.5:1）：

| | 侧栏 #1A1610 | 画布 #211B11 | 卡片 #292217 |
|---|---|---|---|
| success-ink `#7ECB9B` | 9.35 | 8.87 | 8.16 |
| warning-ink `#E8B54E` | 9.56 | 9.07 | 8.35 |
| info-ink `#8AB6E0` | 8.44 | 8.01 | 7.37 |

另外补了 `--red-solid`（浅 `#B4472F` / 深 `#A03E28`）给"红底白字"专用：深色的 `--red` 提亮成 `#D9705A` 后压白字只剩 2.9:1，描边/图标可以继续用它，但 toast.error 这类实心底得换 `--red-solid`（6.0:1）。

---

## 3. 四个斟酌项（不一定照抄）

**间距阶梯** — 它用 4px 基础 7 级。苞米现在 `gap` 有 10 种取值，其中 `6px`×7 与 `10px`×7 是高频真实需求，落在 4px 阶梯外。中文小控件密排时 4px 步长偏粗，**建议保留 2px 半档**（4/6/8/10/12/16/20/24/32），别硬套。

**表面第四档 Elevated** — 它区分 Container 与 Elevated。苞米的浮层（`.ability-pop`、`.fs-picker`、toast）已经用 `--card-bg` + `--shadow-2` 拉开了，再补一档收益不大。

**文本第四档 Quaternary** — 它是专门给 disabled 的。苞米三处 disabled 都用 `opacity`，视觉结果一样，补令牌反而要改三处。优先级低。

**圆角值** — 它的 4/6/8/12，苞米是 4/8/12/16。后者偏软，是纸质调性的需要（糙纸不该有锐利的紧圆角）。**保留自己的。**

---

## 4. 苞米比它强的四项，别丢

对照是双向的。这四层它没有，但苞米有，而且是为"纸质"这个定位服务的：

- **`--noise` / `--grain-noise`** — 两条 SVG feTurbulence 烘焙的纸纹，书脊用暖褐、全页用中性白。它的规范里没有材质层。
- **`--canvas-light` / `--desk-0/1`** — "光从左边来"的桌面与画布高光。
- **`--veil` / `--veil-strong`** — 半透明纸面盖层。
- **`--measure` + 1600px 断点 + `--msg-fs` 用户可调三档 + 字距三档** — 阅读栏宽与字号偏好，它完全没有对应概念。

这几项是苞米的身份，做收口时不要为了"对齐 LobeHub"而砍掉。

---

## 5. 第四批执行结果（2026-09-28 已完成）

三个真缺口全部补齐，"斟酌项"按原判断不动。

**缺口 1 · 填充阶梯** — 新增 `--fill-1~4`（浅：暖墨 alpha .12/.07/.035/.018；深：暖白 alpha .16/.09/.05/.025），`--bg-subtle` 收编为 `var(--fill-3)`。规则写进 tokens.css 注释：**瞬时反馈（hover / focus / 按下）用中性填充，持久状态（`.on` / `.active` / `:target`）与主操作（`.send` / `.btn-new` / `.pv-btn.primary`）才用金**。按这条换了 24 处：CSS 19 处（`--accent-soft`×9、`--accent`×5、`--bg-subtle`×3、`--accent-line` 等 2 处）+ tsx 5 处 `hover:bg-subtle` / `hover:bg-accent-soft`。顺带两笔语义修正：删除类按钮 hover 从金改红（`.attach-rm`）、计划面板 `.plan-export` 的实心金改中性。

**缺口 2 · 字号阶梯** — 新增 `--fs-badge 10 / --fs-xs 11 / --fs-sm 12 / --fs-md 13 / --fs-lg 14 / --fs-xl 16`，另留 `--fs-title 20`（面板衬线标题）与 badge 两个例外。CSS 侧 66 处、tsx 侧 19 处硬编码 px 全部收进档位，`--fs-*` 经 `@theme inline` 接出 `text-fs-*` 供 Tailwind 用。额外修掉一个真 bug：正文与输入框的 `text-[15px]` 是写死的，用户调字号档位时它们纹丝不动，现在走 `--text-msg: var(--msg-fs)`。

**缺口 3 · 语义色** — success / warning / info 三对（本体 + `-ink`）浅深两态全部落地，替换掉 toast 里硬写的 `rgba(30,122,70,.9)`、`rgba(160,62,42,.94)` 等 6 处。`.status .dot` 与 `.kb-chip .dot` 从金改 `--success`（"在线/已绑定"是成功语义，不该跟强调色同色）。

**没动的部分**：间距阶梯（保留 2px 半档）、Elevated 第四档、Quaternary 文本档、圆角 4/8/12/16——理由见第 3 节，均按原判断保留。

验证：CSS 编译 91820 字节，`--fill-*` / `--fs-*` / 语义色令牌与 `bg-fill-2` / `text-fs-*` / `text-msg` 工具类均确认进产物；CSS 层 `font-size: NNpx` 残留 **0**；`tsc --noEmit` 0 错；ESLint 仍是那 6 个既有 error。

---

## 6. 一句话

**你缺的不是审美判断，是把判断固化成令牌的纪律。** 前三批证明了你能做出正确决定（明度三档、阴影两级、语义红分工、行高三档都对），LobeHub 的价值在于它把这些决定**预先写成了有名字的层**——这样下一个写组件的人不用重新判断一次，直接取名字就行。缺的六层里，填充和字号最痛。
