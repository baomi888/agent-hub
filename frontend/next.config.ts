import type { NextConfig } from "next";

const nextConfig: NextConfig = {
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