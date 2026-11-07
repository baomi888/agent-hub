/**
 * 「设置提醒」的分步教程（/reminder 与 /settings/reminder 共用）。
 *
 * 和 installGuide 分开写、不合并：两边讲的是完全不同的动作，
 * 合并只会让"哪一步属于哪个教程"变得要靠 if 猜。
 *
 * ## 为什么三端只推一条主路
 *
 * 推送在国内是分平台可用的：
 *   · iPhone 装到主屏 —— 走苹果 APNs，能收
 *   · Windows Edge  —— 走微软 WNS，能收
 *   · 安卓所有浏览器 —— 底层一律 Google FCM，国内连不通，换浏览器无效
 * 而日历订阅三端都能用（靠系统日历自己的定时器，不吃网络）。
 * 所以：iPhone / 安卓主推日历，电脑主推推送；iPhone 再补一句"想收系统通知怎么办"。
 * 这一条判断写在这里，不要在前端组件里再判一次平台。
 *
 * ## 图
 *   · shot：真机截图裁出来的实拍图（iPhone 那四张是用户自己操作时截的）
 *   · draw：还没有真机素材时按系统控件位置画的示意图（目前只有安卓）
 *   两种都带原始宽高，页面用 aspect-ratio 撑住，避免加载时版式跳动。
 */

import type { Platform } from "./pwa";

/** 示意图的键（只在没有真机图时兜底） */
export type RemindFigKey = "android-subscribe" | "android-url-dialog";

export type RemindFigure =
  | { shot: string; w: number; h: number; alt: string }
  | { draw: RemindFigKey; w: number; h: number; alt: string };

export interface RemindStep {
  /** 这一步做什么（动词开头，一句话） */
  title: string;
  /** 补充说明：找不到时怎么办、和别的叫法怎么对应 */
  desc: string;
  /** 没有合适的图就省略，页面只渲染文字 */
  fig?: RemindFigure;
  /** 图注：图里那个金色框圈的是什么 */
  caption?: string;
}

export interface RemindGuide {
  label: string;
  title: string;
  /** 动手前的前提 */
  prereq?: string;
  steps: RemindStep[];
  /** 做完之后是什么效果 */
  note: string;
  /** 这个平台的另一条路（目前只有 iPhone 有：日历之外还能收系统通知） */
  alt?: { title: string; body: string };
}

/** 设置页那张「日历提醒」卡：第一张图在三端都要用，抽出来免得三处写法漂移 */
const FIG_SETTINGS_CAL: RemindFigure = {
  shot: "/remind/settings-cal.webp",
  w: 688,
  h: 455,
  alt: "设置页的「日历提醒」卡片，「复制订阅链接」按钮被金色方框圈出",
};

