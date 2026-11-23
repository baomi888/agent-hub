/**
 * 控制台导航清单 —— 桌面侧栏、手机底部 tab、「更多」面板三处共用一份。
 *
 * 为什么单独成文件：三处各写一份清单迟早漂移（"手机上怎么找不到 X"就是这么来的）。
 *
 * ★ 2026-11-14 iOS 内测改造：**移动端不再是「前 4 项」**。
 *
 * 旧模型是 `items.slice(0, TAB_PRIMARY_COUNT)` —— 直接拿桌面顺序的前四项。
 * 结果：「学习路径」在 NAV 里排第 9（桌面顺序是按"什么时候做什么"排的），
 * 而它和「对话」同等重要，在手机上却要进「更多」翻到第 9 项才有入口。
 *
 * 现在拆成两个维度：
 *   · NAV 的数组顺序 = **桌面侧栏**顺序
 *   · `mobileTab: 1..4` = **手机底部 tab** 的位置
 *   · `desktopOnly` = 桌面有、移动端彻底不出现（连「更多」里也没有）
 *
 * 「移出移动端」只影响导航入口，不动路由、不动后端、不删代码 —— 完全可逆。
 */

export interface NavItem {
  href: string;
  /** NavIcon 的名字（单色线性），不是 CornIcon 的插画名 */
  icon: string;
  label: string;
  /** 只有开发者看得到（后端仍会二次校验，这里只是不给入口） */
  adminOnly?: boolean;
  /**
   * 手机底部 tab 的格位（1 起，必须连续）。
   * 不填 = 不进 tab，落到「更多」面板里（除非 desktopOnly）。
   * 用**数字**而不是布尔：tab 顺序要和桌面顺序解耦，否则又变成"改顺序要同时看两处"。
   */
  mobileTab?: number;
  /** 那个格位之后的内容：桌面侧栏照旧可见，手机连「更多」里都不出现 */
  desktopOnly?: boolean;
  /**
   * 「更多」面板里的一项补充说明（只在手机面板渲染，桌面侧栏不用）。
   *
   * 为什么需要它（2026-11-15）：面板里的入口名字都很抽象 ——「工具集」到底
   * 装了 GPA 还是番茄钟，光看两个字猜不到；而 4 个入口排成 3+1 时右下角
   * 空着两格，看着像页面没做完。一句 6–8 字的说明同时补上"这是干什么的"
   * 和"格子怎么填满"。
   *
   * 约束：**只写用途，不复述名字**（"资料库"下面写"资料库管理"等于没写），
   * 也**不写版本号/状态**（"新功能"三个月后就是谎话）。宽度只有约 150px，
   * 超过 12 个字手机上会被省略号截掉。
   */
  hint?: string;
}

