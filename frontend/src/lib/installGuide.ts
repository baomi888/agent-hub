/**
 * 「装到桌面」的三端分步教程数据（/install 独立页面用）。
 *
 * ★ 措辞按各系统**自己显示的字**写，不要意译：
 *   - iOS 底部那个是「⋯」，菜单里叫「共享」（用户真机实测，不是「分享」）
 *   - iOS 要先进「查看更多」，展开后的动作列表里才有「添加到主屏幕」，确认键叫「添加」
 *   - 安卓 Chrome 叫「安装应用」，部分机型叫「添加到主屏幕」
 *   - Edge 的菜单里是「应用 → 安装 知穗Agent」，装完弹「已安装应用」，按钮叫「允许」
 *   用户是照着屏幕找按钮的，写错一个字就等于没写。
 *
 * 图有两种：
 *   - shot：真机/录屏裁出来的实拍图，金框已经是图的一部分（见 .workbuddy/pp/make_shots.py）
 *   - draw：还没有真机素材时用示意图顶上（目前只有安卓）
 *   两种都带原始宽高，页面用 aspect-ratio 撑住，避免加载时版式跳动。
 */

import type { Platform } from "./pwa";

/** 示意图的键（只在没有真机图时兜底） */
export type FigKey = "android-menu" | "android-install" | "android-home";

export type Figure =
  /** 实拍图：w/h 是裁剪后的原始像素，用来算长宽比 */
  | { shot: string; w: number; h: number; alt: string }
  /** 示意图：draw 指向 components/install/figs.tsx 里的组件 */
  | { draw: FigKey; w: number; h: number; alt: string };

export interface GuideStep {
  /** 这一步做什么（动词开头，一句话） */
  title: string;
  /** 补充说明：找不到时怎么办、和别的叫法怎么对应 */
  desc: string;
  fig: Figure;
  /** 图注：图里那个金色框圈的是什么 */
  caption: string;
}

export interface InstallGuide {
  /** 平台名，显示在 tab 和抬头 */
  label: string;
  /** 一句话概括这套流程走几步 */
  title: string;
  /** 动手前的前提（有些平台有硬前提，比如必须用 Safari） */
  prereq?: string;
  steps: GuideStep[];
  /** 装完是什么样、有什么好处，放在最后一步之后 */
  note: string;
}

const IOS: InstallGuide = {
  label: "iPhone / iPad",
  title: "在 Safari 里加到主屏幕",
  prereq:
    "前提：用 Safari 打开 myzhisui.cn。微信、QQ、钉钉里点开的是内嵌网页，底部这排按钮根本没有，照着点也找不到——先点右上角「···」，选「在浏览器中打开」。",
  steps: [
    {
      title: "点底部最右边的「⋯」",
      desc: "Safari 的底部工具条：左边是后退，中间是网址，右边是刷新和这个「⋯」。它的正式名字叫「更多」，装到桌面就藏在里面。",
      fig: {
        shot: "/install/ios-1-toolbar.webp",
        w: 883,
        h: 212,
        alt: "Safari 底部工具条，最右边的三个点按钮被金色方框圈出",
      },
      caption: "就是这个圆圈里三个点",
    },
    {
      title: "点「共享」",
      desc: "弹出的白色面板第一项就是「共享」——它就是分享面板的入口，苹果把它翻译成了共享。",
      fig: {
        shot: "/install/ios-2-share.webp",
        w: 517,
        h: 640,
        alt: "Safari 的「⋯」菜单，第一项「共享」被金色方框圈出",
      },
      caption: "第一项「共享」，别点成「添加到书签」",
    },
    {
      title: "在面板底部点「查看更多」",
      desc: "共享面板下半部分有一排圆按钮：拷贝、添加到「书签」、添加到阅读列表、查看更多。「查看更多」是最后那个向下的箭头，点开才是完整的动作列表。有的系统版本不用点，往下滑就能看到。",
      fig: {
        shot: "/install/ios-3-more.webp",
        w: 883,
        h: 520,
        alt: "共享面板底部一排圆按钮，最右侧「查看更多」被金色方框圈出",
      },
      caption: "最右边这个向下箭头",
    },
    {
      title: "点「添加到主屏幕」",
      desc: "展开后的列表里往下找这一项。同一屏还有「标记」「打印」「添加到个人收藏」，都不是。",
      fig: {
        shot: "/install/ios-4-home.webp",
        w: 840,
        h: 460,
        alt: "iOS 动作列表，「添加到主屏幕」被金色方框圈出",
      },
      caption: "列表靠下这一项：一个方框里加号",
    },
    {
      title: "右上角点「添加」",
      desc: "名字默认就是「知穗 Agent」，不用改。「作为网页 App 打开」保持打开状态，这样从桌面点开是全屏的，没有地址栏。",
      fig: {
        shot: "/install/ios-5-add.webp",
        w: 900,
        h: 750,
        alt: "添加到主屏幕面板，右上角蓝色「添加」按钮被金色方框圈出",
      },
      caption: "点这个蓝色「添加」",
    },
  ],
  note: "苹果没开放一键安装的接口，只能手动加这么一次。加完以后从桌面图标打开是全屏的、没有地址栏，和装了个 App 一样。",
};