const IOS: RemindGuide = {
  label: "iPhone / iPad",
  title: "在自己日历里加一条订阅",
  prereq:
    "iPhone 上加一次订阅，之后每天到点由「系统日历」提醒你。它不依赖浏览器，Safari 关掉、手机锁屏都照响。全程 2 分钟，不用装任何东西。",
  steps: [
    {
      title: "在设置里点「复制订阅链接」",
      desc: "登录后进「设置」→ 往下找到「提醒」，最下面那张「日历提醒」，点左边的「复制订阅链接」。这条链接是你专属的，别公开发出去——拿到它的人能拉到你的提醒日历内容。",
      fig: FIG_SETTINGS_CAL,
      caption: "点这个「复制订阅链接」",
    },
    {
      title: "打开日历，选「添加订阅日历」",
      desc: "打开系统「日历」App → 底部中间的「日历」→ 左下角「添加日历」→「添加订阅日历」。然后把刚复制的链接粘到「订阅 URL」这一栏，下面的「查找」会自动开始。",
      fig: {
        shot: "/remind/ios-1-paste.webp",
        w: 1206,
        h: 600,
        alt: "iPhone 的「添加订阅日历」页面，「订阅 URL」输入框里已经粘好链接",
      },
      caption: "粘进「订阅 URL」这一栏",
    },
    {
      title: "等一两秒，点「完成」",
      desc: "「查找」转完，页面会显示日历名「知穗」，说明链接读到了。点右上角「完成」保存。如果一直转圈或报错，多半是链接没复制全——回设置里重新复制一次。",
    },
    {
      title: "日历列表里多出一项，就成功了",
      desc: "回到日历列表，能看到「知穗 · 每日提醒」，底下写着「已订阅」。这一项在，提醒就已经生效了，不用再动它。",
      fig: {
        shot: "/remind/ios-2-list.webp",
        w: 1206,
        h: 715,
        alt: "iPhone 日历列表里多出一项「知穗 · 每日提醒」，下方标注「已订阅」",
      },
      caption: "认这个黄色圆圈 + 「已订阅」",
    },
    {
      title: "到点那天，日程里能看到它",
      desc: "每天你设的那个时刻（默认 20:00），日历里会出现一条 10 分钟长的事件「知穗 · 看看今天的事」。手机到点会推一条日历提醒，点开就是你今天的摘要。",
      fig: {
        shot: "/remind/ios-3-event.webp",
        w: 1206,
        h: 640,
        alt: "iPhone 日历的时间轴，20:00 处有一条黄色事件「知穗 · 看看今天的事」",
      },
      caption: "20:00 这条黄色横条",
    },
  ],
  note: "加完之后，你在设置里改提醒时间，日历会跟着变——但「多久同步一次不由我们定」（苹果自己排期，可能几小时）。改完不用急着验证，第二天看就对了。",
  alt: {
    title: "也想收系统通知（点一下直接进站内）？",
    body: "可以，但要先把这个站「添加到主屏」，系统才允许它发通知：按「装到桌面」把知穗加到主屏 → 从主屏图标打开（不是 Safari）→ 设置 → 「每日提醒」→ 打开提醒 → 允许通知。装到主屏之后两条通道能同时开着，互不影响。",
  },
};

const ANDROID: RemindGuide = {
  label: "安卓手机",
  title: "在系统日历里加订阅",
  prereq:
    "安卓上「不要等浏览器通知」：国内安卓的网页推送底层是 Google 的服务，连不通，换 Chrome、Edge、三星浏览器都一样。用日历订阅，走手机自己的定时器，断网也响。",
  steps: [
    {
      title: "在设置里点「复制订阅链接」",
      desc: "登录后进「设置」→「提醒」→ 最下面那张「日历提醒」，点「复制订阅链接」。也可以点旁边的「下载日历文件」，把 .ics 存到手机再导入——但那样以后改时间不会自动同步，推荐用复制链接。",
      fig: FIG_SETTINGS_CAL,
      caption: "点这个「复制订阅链接」",
    },
    {
      title: "在日历里找「订阅日历」入口",
      desc: "各家叫法不一样，认「订阅」两个字：小米 / Redmi 是右上「⋮」→「订阅日历」；华为 / 荣耀是右下「+」→「订阅日历」；三星是左上「≡」→「添加日历」→「从 URL 添加」；OPPO / vivo 在右上「⋮」→「设置」→「订阅」；Google 日历是左上「≡」→「设置」→「添加日历」→「通过网址」。",
      fig: {
        draw: "android-subscribe",
        w: 360,
        h: 300,
        alt: "安卓日历右上角菜单展开，「订阅日历」一项被金色方框圈出（示意图）",
      },
      caption: "菜单里找带「订阅」字样的那一项",
    },
    {
      title: "粘上链接，点「添加」",
      desc: "弹出的对话框里把链接粘进去，点「添加」或「订阅」。加成功后日历列表里会多出一项「知穗 · 每日提醒」。",
      fig: {
        draw: "android-url-dialog",
        w: 360,
        h: 240,
        alt: "订阅日历对话框，URL 已填好，底部「添加」按钮被金色方框圈出（示意图）",
      },
      caption: "认「添加 / 订阅」这个按钮",
    },
  ],
  note: "到点由系统日历响，和闹钟一样，不需要联网。有些机型第一次订阅后要等一次刷新才出现在列表里，下拉一下日历或者重启一次日历 App 就好。",
};

