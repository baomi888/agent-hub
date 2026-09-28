import type { Metadata, Viewport } from "next";
import "./globals.css";

const TITLE = "苞米Agent · 知识库 · 多对话 · 智能体";
const DESC = "支持知识库检索、联网搜索、计算、天气的 AI 智能体";

export const metadata: Metadata = {
  title: TITLE,
  description: DESC,
  // 标签页图标：用 public 里唯一那份 corn-logo.webp（32KB），不再额外放图
  icons: {
    icon: [{ url: "/corn-logo.webp", type: "image/webp" }],
    apple: "/corn-logo.webp",
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