const ANDROID: InstallGuide = {
  label: "安卓手机",
  title: "在 Chrome 里装到桌面",
  prereq:
    "用 Chrome 打开 myzhisui.cn。夸克、UC、小米浏览器大多也有这一项，只是位置不同；实在找不到，换成 Chrome 一定有。",
  steps: [
    {
      title: "点右上角的三个点",
      desc: "Chrome 的菜单在地址栏右边，三个竖着排的点。国产浏览器多半在右下角，换成「≡」或「···」，菜单项是一样的。",
      fig: {
        draw: "android-menu",
        w: 360,
        h: 300,
        alt: "Chrome 右上角菜单展开，其中「安装应用」一项被金色虚线圈出（示意图）",
      },
      caption: "菜单里找带下载箭头的「安装应用」",
    },
    {
      title: "点「安装应用」",
      desc: "部分机型这一项写的是「添加到主屏幕」，意思完全一样。点了之后底部会弹出一条确认。",
      fig: {
        draw: "android-install",
        w: 360,
        h: 200,
        alt: "底部确认条，「安装」按钮被金色虚线圈出（示意图）",
      },
      caption: "点「安装」，不是「取消」",
    },
    {
      title: "确认，桌面就有图标了",
      desc: "装好后图标长这样：米黄底、玉米小人，名字是「知穗 Agent」。找不到就先翻桌面最后一屏，再看应用抽屉（上滑出来的那一屏）。",
      fig: {
        draw: "android-home",
        w: 360,
        h: 260,
        alt: "手机桌面上的知穗 Agent 图标（示意图）",
      },
      caption: "米黄底、玉米小人",
    },
  ],
  note: "安卓装出来是真正的 WebAPK（部分机型是快捷方式）。从图标打开没有地址栏；断网时会显示一个提示页，不会白屏。",
};

const DESKTOP: InstallGuide = {
  label: "电脑",
  title: "在电脑上装成独立窗口",
  prereq:
    "用 Chrome 或 Edge 打开 myzhisui.cn（这两个浏览器都支持）。下面演示的是 Edge，Chrome 的菜单叫法略有不同，每一步都写了两边的说法。",
  steps: [
    {
      title: "点地址栏右端的「安装」图标",
      desc: "地址栏最右边会出现一个小图标，点开就是这个卡片。它不会一直挂着：关掉页面隔一阵再开才会重新出现。",
      fig: {
        shot: "/install/win-1-install.webp",
        w: 500,
        h: 330,
        alt: "Edge 地址栏弹出的「安装 知穗Agent 应用」卡片，「安装」按钮被金色方框圈出",
      },
      caption: "点「安装」，不是「以后再说」",
    },
    {
      title: "没看到图标，就用菜单装",
      desc: "Edge：右上角「···」→「应用」→「安装 知穗Agent」。Chrome：右上角「⋮」→「保存并分享」→「安装页面为应用」。",
      fig: {
        shot: "/install/win-2-menu.webp",
        w: 750,
        h: 342,
        alt: "Edge 的「···」菜单里「应用」被圈出，右侧子菜单里「安装 知穗Agent」被圈出",
      },
      caption: "先「应用」，再点右边弹出的「安装 知穗Agent」",
    },
    {
      title: "勾上桌面快捷方式，点「允许」",
      desc: "装好后浏览器弹「已安装应用」。想让它出现在桌面就勾「创建桌面快捷方式」，想固定到任务栏就勾第一项，然后点「允许」。",
      fig: {
        shot: "/install/win-3-allow.webp",
        w: 360,
        h: 550,
        alt: "Edge 的「已安装应用」对话框，底部「允许」按钮被金色方框圈出",
      },
      caption: "勾好选项，点这个「允许」",
    },
  ],
  note: "电脑上装的是同一个网站的独立窗口：没有地址栏、没有标签页，开始菜单也能搜到「知穗 Agent」。数据与浏览器里完全互通，登录一次就够。",
};

