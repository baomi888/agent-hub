"use client";

// 第三方品牌角标：设置页的「支付宝账号 / 微信账号 / 安全邮箱」标题前各挂一枚。
// 支付宝、微信与登录页 OAuthLoginButtons 里的图标同形（蓝「支」/ 绿双气泡），
// 这里复制一份而不是跨层 import —— 登录组件在 components/ 根，设置组件在
// components/settings/，互相引用会把两个页面打包进同一个 chunk。
// 颜色写死是品牌要求，不随主题变；尺寸跟 card-title 的字号走，不喧宾夺主。

export type Brand = "alipay" | "wechat" | "qqmail";

export default function BrandIcon({ brand, size = 20 }: { brand: Brand; size?: number }) {
  if (brand === "alipay") {
    return (
      <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" style={{ flex: "none" }}>
        <rect x="1" y="1" width="22" height="22" rx="5" fill="#1677FF" />
        <text x="12" y="16.6" textAnchor="middle" fontSize="13" fontWeight="600" fill="#fff">支</text>
      </svg>
    );
  }
  if (brand === "wechat") {
    return (
      <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" style={{ flex: "none" }}>
        <rect x="1" y="1" width="22" height="22" rx="5" fill="#07C160" />
        <path
          d="M9.4 7.3c-2.9 0-5.2 1.7-5.2 3.9 0 1.3.8 2.4 2 3.2l-.5 1.6 1.9-.9c.5.1 1.1.2 1.7.2h.4a3.6 3.6 0 0 1-.2-1.1c0-2.3 2.2-4.1 4.9-4.1h.3c-.6-1.7-2.5-2.8-5.3-2.8z"
          fill="#fff"
        />
        <path
          d="M19.8 13c0-1.8-1.8-3.2-4-3.2s-4 1.4-4 3.2 1.8 3.2 4 3.2c.5 0 1-.1 1.4-.2l1.5.8-.4-1.3c.9-.6 1.5-1.4 1.5-2.5z"
          fill="#fff"
        />
      </svg>
    );
  }
  // QQ 邮箱：企鹅蓝圆底 + 白色信封。不用 QQ 企鹅剪影（表情化太重），
  // 信封才是「邮箱」的通用符号，蓝底负责点出 QQ 品牌。
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" style={{ flex: "none" }}>
      <rect x="1" y="1" width="22" height="22" rx="5" fill="#12B7F5" />
      <rect x="5" y="7" width="14" height="10" rx="2" fill="none" stroke="#fff" strokeWidth="1.6" />
      <path d="M5.6 8.2 12 13l6.4-4.8" fill="none" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