const DESKTOP: RemindGuide = {
  label: "电脑",
  title: "在设置里打开每日提醒",
  prereq:
    "电脑上用 Edge 或 Chrome 打开 myzhisui.cn。「每日提醒」走的是浏览器推送：Edge 走微软的通道，国内能通；Chrome 在国内常常连不上，打不开就换 Edge。",
  steps: [
    {
      title: "设置 →「每日提醒」→ 打开提醒",
      desc: "登录后进「设置」，找到「提醒」这一组，第二张卡「每日提醒」，点右边的「打开提醒」。",
      fig: {
        shot: "/remind/settings-push.webp",
        w: 688,
        h: 271,
        alt: "设置页的「每日提醒」卡片，「打开提醒」按钮被金色方框圈出",
      },
      caption: "点这个「打开提醒」",
    },
    {
      title: "允许通知，然后发一条试试",
      desc: "浏览器左上角会弹一个授权框，选「允许」。授权后卡片上会多出「发一条测试」，点它——几秒内应该在屏幕右下角看到一条通知。没看到先检查系统设置里的「通知和操作」有没有把 Edge 关掉，或者「专注助手」是不是开着。",
    },
  ],
  note: "电脑和手机是两个设备，各开各的，互不影响。电脑上关掉提醒不会影响手机日历。",
};

/** 认不出的浏览器：把人往最通用的那条路上带 */
const GENERIC: RemindGuide = {
  label: "其他浏览器",
  title: "用日历订阅最稳",
  prereq: "不同浏览器对推送的支持差别很大，日历订阅不挑浏览器——换哪个都能用。",
  steps: [
    {
      title: "在设置里复制订阅链接",
      desc: "登录后进「设置」→「提醒」→「日历提醒」→「复制订阅链接」。",
      fig: FIG_SETTINGS_CAL,
      caption: "点这个「复制订阅链接」",
    },
    {
      title: "加到你的系统日历里",
      desc: "电脑上用 Outlook、macOS 日历、Thunderbird 都能加订阅：找「订阅日历 / 通过网址添加 / Subscribe from web」这类入口，把链接粘进去即可。手机上的做法看「iPhone」或「安卓」那两个页签。",
    },
  ],
  note: "加完后每天到点由系统日历提醒，和用哪个浏览器无关。",
};

export function reminderGuide(platform: Platform): RemindGuide {
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

/** tab 顺序：手机两头在前，电脑最后 */
export const GUIDE_ORDER: Platform[] = ["ios", "android", "desktop"];

/** 常见问题：按"最可能卡住的地方"排序 */
export const FAQ: { q: string; a: string }[] = [
  {
    q: "加完订阅，日历里没看到「知穗 · 每日提醒」",
    a: "先确认链接是完整的——复制时少了尾巴就会「查找」失败。iPhone 上要选「添加订阅日历」，不是「新建日历」；安卓上要找带「订阅 / 通过网址」的入口，不是普通的「添加日程」。加成功后它出现在日历「列表」里，不是某一天的日程里。",
  },
  {
    q: "改了提醒时间，日历怎么还是老时间",
    a: "分两种：「订阅链接」加的，服务端下次被拉取时会变成新时间，但多久拉一次不由我们定（苹果自己排期，可能几小时），改完第二天再看；「下载 .ics 导入」的是一次性快照，永远不会自己更新，必须在日历里删掉那条、回设置重新下载导入。",
  },
  {
    q: "为什么每天只有一条，不告诉我具体哪个任务没做完",
    a: "故意的。日历事件的内容是「恒定」的，因为客户端什么时候去拉一次我们控制不了——如果把「还剩 3 个任务」写进日历，第二天任务变了它还是 3，等于在说假话。点开提醒进站内，看到的才是当时的真实情况。",
  },
  {
    q: "iPhone 能收系统通知吗，不用日历",
    a: "能，但要满足两个条件：iOS 16.4 以上，并且先把知穗「添加到主屏」、从主屏图标打开。在 Safari 标签页里打开是没有的，系统不给。装好之后在设置里点「每日提醒」→「打开提醒」→ 允许通知。",
  },
  {
    q: "安卓为什么收不到浏览器通知",
    a: "不是我们没做，是国内安卓的网页推送底层统一走 Google FCM（fcm.googleapis.com），这个域名在大陆连不通。换 Chrome、Edge、三星浏览器都一样，因为它们都用系统 FCM。所以安卓只推日历这条路——它靠手机自己的定时器，反而更可靠。",
  },
  {
    q: "不想用了怎么取消",
    a: "在日历里把「知穗 · 每日提醒」这个订阅删掉就行，服务端不用管。想换一条新链接（比如旧链接不小心发出去了），在设置里点「换一个链接」，旧链接会立刻失效。",
  },
];
