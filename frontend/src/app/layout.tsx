import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import PwaRegister from "@/components/PwaRegister";
// KaTeX 的字体与排版样式必须在 globals.css 之前：globals 里我们对
// .math-inline / .math-block 的微调要能盖住它的默认值。
import "katex/dist/katex.min.css";
import "./globals.css";

/*
 * 对外称呼统一用「资料库」：侧栏、页面标题、右栏面板、空状态都是这个词。
 * 「知识库」是后端命名（kb_*）和技术术语，不出现在界面里 —— 一个东西两个名字，
 * 用户会以为导航里那个是另一处。
 */
const TITLE = "知穗Agent · 资料库 · 场景智能体 · 校园工具集";

/* iOS 从主屏打开时的启动画面。
   不提供的话那一瞬是纯白 —— 页面本身是米黄纸色，切换时闪一下白，
   观感立刻从"App"掉回"网页"。图片由 tools 里的脚本生成（纸色底 + 居中图标）。
   media 三要素必须同时写全（宽 / 高 / 像素比），少一个就会串到别的机型上：
   只用宽度的话，同宽的 SE 与 12 mini 会互相抢，结果是其中一台被拉伸变形。 */
const SPLASH: { w: number; h: number; dpr: number; file: string }[] = [
  { w: 375, h: 667, dpr: 2, file: "750x1334.png" },
  { w: 375, h: 812, dpr: 3, file: "1125x2436.png" },
  { w: 414, h: 896, dpr: 2, file: "828x1792.png" },
  { w: 414, h: 896, dpr: 3, file: "1242x2688.png" },
  { w: 390, h: 844, dpr: 3, file: "1170x2532.png" },
  { w: 393, h: 852, dpr: 3, file: "1179x2556.png" },
  { w: 428, h: 926, dpr: 3, file: "1284x2778.png" },
  { w: 430, h: 932, dpr: 3, file: "1290x2796.png" },
  { w: 440, h: 956, dpr: 3, file: "1320x2868.png" },
];
// 定位对齐「备考 + 求职」两大主场景（10-14 晚场景撤出后，旧三场景名不再出现在主界面）
const DESC = "为大学生的 AI 素养平台：备考与求职智能体，资料库问答，附 GPA 分析、番茄钟等校园工具集";

export const metadata: Metadata = {
  title: TITLE,
  description: DESC,
  /* 标签页图标：直接用品牌吉祥物，三套并行备份
     - SVG：Chrome / Edge / Firefox 首选，任意尺寸都锐利
     - ICO：app/favicon.ico（多尺寸 16→256），Safari 与必应等搜索爬虫只认位图
     - PNG：Android / PWA 主屏，见 manifest.webmanifest
     圆角在此处烘进位图（22%，抖音/豆包那种 App 观感）：Windows 桌面快捷方式、
     必应搜索结果都不会替我们切圆角，直角会像一张方块贴纸。
     唯一例外是 apple 那张——iOS 会自己套圆角遮罩，给圆角图会变成圆角套圆角，
     所以它保持满铺，透明底也会被填黑，故本地渲染时铺了纸色 #EDE4C8。 */
  icons: {
    icon: [
      { url: "/icon.svg", type: "image/svg+xml" },
      { url: "/icon-192.png", sizes: "192x192", type: "image/png" },
      { url: "/icon-512.png", sizes: "512x512", type: "image/png" },
    ],
    apple: [{ url: "/apple-touch-icon.png", sizes: "180x180", type: "image/png" }],
  },
  manifest: "/manifest.webmanifest",
  /* 桌面/主屏图标下面的名字。不设的话各系统会去用 title 那串长标题，
     桌面快捷方式会被截成「知穗Agent · 大学…」。三处一起给才管用：
     manifest 的 name/short_name、这里的 appleWebApp.title。 */
  appleWebApp: {
    title: "知穗Agent",
    capable: true,
    /* ★ 2026-11-14（iOS 内测）：default → black-translucent。
       default 下 iOS 用浅色状态栏配黑字，而我们的深色模式背景是 #1A1610，
       深纸上写黑字 = 状态栏里什么都看不见。black-translucent 让内容铺到
       状态栏底下、状态栏文字转白，深色下才有对比。
       ★ 代价与前提：改成黑透之后 env(safe-area-inset-top) 从 0 变成 47px，
       所有顶层容器都必须自己让出这一条，否则页头被刘海压住。已经补过的五处：
         .console-scroll（console.css）、.tb-top（forum-tb.css）、
         main.workbench-main（base.css + fx.css 窄屏）、.tour（install-tour.css）、
         .auth-stage（auth.css）；资料库的 .lib-top 本来就有（library.css:1311）。
       新增全屏滚动容器时，必须同样处理 —— 这是这个模式唯一的硬约束。 */
    statusBarStyle: "black-translucent",
  },
  openGraph: {
    title: TITLE,
    description: DESC,
    type: "website",
    locale: "zh_CN",
  },
};

