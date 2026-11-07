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

  // 开发时允许用局域网 IP / 127.0.0.1 访问。
  //
  // 为什么非加不可：Next 16 默认把来自非 localhost 的 dev 资源请求
  // （/_next/hmr 等）当成跨域拦掉，而它拦的时候**不报错、不发请求**——
  // 页面就停在服务端渲染出来的那一帧，所有 useEffect 都不跑。
  // 症状是"登录态一直确认中""按钮点了没反应"，排查方向很容易被带到
  // 组件或 hook 上去（本项目已踩过一次）。手机真机调试也必须走局域网 IP，
  // 所以这里把常见的几种写进来。
  allowedDevOrigins: ["127.0.0.1", "localhost", "192.168.*.*", "10.*.*.*"],
};

export default nextConfig;