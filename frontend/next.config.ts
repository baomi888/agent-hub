import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 独立输出：build 后在 .next/standalone 生成自带精简 node_modules 的服务包，
  // 部署到 2 核 2G 这种小内存机器时无需再 npm install 449MB 全套依赖，
  // 直接 `node .next/standalone/server.js` 即可（详见 DEPLOY 流程）。
  output: "standalone",

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