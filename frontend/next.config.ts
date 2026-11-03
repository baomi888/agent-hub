import type { NextConfig } from "next";

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
};

export default nextConfig;