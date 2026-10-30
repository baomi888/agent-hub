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
    // 注册前先在本地拦一道，和后端 core/account.py 的规则对齐：
    // 否则用户填错一次就打一次 400，日志里全是重试（内测第一天实测如此）。
    if (currentMode === "register") {
      if (u.length < 3 || u.length > 32) {
        setError("用户名需 3~32 个字符");
        return;
      }
      // 后端正则 ^[\w.\-]+$ 按 Unicode 匹配（含中文），JS 的 \w 不含中文，
      // 用 Unicode 属性转义对齐：字母/数字 + 下划线 + 点 + 连字符
      if (!/^[\p{L}\p{N}._\-]+$/u.test(u)) {
        setError("用户名只能包含字母、数字、中文、下划线、点或连字符");
        return;
      }
      if (password.length < 6 || password.length > 128) {
        setError("密码需 6~128 位");
        return;
      }
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
    <div className="auth-stage">
      <div className="auth-card">
        {/* 品牌区：只有图腾和名字。
            原先这里还有一行"登录后使用你自己的会话与知识库"、卡片底部还有一行
            "每人只能看到自己的会话与知识库" —— 两句话说的都是**我们怎么做的隔离**，
            回来登录的人不需要被告知这件事，删掉后上半部留白反而把品牌托了出来。 */}
        <div className="auth-brand">
          <MascotLogo className="auth-logo" />
          <h1 className="auth-name">知穗 Agent</h1>
        </div>

        {allowSignup && (
          <div className="auth-seg" role="tablist" aria-label="登录或注册">
            {(
              [
                { v: "login", label: "登录" },
                { v: "register", label: "注册" },
              ] as const
            ).map((t) => (
              <button
                key={t.v}
                type="button"
                role="tab"
                aria-selected={currentMode === t.v}
                onClick={() => {
                  setMode(t.v);
                  setError(null);
                }}
              >
                {t.label}
              </button>
            ))}
          </div>
        )}

        <form
          className="auth-form"
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <label className="auth-label">
            <span>用户名</span>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              placeholder={currentMode === "register" ? "至少 3 个字符" : ""}
              // 自动聚焦放在用户名上：打开就能敲，是登录页唯一该有的体贴
              autoFocus
              className="auth-input"
            />
          </label>

          <label className="auth-label">
            <span>密码</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={currentMode === "register" ? "new-password" : "current-password"}
              placeholder={currentMode === "register" ? "至少 6 位" : ""}
              className="auth-input"
            />
          </label>

          {error && (
            <p className="text-fs-sm text-red-ink" role="alert">
              {error}
            </p>
          )}

          <button type="submit" disabled={busy || checking} className="btn-new auth-submit disabled:opacity-60">
            {busy ? "请稍候..." : currentMode === "login" ? "登录" : "注册并登录"}
          </button>
        </form>
      </div>
    </div>
  );
}