export const NAV: NavItem[] = [
  // 对话是最高频入口，手机 tab 第 1 格。
  { href: "/", icon: "chat", label: "对话", mobileTab: 1 },
  // 资料库是**独立页面**（2026-10-30）。原先它指向 /?kb=1，和「对话」落到同一个路由，
  // 只是首页右栏抽屉被拉开 —— 用户的原话是"点击对话和点击知识库都是一样的跳转"。
  // ★ 2026-11-14 改名：侧栏原写「知识库」，而这一页自己的标题、右栏面板、空状态
  //   全都叫「资料库」（我的资料库 / 全部资料库 / 建第一个资料库）。同一个东西两个名字，
  //   用户会以为导航里那个是另一处 —— 统一成「资料库」，后端 kb_* 命名不动。
  // ★ 移动端：不进 tab，落「更多」。检索型操作在手机上不高频，但确实会用。
  { href: "/knowledge", icon: "book", label: "资料库", hint: "课件、笔记、论文" },
  // 论坛中心（2026-11-14）：公开知识库里的讨论汇到一处。
  // 紧跟「知识库」是因为它们是一对 —— 一个是资料、一个是关于资料的讨论；
  // 放到「总结」「打卡」后面会让人以为它是个统计页。
  // ★ 标签只能两个字：「论坛中心」在手机 3 列网格里会挤成两行、把导航撑高
  //   （和「校园工具集」→「工具集」同一个坑）。完整名字在页面标题里。
  // ★ 移动端 tab 第 2 格：UGC 浏览是手机的原生场景。
  { href: "/forum", icon: "forum", label: "论坛", mobileTab: 2 },
  // 待办中心（独立模块，2026-11-09）。固定在第 3 位：它回答的是「什么时候做什么」，
  // 比「每日委托」（生成什么）更靠前。★ 标签只能两个字：写「待办中心」会在手机
  // 3 列网格里挤成两行、把整块导航撑高 —— 和当年「校园工具集」砍成「工具集」同一个坑。
  // 2026-11-10 晚改名：原先叫「提醒」，但页面里的主体是待办，提醒只是它的一部分
  // （「提示什么时候叫我」）。一个词在侧边栏、页标题、tab 三层各指一件事，
  // 连作者自己都说不清区别 —— 统一成「待办」，提醒退回到属性位置。
  // ★ 移动端：不进 tab（3769 行是全站最重的页面，值得单独真机走查），落「更多」。
  { href: "/reminders", icon: "task", label: "待办", hint: "今天要做什么" },
  // 每日委托：2026-11-15 晚**从桌面专属改回移动端可见**（不进 tab，落「更多」）。
  // 11-14 那次标 desktopOnly 的理由是「任务生成完之后用户实际落在的是对话」——
  // 只看到了"生成"那一刻，漏了另外两件事：**改**（七点改八点）和**看**
  // （今天跑没跑、为什么失败）。这两件事在手机上恰恰更该做，而电脑不常开。
  // 触发本次回滚的用户原话：「我的手机端的每日总结和每日委托去哪里了？看不到」。
  // 更关键的组合 bug：手机上它俩**只有首屏那两张卡**这一条路，而首屏卡片
  // 只在 `messages.length === 0` 时渲染 —— 聊过一句之后入口就整块消失了。
  // 桌面侧栏一直有，所以"电脑上好好的、手机上找不到"。
  // 现在与待办 / 资料库 / 工具集同一档：移动端可见、不占底部一级格位。
  {
    href: "/delegate",
    icon: "clock",
    label: "每日委托",
    hint: "到点替你做事",
  },
  // 每日总结：同上，2026-11-15 从 desktopOnly 改回移动端可见。
  // 11-14 的理由是"对比型报表，窄屏读不出结论"—— 实测 390 宽下页面
  // 横向溢出 0、正文完整可读（`.workbuddy/tmp/_probe_p13.log`），
  // 真正窄屏做不了的是**工作流**那一页（要编辑 JSON），不是这一页。
  // ★ 标签从「总结」改成「每日总结」：首屏那张卡叫「每日总结」，同名两处
  //   才认得出是同一件事；4 个字在 3 列模块格里放得下（「每日委托」已经是 4 个字）。
  {
    href: "/report",
    icon: "summary",
    label: "每日总结",
    hint: "今天聊过什么",
  },
  // 打卡：轻交互、每天一次，是手机最合适的形态，tab 第 3 格。
  { href: "/checkin", icon: "star", label: "打卡", mobileTab: 3 },
  // 四六级备考工作区：计划 / 词汇本 / 模考分数（申报书 B3 的专项模块入口）
  // ★ 移动端移出：备考是长周期、要排计划做模考的活，主要在电脑上做；
  //   手机端的碎片学习需求由「工具集」里的单词卡覆盖，不需要重复入口。
  { href: "/cet", icon: "globe", label: "四六级", desktopOnly: true },
  // ★ 2026-11-15 用户要求：**关掉「学习路径」的导航入口**。
  //   `/path` 页面、接口、12 关的判定逻辑一律保留（`service/path.py` 与
  //   `tests/test_path.py` 都没动），去掉的只是这条导航项 —— 完全可逆：
  //   把这两行加回来、并把 TAB_PRIMARY_COUNT 改回 4 即可。
  //   连带影响：手机底部 tab 从 4 格回到 3 格（对话 / 论坛 / 打卡），
  //   编号必须保持 1..N 连续，否则中间会留一个空格位（tests 会红）。
  // { href: "/path", icon: "path", label: "学习路径", mobileTab: 2 },
  // 工具集是**独立单页**（GPA / 番茄钟 / 课程表 / 单词卡 / 倒计时 / 抽签），
  // 不进控制台外壳，也不要求登录。从这里点出去就是换页，左导航仍留在原处；
  // 之前的入口是首页下方那一行，用户反馈"找不到"，所以在这里给一条固定入口。
  // ★ 标签只写「工具集」：写成「校园工具集」时手机上（3 列网格）挤成两行，
  //   把整块导航撑高；完整名字留在那一页自己的标题和落地页里。
  // ★ 移动端：不进 tab，落「更多」。
  { href: "/tools", icon: "tools", label: "工具集", hint: "GPA、番茄钟、课表" },
  // 混合架构：工作流定义 + 切换规则。**这是开发者调试面，不是用户功能**
  // —— 界面上摊着 UUID、任务 id、日志入口和可编辑的 JSON 定义。
  // 2026-10-27 内测反馈："这个为啥要给用户看"。与 /dashboard 同一性质，因此同样加 adminOnly。
  // 注意：这只是不给误点的机会，不是安全边界（真隐藏需要后端也返 404，本期没做）。
  // ★ 2026-11-15 晚：现在 desktopOnly 只剩它和四六级两条。看板已放开到手机，
  //   委托 / 总结也改回移动端可见（见上）。这一页不放：它的主体是可编辑的 JSON
  //   定义和 UUID 列表，390 宽的输入框里改一条 200 字符的规则只能靠横向滚动，
  //   改坏了还没有撤销 —— 不是"不常用"，是"窄屏根本做不了这件事"。
  { href: "/flow", icon: "flow", label: "工作流", adminOnly: true, desktopOnly: true },
  // 数据看板：开发者看"有多少人、在调什么接口、反馈堆了多少"。
  // 只在 is_admin 时渲染入口 —— 但**真正的权限在后端**（非管理员返回 404，见 api/admin.py）。
  //
  // ★ 2026-11-15：**去掉 desktopOnly**。2026-11-14 的 iOS 内测把它当"开发者调试面、
  //   手机上永不需要"一并移出移动端，后果是管理员在手机上点开「更多」只有 4 项、
  //   找不到看板 —— 用户报「数据面板再次无法显示」，查下来页面和接口全都是好的，
  //   缺的只是这一个入口（实测 390 宽下 /dashboard 渲染 13 张卡、横向溢出 0）。
  //   「开发者面」不等于「只在桌面看」：管理员恰好在手机上最想瞄一眼今天有没有人用。
  //   工作流 /flow 保持移出 —— 那一页要编辑 JSON，手机上确实没法用，两者不能类比。
  {
    href: "/dashboard",
    icon: "dashboard",
    label: "看板",
    adminOnly: true,
    hint: "用户、调用、失败",
  },
  // 用户反馈（2026-11-18 从看板独立成页）。
  // 独立的原因不是"内容多"，是**工作节奏不同**：看板每天瞄一眼数字，
  // 反馈要读、要回、要流转、月底要导一份汇总交差。挤在看板第 6 屏的结果是
  // 13 条反馈一个月没人动。两个管理员都能进（同一套 adminOnly 判定）。
  // ★ 标签只能两个字：3 列模块格里 3 个字会挤成两行（和「校园工具集」同一个坑）。
  // ★ 移动端可见、不进 tab：手机上回一句是常见动作，虽然读长文还是电脑舒服。
  {
    href: "/feedback",
    icon: "feedback",
    label: "反馈",
    adminOnly: true,
    hint: "用户提的意见",
  },
  // 设置：移动端落「更多」。手机上改设置是低频但必要（头像、昵称、主题）。
  { href: "/settings", icon: "settings", label: "设置", hint: "头像、主题、密码" },
];

