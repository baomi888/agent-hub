"use client";

// 登录门：未登录时挡住整个工作台。
//
// 为什么要一整页而不是弹窗：工作台里所有数据（会话、知识库、用量）都是
// 按用户隔离的，弹一个"请登录"的小框、背后还留着别人的会话列表，等于把
// 隔离做了一半。整页挡住最干净，也顺带避免了首屏先渲染空列表再跳走的闪烁。
import { useState } from "react";
import MascotLogo from "./chat/MascotLogo";

interface Props {
  allowSignup: boolean;
  /** 首屏探活中：这段先不渲染表单，免得用户填一半被告知"其实你已登录" */
  checking?: boolean;
  onLogin: (username: string, password: string) => Promise<void>;
  onRegister: (username: string, password: string) => Promise<void>;
}

export default function AuthGate({ allowSignup, checking, onLogin, onRegister }: Props) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // 注册被关闭时（ALLOW_SIGNUP=0）强制退回登录态。
  // 用派生值而不是 useEffect + setState：后者是"在 effect 里同步改状态"，
  // 会被 React 编译器判成级联渲染。
  const currentMode: "login" | "register" = allowSignup ? mode : "login";

  const submit = async () => {
    if (busy) return;
    const u = username.trim();
    if (!u || !password) {
      setError("请填写用户名和密码");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (currentMode === "login") await onLogin(u, password);
      else await onRegister(u, password);
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败，请重试");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-dvh items-center justify-center bg-canvas px-5 py-10">
      <div
        className="w-full max-w-[420px] rounded-lg border border-line bg-card-bg p-7"
        style={{ boxShadow: "var(--shadow-2)" }}
      >
        {/* 品牌区：与侧栏同一套（玉米穗 + 衬线标题） */}
        <div className="flex flex-col items-center gap-2 text-center">
          <MascotLogo className="h-12 w-12 object-contain" />
          <h1 className="font-serif text-fs-title font-bold leading-tight text-ink">苞米Agent</h1>
          <p className="brand-sub">登录后使用你自己的会话与知识库</p>
        </div>

        {allowSignup && (
          <div className="mt-6 flex gap-1 rounded-sm bg-fill-3 p-1" role="tablist" aria-label="登录或注册">
            {(
              [
                { v: "login", label: "登录" },
                { v: "register", label: "注册" },
              ] as const
            ).map((t) => (
              <button
                key={t.v}
                role="tab"
                aria-selected={currentMode === t.v}
                onClick={() => {
                  setMode(t.v);
                  setError(null);
                }}
                className={`flex-1 rounded-xs py-1.5 text-fs-md transition ${
                  currentMode === t.v
                    ? "bg-card-bg font-medium text-ink"
                    : "text-muted hover:text-ink"
                }`}
                style={currentMode === t.v ? { boxShadow: "var(--shadow-1)" } : undefined}
              >
                {t.label}
              </button>
            ))}
          </div>
        )}

        <form
          className="mt-5 flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <label className="flex flex-col gap-1.5">
            <span className="eyebrow">用户名</span>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              placeholder={currentMode === "register" ? "至少 3 个字符" : ""}
              // 自动聚焦放在用户名上：打开就能敲，是登录页唯一该有的体贴
              autoFocus
              className="w-full rounded-sm border border-input-border bg-card-bg px-3.5 py-2.5 text-msg text-ink outline-none transition placeholder:text-faint focus:border-accent"
            />
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="eyebrow">密码</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={currentMode === "register" ? "new-password" : "current-password"}
              placeholder={currentMode === "register" ? "至少 6 位" : ""}
              className="w-full rounded-sm border border-input-border bg-card-bg px-3.5 py-2.5 text-msg text-ink outline-none transition placeholder:text-faint focus:border-accent"
            />
          </label>

          {error && (
            <p className="text-fs-sm text-red-ink" role="alert">
              {error}
            </p>
          )}

          <button type="submit" disabled={busy || checking} className="btn-new mt-1 w-full disabled:opacity-60">
            {busy ? "请稍候..." : currentMode === "login" ? "登录" : "注册并登录"}
          </button>
        </form>

        <p className="mt-5 text-center text-fs-xs leading-relaxed text-faint">
          每人只能看到自己的会话与知识库，互相不可见。
        </p>
      </div>
    </div>
  );
}
