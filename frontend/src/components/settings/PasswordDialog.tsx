"use client";

// 修改密码弹窗。
//
// 三个决定，都是为了"改密码这件事不能半懂不懂"：
//   1. **必须输原密码**：否则任何拿到未锁屏电脑的人都能把号锁死。
//   2. **改完要告诉用户"其它设备已退出"**：后端会写令牌水位把其它会话全部作废
//      （见 docs/账号自助_头像姓名密码_方案_2026-11-03.md D3）。不说明的话，
//      用户在另一台设备上被弹回登录页时会以为是故障。
//   3. **成功态单独一屏**，不直接关窗：用户需要一眼确认"确实改成了"，
//      而不是"我点了一下，窗没了，到底成没成？"
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import DialogShell from "./DialogShell";

interface Props {
  open: boolean;
  onClose: () => void;
}

/** 后端错误文案优先，兜底给一句人话（不要露出 HTTP 500 这种） */
function toMsg(e: unknown): string {
  const m = (e as Error)?.message;
  return m && m.trim() ? m : "没能改成，请稍后重试";
}

export default function PasswordDialog({ open, onClose }: Props) {
  const [oldPwd, setOldPwd] = useState("");
  const [newPwd, setNewPwd] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  // 每次打开都清空：留着上次输的密码，等于把明文摆在屏幕上。
  // 延到下一个宏任务再 setState —— effect 里同步 setState 会被
  // react-hooks/set-state-in-effect 判成级联渲染（本仓库统一这么绕，见 settings/page.tsx）
  useEffect(() => {
    if (!open) return;
    const t = window.setTimeout(() => {
      setOldPwd("");
      setNewPwd("");
      setConfirm("");
      setErr(null);
      setDone(false);
      setBusy(false);
    }, 0);
    return () => window.clearTimeout(t);
  }, [open]);

  async function submit(e?: React.FormEvent) {
    e?.preventDefault();
    if (busy) return;
    // 前端这三条只是省一次往返；后端还会再校验一遍（前端校验永远可以被绕过）
    if (!oldPwd) {
      setErr("请先输入原密码");
      return;
    }
    if (newPwd.length < 8) {
      setErr("新密码至少 8 位");
      return;
    }
    if (newPwd !== confirm) {
      setErr("两次输入的新密码不一样");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      await api.changePassword(oldPwd, newPwd);
      setDone(true);
    } catch (e2) {
      setErr(toMsg(e2));
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <DialogShell
        open={open}
        title="密码已修改"
        onClose={onClose}
        footer={
          <button type="button" className="btn btn-primary btn-sm" onClick={onClose} data-autofocus>
            知道了
          </button>
        }
      >
        <p className="acct-ok-line">
          <span className="acct-ok-dot" aria-hidden="true" />
          以后用新密码登录。
        </p>
        <p className="hint" style={{ marginTop: 10 }}>
          其它设备上的登录已经失效，需要重新登录一次；你现在这台不受影响，可以继续用。
          如果刚才那台是你自己的手机，下次打开重新输一下新密码就行。
        </p>
      </DialogShell>
    );
  }

  return (
    <DialogShell
      open={open}
      title="修改密码"
      desc="改完之后，其它设备上的登录会失效，需要重新登录。你现在这台不受影响。"
      onClose={onClose}
      busy={busy}
      footer={
        <>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose} disabled={busy}>
            取消
          </button>
          <button
            type="submit"
            form="acct-pw-form"
            className="btn btn-primary btn-sm"
            disabled={busy}
          >
            {busy ? "正在改…" : "确认修改"}
          </button>
        </>
      }
    >
      <form id="acct-pw-form" className="acct-form" onSubmit={submit}>
        <label className="acct-field">
          <span className="acct-label">原密码</span>
          <input
            className="auth-input"
            type="password"
            autoComplete="current-password"
            value={oldPwd}
            onChange={(e) => setOldPwd(e.target.value)}
            data-autofocus
          />
        </label>
        <label className="acct-field">
          <span className="acct-label">新密码</span>
          <input
            className="auth-input"
            type="password"
            autoComplete="new-password"
            value={newPwd}
            onChange={(e) => setNewPwd(e.target.value)}
          />
          <span className="hint">至少 8 位。别用生日和学号，那是最先被试的几个。</span>
        </label>
        <label className="acct-field">
          <span className="acct-label">再输一次新密码</span>
          <input
            className="auth-input"
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
        </label>
        {err && (
          <p className="acct-err" role="alert">
            {err}
          </p>
        )}
      </form>
    </DialogShell>
  );
}