/* 地址栏配色跟随纸色：浅色是 step-0 的米黄，深色是深纸。
   不写的话移动端浏览器顶栏会是一块和系统默认色打架的灰 */
export const viewport: Viewport = {
  /* ★ 2026-11-14（iOS 内测）：viewportFit 是 env(safe-area-inset-*) 生效的**前提**。
     没有 `cover`，iOS 上这些环境变量恒为 0 —— 全站那几十处 safe-area 写法
     （console.css / library.css / announce.css）等于没写，刘海与 Home 指示条会压住内容。
     这一条此前从未在真机独立窗口里验过（与论坛线"未上真机"是同一笔欠账）。 */
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#EDE4C8" },
    { media: "(prefers-color-scheme: dark)", color: "#1A1610" },
  ],
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN" className="h-full antialiased" suppressHydrationWarning>
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var s=localStorage.getItem('theme');var f=new URLSearchParams(location.search).get('theme');var d=f?f==='dark':s?s==='dark':window.matchMedia('(prefers-color-scheme: dark)').matches;if(d)document.documentElement.classList.add('dark');var p={};try{p=JSON.parse(localStorage.getItem('baomi.prefs')||'{}')}catch(e){}if(p.fontScale)document.documentElement.setAttribute('data-fs',p.fontScale);}catch(e){}})();`,
          }}
        />
        {/* Safari 15 及更早只认这个传统标签：缺了它，从主屏打开会退化成带地址栏的 Safari 窗口。
            Next 16 的 appleWebApp.capable 现在只输出新标准 mobile-web-app-capable，
            老 iOS 读不到（线上实测 head 里确实没有这一条），所以手工补一份。 */}
        <meta name="apple-mobile-web-app-capable" content="yes" />
        {/* 中文 Web 字体改为本地子集（frontend/public/fonts/），彻底去掉外链依赖：
            jsdelivr / 飞书字体在国内网络下时通时断，线上实测字体曾被重置两次，
            汇报演示时一旦外链挂掉设计感会直接塌成系统默认字体。
            重新生成见 tools/subset_fonts_project.py。 */}
        <link rel="stylesheet" href="/fonts/fonts.css" />
        {/* iOS 主屏启动画面。必须是 9 个独立 link —— 这个 rel 不支持 srcset，
            每种机型靠 media 精确命中，命中不了就退回系统默认的白屏。 */}
        {SPLASH.map((s) => (
          <link
            key={s.file}
            rel="apple-touch-startup-image"
            media={`(device-width: ${s.w}px) and (device-height: ${s.h}px) and (-webkit-device-pixel-ratio: ${s.dpr})`}
            href={`/splash/${s.file}`}
          />
        ))}
      </head>
      <body className="min-h-full">
        {children}
        <PwaRegister />
        {/* InstallBanner 不在这里：它必须挂在"已登录的首页"里面（app/page.tsx）。
            挂在根布局会先出现在登录门上 —— 详见组件头部注释。 */}
        <div className="grain" aria-hidden="true" />
      </body>
    </html>
  );
}