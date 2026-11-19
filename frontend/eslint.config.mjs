import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // 历史设计稿备份（旧版 Sidebar / page 快照），不参与路由、无任何 import 指向它。
    // 死代码不进 lint；真要删目录走单独清理，别让它常年贡献假诊断。
    "src/_backup/**",
    // 历次 mv .next 留下的构建备份残留（约 1.3GB），同理只是噪声。
    ".next-bak-*/**",
  ]),
]);

export default eslintConfig;
