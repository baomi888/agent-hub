import type { Metadata, Viewport } from "next";
import "./globals.css";

const TITLE = "知穗Agent · 知识库 · 场景智能体 · 校园工具集";
// 定位对齐「备考 + 求职」两大主场景（10-14 晚场景撤出后，旧三场景名不再出现在主界面）
const DESC = "为大学生的 AI 素养平台：备考与求职智能体，知识库问答，附 GPA 分析、番茄钟等校园工具集";

export const metadata: Metadata = {
  title: TITLE,
  description: DESC,
  // 标签页图标：漫画风知穗吉祥物（矢量 SVG，缩放不糊）
  icons: {
    icon: [{ url: "/mascot.svg", type: "image/svg+xml" }],
    apple: "/mascot.svg",
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
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#EDE4C8" },
    { media: "(prefers-color-scheme: dark)", color: "#1A1610" },
  ],
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="zh-CN" className="h-full antialiased" suppressHydrationWarning>
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var s=localStorage.getItem('theme');var f=new URLSearchParams(location.search).get('theme');var d=f?f==='dark':s?s==='dark':window.matchMedia('(prefers-color-scheme: dark)').matches;if(d)document.documentElement.classList.add('dark');var p={};try{p=JSON.parse(localStorage.getItem('baomi.prefs')||'{}')}catch(e){}if(p.fontScale)document.documentElement.setAttribute('data-fs',p.fontScale);}catch(e){}})();`,
          }}
        />
        {/* 中文 Web 字体改为本地子集（frontend/public/fonts/），彻底去掉外链依赖：
            jsdelivr / 飞书字体在国内网络下时通时断，线上实测字体曾被重置两次，
            汇报演示时一旦外链挂掉设计感会直接塌成系统默认字体。
            重新生成见 tools/subset_fonts_project.py。 */}
        <link rel="stylesheet" href="/fonts/fonts.css" />
      </head>
      <body className="min-h-full">
        {children}
        <div className="grain" aria-hidden="true" />
      </body>
    </html>
  );
}