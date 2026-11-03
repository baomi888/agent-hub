"use client";

// 修改登录名弹窗。
//
// 与"改昵称"的关键区别：登录名是**凭证**，所以这里要输密码、要查重、还有终身上限。
// 三个设计点：
//   1. 输入框旁边常驻提示"还能改几次"——不告知上限，用户第 4 次才被拒，会很恼火。
//   2. 成功**不直接关窗**：把新登录名用大字摆出来让他点"我记得了"。
//      这不是仪式感，是这个功能唯一一次能让用户记住新名字的机会（没有邮箱可找回）。
//   3. 当前登录名在弹窗里一直可见：改完忘了自己叫什么，是这条路最真实的失败方式。
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import DialogShell from "./DialogShell";

interface Props {
  open: boolean;
  onClose: () => void;
  /** 当前登录名（改完之后这里还是旧值，成功态用新值另存） */
  currentUsername: string;
  /** 还能改几次。undefined = 未知（老后端），此时不显示次数 */
  remaining?: number;
  /** 改成功后通知外层刷新登录态（侧栏的登录名要跟着变） */
  onSaved: () => Promise<void> | void;
}

function toMsg(e: unknown): string {
  const m = (e as Error)?.message;
  return m && m.trim() ? m : "没能改成，请稍后重试";
}

export default function UsernameDialog({
  open,
  onClose,
  currentUsername,
  remaining,
  onSaved,
}: Props) {
  const [pwd, setPwd] = useState("");
  const [next, setNext] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  /** 改成功后的新登录名。非 null 即成功态 */
  const [saved, setSaved] = useState<string | null>(null);
  /**
   * 改名**之前**的登录名。
   *
   * 必须自己存一份：改成功后外层会 refresh，`currentUsername` 这个 prop 立刻
   * 变成新名字。直接读它的话，成功态会写成"原来那个「<刚设的新名>」已经不能
   * 登录了"——用户看到自己刚设的名字被告知失效，只会以为改坏了。
   * （这条是探针抓出来的，纯看代码看不出来：prop 变化是异步的。）
   */
  const [prevName, setPrevName] = useState("");

  // 同 PasswordDialog：每次打开都清空（别把上次输的密码留在屏幕上），
  // 且延到宏任务里 setState，避开 react-hooks 的级联渲染判定
  useEffect(() => {
    if (!open) return;
    const t = window.setTimeout(() => {
      setPwd("");
      setNext("");
      setErr(null);
      setSaved(null);
      setBusy(false);
    }, 0);
    return () => window.clearTimeout(t);
  }, [open]);

  const exhausted = typeof remaining === "number" && remaining <= 0;

  async function submit(e?: React.FormEvent) {
    e?.preventDefault();
    if (busy || exhausted) return;
    const name = next.trim();
    if (!pwd) {
      setErr("请先输入当前密码");
      return;
    }
    if (!name) {
      setErr("请填一个新的登录名");
      return;
    }
    if (name === currentUsername) {
      setErr("和现在的登录名一样，不用改");
      return;
    }
    setBusy(true);
    setErr(null);
    // 先记下旧名，再让外层刷新（refresh 之后 prop 就不是旧名了）
    const before = currentUsername;
    try {
      const me = await api.changeUsername(pwd, name);
      // 后端会重签 Cookie，前端把登录态重新拉一遍，侧栏才会显示新名字
      await onSaved();
      setPrevName(before);
      setSaved(me.user.username);
    } catch (e2) {
      setErr(toMsg(e2));
    } finally {
      setBusy(false);
    }
  }

  if (saved) {
    return (
      <DialogShell
        open={open}
        title="登录名改好了"
        onClose={onClose}
        footer={
          <button type="button" className="btn btn-primary btn-sm" onClick={onClose} data-autofocus>
            我记得了
          </button>
        }
      >
        <p className="hint" style={{ marginTop: 0 }}>
          下次登录请用这个：
        </p>
        <p className="acct-new-name">{saved}</p>
        <p className="hint">
          原来那个「{prevName}」已经不能登录了。如果怕忘，现在就用手机拍一张，
          或者在设置页看一眼 —— 那里一直显示你的登录名。
        </p>
      </DialogShell>
    );
  }

  return (
    <DialogShell
      open={open}
      title="修改登录名"
      desc={
        exhausted
          ? "修改次数已经用完了。想再改请联系管理员。"
          : "登录名用于登录，会一直显示在设置页。昵称是另一个东西，随便改，不受影响。"
      }
      onClose={onClose}
      busy={busy}
      footer={
        <>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose} disabled={busy}>
            取消
          </button>
          <button
            type="submit"
            form="acct-name-form"
            className="btn btn-primary btn-sm"
            disabled={busy || exhausted}
          >
            {busy ? "正在改…" : "确认修改"}
          </button>
        </>
      }
    >
      <form id="acct-name-form" className="acct-form" onSubmit={submit}>
        <div className="acct-now">
          <span className="acct-label">现在的登录名</span>
          <span className="acct-now-value">{currentUsername}</span>
        </div>

        <label className="acct-field">
          <span className="acct-label">当前密码</span>
          <input
            className="auth-input"
            type="password"
            autoComplete="current-password"
            value={pwd}
            onChange={(e) => setPwd(e.target.value)}
            disabled={exhausted}
            data-autofocus
          />
          <span className="hint">
            改登录名要验证一次密码：它是登录凭证，不能只凭「已经登录了」就改。
          </span>
        </label>

        <label className="acct-field">
          <span className="acct-label">
            新登录名
            {typeof remaining === "number" && (
              <span className="acct-quota">还能改 {remaining} 次</span>
            )}
          </span>
          <input
            className="auth-input"
            type="text"
            autoComplete="username"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            disabled={exhausted}
            placeholder="3~32 个字符"
          />
          <span className="hint">字母、数字、中文、下划线、点或连字符。改完这个账号就认这个名字了。</span>
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
