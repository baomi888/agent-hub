import Link from "next/link";

// 全局 404。
//
// 它不只服务"路径敲错了"这一种情况：**数据看板对非管理员也走到这里**。
// 只有让"无权访问 /dashboard"和"访问一个根本不存在的路径"在视觉、
// HTTP 语义、提示文案上完全一致，才算真的把看板藏住了 ——
// 否则给普通用户看一句"这个页面只有开发者能看"，等于告诉他这里有什么。
export default function NotFound() {
  return (
    <main
      style={{
        minHeight: "100dvh",
        display: "grid",
        placeItems: "center",
        padding: 24,
        background: "var(--bg-canvas)",
        color: "var(--ink)",
      }}
    >
      <div
        className="card"
        style={{
          maxWidth: 420,
          width: "100%",
          padding: "40px 32px",
          textAlign: "center",
          display: "grid",
          gap: 14,
          justifyItems: "center",
        }}
      >
        <div
          style={{
            fontSize: 56,
            fontWeight: 600,
            lineHeight: 1,
            letterSpacing: "-0.03em",
            color: "var(--accent-ink)",
            opacity: 0.55,
          }}
        >
          404
        </div>
        <h1 style={{ fontSize: 20, fontWeight: 600, margin: 0 }}>这条路走不通</h1>
        <p style={{ margin: 0, color: "var(--ink)", opacity: 0.7, lineHeight: 1.7 }}>
          页面不存在，或者它已经不在这儿了。
        </p>
        <Link
          href="/"
          className="btn btn-primary"
          style={{ marginTop: 10 }}
          prefetch={false}
        >
          回工作台
        </Link>
      </div>
    </main>
  );
}
