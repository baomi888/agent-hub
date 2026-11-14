/**
 * 控制台导航清单 —— 桌面侧栏、手机底部 tab、「更多」面板三处共用一份。
 *
 * 为什么单独成文件：手机底部 tab 只放前 4 项，其余进「更多」。
 * 如果各写一份清单，两边迟早漂移（"手机上怎么找不到 X"就是这么来的）。
 * 现在只改这里的顺序，侧栏和底部 tab 一起变。
 */

export interface NavItem {
  href: string;
  /** NavIcon 的名字（单色线性），不是 CornIcon 的插画名 */
  icon: string;
  label: string;
  /** 只有开发者看得到（后端仍会二次校验，这里只是不给入口） */
  adminOnly?: boolean;
}

export const NAV: NavItem[] = [
  { href: "/", icon: "chat", label: "对话" },
  // 知识库是**独立页面**（2026-10-30）。原先它指向 /?kb=1，和「对话」落到同一个路由，
  // 只是首页右栏抽屉被拉开 —— 用户的原话是"点击对话和点击知识库都是一样的跳转"。
  { href: "/knowledge", icon: "book", label: "知识库" },
  // 论坛中心（2026-11-14）：公开知识库里的讨论汇到一处。
  // 紧跟「知识库」是因为它们是一对 —— 一个是资料、一个是关于资料的讨论；
  // 放到「总结」「打卡」后面会让人以为它是个统计页。
  // ★ 标签只能两个字：「论坛中心」在手机 3 列网格里会挤成两行、把导航撑高
  //   （和「校园工具集」→「工具集」同一个坑）。完整名字在页面标题里。
  { href: "/forum", icon: "forum", label: "论坛" },
  // 待办中心（独立模块，2026-11-09）。固定在第 3 位：它回答的是「什么时候做什么」，
  // 比「每日委托」（生成什么）更靠前。★ 标签只能两个字：写「待办中心」会在手机
  // 3 列网格里挤成两行、把整块导航撑高 —— 和当年「校园工具集」砍成「工具集」同一个坑。
  // 2026-11-10 晚改名：原先叫「提醒」，但页面里的主体是待办，提醒只是它的一部分
  // （「提示什么时候叫我」）。一个词在侧边栏、页标题、tab 三层各指一件事，
  // 连作者自己都说不清区别 —— 统一成「待办」，提醒退回到属性位置。
  { href: "/reminders", icon: "task", label: "待办" },
  { href: "/delegate", icon: "clock", label: "每日委托" },
  { href: "/report", icon: "summary", label: "总结" },
  { href: "/checkin", icon: "star", label: "打卡" },
  // 四六级备考工作区：计划 / 词汇本 / 模考分数（申报书 B3 的专项模块入口）
  { href: "/cet", icon: "globe", label: "四六级" },
  // 关卡式学习路径：12 关由真实学习数据判定（申报书 3.4 / 2.8）
  { href: "/path", icon: "path", label: "学习路径" },
  // 工具集是**独立单页**（GPA / 番茄钟 / 课程表 / 单词卡 / 倒计时 / 抽签），
  // 不进控制台外壳，也不要求登录。从这里点出去就是换页，左导航仍留在原处；
  // 之前的入口是首页下方那一行，用户反馈"找不到"，所以在这里给一条固定入口。
  // ★ 标签只写「工具集」：写成「校园工具集」时手机上（3 列网格）挤成两行，
  //   把整块导航撑高；完整名字留在那一页自己的标题和落地页里。
  { href: "/tools", icon: "tools", label: "工具集" },
  // 混合架构：工作流定义 + 切换规则。**这是开发者调试面，不是用户功能**
  // —— 界面上摊着 UUID、任务 id、日志入口和可编辑的 JSON 定义。
  // 2026-10-27 内测反馈："这个为啥要给用户看"。与 /dashboard 同一性质，因此同样加 adminOnly。
  // 注意：这只是不给误点的机会，不是安全边界（真隐藏需要后端也返 404，本期没做）。
  { href: "/flow", icon: "flow", label: "工作流", adminOnly: true },
  // 数据看板：开发者看"有多少人、在调什么接口、反馈堆了多少"。
  // 只在 is_admin 时渲染入口 —— 但**真正的权限在后端**（非管理员返回 404，见 api/admin.py）。
  { href: "/dashboard", icon: "dashboard", label: "看板", adminOnly: true },
  { href: "/settings", icon: "settings", label: "设置" },
];

/** 手机底部 tab 占几格（不含「更多」）。改这个数就同时改了底部 tab 和「更多」的内容。 */
export const TAB_PRIMARY_COUNT = 4;

export function visibleNav(isAdmin: boolean | undefined): NavItem[] {
  return NAV.filter((it) => !it.adminOnly || isAdmin);
}

/** 首页是精确匹配；其余按前缀匹配，将来 /knowledge/xxx 也能点亮同一项。 */
export function isCurrentNav(href: string, here: string): boolean {
  return href === "/" ? here === "/" : here === href || here.startsWith(href + "/");
}