/**
 * 手机底部 tab 有几格（**不含**「更多」按钮）。
 *
 * 必须与 `console.css` 里 `.tabbar { grid-template-columns: repeat(N+1, ...) }` 对齐，
 * 也要与 `mobileTab` 标记的项数一致 —— 三者任一不符，第 6 个 tab 会悄悄挤进第 5 格、
 * 或者最后一格空着，两种都只有截图才看得出来。
 * `tests/test_console_nav.py` 同时锁这三处。
 *
 * 2026-11-15：4 → 3（「学习路径」的入口已按用户要求关掉，tab 跟着少一格）。
 */
export const TAB_PRIMARY_COUNT = 3;

export function visibleNav(isAdmin: boolean | undefined): NavItem[] {
  return NAV.filter((it) => !it.adminOnly || isAdmin);
}

/**
 * 手机底部 tab 的项，**按 mobileTab 数字升序**（不是按 NAV 顺序）。
 *
 * 顺序单独定义，是为了让「桌面侧栏怎么排」和「手机 tab 怎么排」互不牵制 ——
 * 以前改桌面顺序会顺手改掉手机 tab，反过来也一样，这是"手机上怎么找不到 X"的根。
 */
export function mobileTabNav(isAdmin: boolean | undefined): NavItem[] {
  return visibleNav(isAdmin)
    .filter((it) => !it.desktopOnly && typeof it.mobileTab === "number")
    .sort((a, b) => (a.mobileTab as number) - (b.mobileTab as number));
}

/** 「更多」面板里的项：移动端可见、但没占 tab 格位。顺序沿用桌面顺序。 */
export function mobileMoreNav(isAdmin: boolean | undefined): NavItem[] {
  return visibleNav(isAdmin).filter(
    (it) => !it.desktopOnly && typeof it.mobileTab !== "number",
  );
}

