import type { Metadata, Viewport } from "next";
import "./globals.css";

const TITLE = "苞米Agent · 知识库 · 场景智能体 · 校园工具集";
const DESC = "为大学生的 AI 素养平台：考研复习/课程笔记/论文写作场景智能体，知识库问答，附 GPA 分析、番茄钟等校园工具集";

export const metadata: Metadata = {
  title: TITLE,
  description: DESC,
  // 标签页图标：漫画风苞米吉祥物（矢量 SVG，缩放不糊）
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
        <link rel="preconnect" href="https://miaoda.feishu.cn" />
        <link
          rel="stylesheet"
          href="https://cdn.jsdelivr.net/npm/lxgw-wenkai-screen-webfont@1.7.0/style.css"
        />
        <link
          rel="stylesheet"
          href="https://miaoda.feishu.cn/fonts/css2?family=Noto+Serif+SC:wght@400;600;700;900&family=Noto+Sans+SC:wght@300;400;500;700&display=swap"
        />
      </head>
      <body className="min-h-full">
        {children}
        <div className="grain" aria-hidden="true" />
      </body>
    </html>
  );
}