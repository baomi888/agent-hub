import os from "node:os";
import type { NextConfig } from "next";

// 本机所有非回环 IPv4。
//
// 真机调试的 IP 是会变的：校园网 192.168.10.x、iPhone 热点 172.20.10.x、
// 家里路由 192.168.1.x。手写死某个网段的老办法，每换一次网络就要改代码 + 重启，
// 而且忘改的症状是「手机白屏、页面卡在服务端渲染那一帧」，很难往配置上想。
// 这里改成启动时自己取当前网卡地址，换网络只要重启 dev server 就自动跟上。
function lanAddresses(): string[] {
  const out: string[] = [];
  for (const list of Object.values(os.networkInterfaces())) {
    for (const item of list ?? []) {
      if (item.family === "IPv4" && !item.internal) out.push(item.address);
    }
  }
  return out;
}

const nextConfig: NextConfig = {
  // 独立输出：build 后在 .next/standalone 生成自带精简 node_modules 的服务包，
  // 部署到 2 核 2G 这种小内存机器时无需再 npm install 449MB 全套依赖，
  // 直接 `node .next/standalone/server.js` 即可（详见 DEPLOY 流程）。
  output: "standalone",

  // 旧版校园工具集静态页 → 新版 React 路由。
  // public/tools.html 是 2026-10 的原型单文件（页脚自述「非线上功能」），
  // 新版在 /tools。旧地址可能被收藏或分享过，直接 301 过去，避免用户停在旧页。
  // Next 的 redirects 检查顺序在 filesystem（含 public 静态文件）之前，所以能压住该文件。
  async redirects() {
    return [
      {
        source: "/tools.html",
        destination: "/tools",
        permanent: true,
      },
    ];
  },

  // 把前端 /api/* 请求代理到后端 FastAPI（端口 8000），避免跨域
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8000/api/:path*",
      },
    ];
  },

  // 开发时允许用局域网 IP / 127.0.0.1 访问。
  //
  // 为什么非加不可：Next 16 默认把来自非 localhost 的 dev 资源请求
  // （/_next/hmr 等）当成跨域拦掉，而它拦的时候**不报错、不发请求**——
  // 页面就停在服务端渲染出来的那一帧，所有 useEffect 都不跑。
  // 症状是"登录态一直确认中""按钮点了没反应"，排查方向很容易被带到
  // 组件或 hook 上去（本项目已踩过一次）。手机真机调试也必须走局域网 IP，
  // 所以这里把当前机器所有网卡地址 + 常见私有网段都写进来。
  //
  // 172.16.0.0/12（172.16–172.31）是 iPhone 热点、docker 的常用段，
  // 2026-11-07 就是漏了它导致手机连热点后白屏。
  allowedDevOrigins: [
    "127.0.0.1",
    "localhost",
    "10.*.*.*",
    "172.*.*.*",
    "192.168.*.*",
    ...lanAddresses(),
  ],
};

export default nextConfig;