/**
 * 对话页左栏的模块入口（桌面轨道与手机抽屉共用同一份）。
 *
 * 为什么需要它（2026-11-15）：对话页不在 `(console)` 外壳里，它自己那栏曾经
 * 硬编码了 4 个入口（委托 / 总结 / 待办 / 设置），和这里完全没有关系 —— 于是
 *   · 手机上这一栏是**唯一**的模块入口（对话页没有底部 tab），却少了
 *     资料库 / 论坛 / 学习路径 / 打卡 / 工具集 / 看板，用户"出不去"；
 *   · 硬编码的两个恰好在 NAV 里标着 `desktopOnly`（手机上彻底不出现），
 *     同一份清单在两处给出了相反的答案。
 * 所以对话页也改从这里派生：排除「对话」自己（当前页），
 * 手机（narrow）按 `desktopOnly` 语义过滤，桌面保留全部。
 *
 * 桌面 12 项；手机管理员 10 项（去掉四六级与工作流）、普通用户 9 项
 * （管理员那 10 项再去掉看板与反馈）。10 项 = 3+3+3+1、9 项 = 3×3，
 * 余 1 时末项通栏、余 0 时正好满行（见 `railLayout`），两种都不留空格子。
 * 渲染在 3 列紧凑格里，见 `.side-entry`。
 */
export function chatRailNav(isAdmin: boolean | undefined, narrow: boolean): NavItem[] {
  return visibleNav(isAdmin).filter((it) => it.href !== "/" && !(narrow && it.desktopOnly));
}

/**
 * 侧栏模块格的排版参数：**3 列，末行不留空格子**。
 *
 * 为什么要算（2026-11-15 用户反馈「普通用户缺两项之后出现空缺」）：
 * 项数除以 3 有余数时，末行会缺角 —— 余 1 时只剩孤零零一个（7 项 = 3+3+1），
 * 余 2 时右边空一格（11 项 = 3+3+3+2，而桌面管理员就是 11 项）。
 * 两种都得补，否则"整齐"只在普通用户那里成立、管理员自己看着还是不齐。
 *
 * 实现上把栅格拆成 6 条轨道：普通格各跨 2 条（= 3 列），余数用它补齐 ——
 *   · 余 1 → 末项跨 6 条（整行通栏，横排）
 *   · 余 2 → 末两项各跨 3 条（各占半行）
 * 为什么不干脆用 4 列填满：侧栏固定 240px，4 列每格 48px，「每日委托」四个字
 * 52px 就开始省略号 —— 为了一个空格子换来两处截断，不值。
 */
export type RailLayout = { cols: number; fill: "none" | "wide" | "pair" };
export function railLayout(count: number): RailLayout {
  const rem = count % 3;
  return { cols: 6, fill: rem === 1 ? "wide" : rem === 2 ? "pair" : "none" };
}

/** 首页是精确匹配；其余按前缀匹配，将来 /knowledge/xxx 也能点亮同一项。 */
export function isCurrentNav(href: string, here: string): boolean {
  return href === "/" ? here === "/" : here === href || here.startsWith(href + "/");
}

/**
 * 左栏的纵向主导航：对话 / 资料库 / 论坛 / 待办（2026-11-22）。
 *
 * 为什么从 12 格宫格改成 5 行：宫格把 12 个入口平均铺开，视觉上没有主次，
 * 而且占掉 246px 高度 —— 会话列表被挤到只剩 96px。
 * 现在只留 4 个高频入口 + 「更多」，其余进浮层。
 */
export const RAIL_PRIMARY_HREFS = ["/", "/knowledge", "/forum", "/reminders"] as const;

export function railPrimary(): NavItem[] {
  const set = new Set<string>(RAIL_PRIMARY_HREFS);
  return NAV.filter((it) => set.has(it.href));
}

/**
 * 「更多」浮层里的项：NAV 里除主入口之外的全部（顺序沿用 NAV）。
 *
 * 沿用 `adminOnly` / `desktopOnly` 语义 —— 普通用户不该看到 /dashboard、/flow，
 * 窄屏（手机抽屉）也不该出现只能在电脑上编辑 JSON 的工作流。
 * 这两个标记是共享清单的既有契约，不要在这里另写一份过滤规则。
 */
export function railMore(isAdmin: boolean | undefined, narrow: boolean): NavItem[] {
  const primary = new Set<string>(RAIL_PRIMARY_HREFS);
  return visibleNav(isAdmin).filter(
    (it) => !primary.has(it.href) && !(narrow && it.desktopOnly),
  );
}