// 认不出的浏览器：给最通用的一套，避免"点开教程是空的"
const GENERIC: InstallGuide = {
  label: "其他浏览器",
  title: "在你的浏览器里装到桌面",
  prereq: "不同浏览器菜单名字不一样，但都在「工具」「更多」或主菜单里。找不到就换 Chrome 或 Edge 打开同一个网址。",
  steps: [
    {
      title: "打开浏览器菜单",
      desc: "一般是右上角或右下角的三个点 / 三条横线。找「安装应用」「添加到主屏幕」「安装页面为应用」这类字样。",
      fig: {
        draw: "android-menu",
        w: 360,
        h: 300,
        alt: "浏览器菜单展开，其中安装相关的一项被金色虚线圈出（示意图）",
      },
      caption: "菜单里找带下载箭头的字样",
    },
    {
      title: "点确认，桌面就有图标了",
      desc: "装好后从图标打开是全屏的、没有地址栏。找不到图标就翻桌面最后一屏或应用列表。",
      fig: {
        draw: "android-home",
        w: 360,
        h: 260,
        alt: "桌面上的知穗 Agent 图标（示意图）",
      },
      caption: "米黄底、玉米小人",
    },
  ],
  note: "菜单里完全没有安装相关的项，说明这个浏览器不支持，换 Chrome 或 Edge 打开同一个网址即可。",
};

export function installGuide(platform: Platform): InstallGuide {
  switch (platform) {
    case "ios":
      return IOS;
    case "android":
      return ANDROID;
    case "desktop":
      return DESKTOP;
    default:
      return GENERIC;
  }
}

/** tab 的顺序：手机两头在前，电脑最后（手机上刷到这页的人最多） */
export const GUIDE_ORDER: Platform[] = ["ios", "android", "desktop"];

/** 常见问题：三端共用，按"最可能卡住的地方"排序 */
export const FAQ: { q: string; a: string }[] = [
  {
    q: "照做完，菜单里没有那一项",
    a: "八成是在微信或 QQ 里点开的链接，那种内嵌浏览器不提供安装。点右上角「···」→「在浏览器中打开」，换成 Safari 或 Chrome 再试。还是找不到就换 Chrome（安卓）或 Chrome / Edge（电脑）。",
  },
  {
    q: "装完打开还带地址栏",
    a: "说明你点的是「添加到书签」或「创建快捷方式」，不是「添加到主屏幕 / 安装应用」。把那个图标删掉，回到浏览器重做一遍，认准金色框圈住的那一项。",
  },
  {
    q: "要重新注册、重新登录吗",
    a: "不用。桌面图标打开的就是同一个网站，登录状态互通，一次登录两处都算。电脑和手机是两个设备，各登一次。",
  },
  {
    q: "以后怎么更新",
    a: "不用管。图标打开的就是线上最新版，改了什么立刻生效，不存在「版本太旧要升级」。",
  },
  {
    q: "占内存吗",
    a: "几乎不占。装进去的只是一个图标和一份外壳，页面内容还是联网取的。断网时会显示一个提示页，不会白屏。",
  },
];
