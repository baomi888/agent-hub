"use client";

// 「邮件提醒」—— 待办设置标签里「提醒权限」下面那张卡。
//
// ## 为什么需要它
//
// 安卓的网页通知底层一律走 Google 的 FCM，国内连不通，换浏览器也不解决
// （见 lib/push.ts 顶部）。日历订阅又在 2026-11-11 删掉了，所以安卓上
// 「到点提醒」等于只能打开 App 才看得见 —— 提醒这个功能在那边是半残的。
// 邮件是唯一在「没装 App、没有 GMS、不必点授权」的前提下还能把人叫回来的通道。
//
// ## 为什么要验证码，不能填了就用
//
// 「填个邮箱就生效」会造出一种**没有任何地方会报错的失败**：用户把 qq.com
// 打成 qq.co，开关是开的、界面写着「已开启」，然后永远收不到任何东西，
// 他没有任何理由怀疑是地址错了，只会觉得这功能没用。
//
// 验证码把这件事变成当场能看见的结果：收到 = 通了，收不到 = 现在就知道了。
// 顺带还免费当了一次自检 —— 发码这一步跑的就是完整发信链路。
//
// ## 为什么没有「用邮件还是用通知」的选择
//
// 服务端只在通知零投递时才发信。已经能在手机上看到通知的人（iPhone 装到主屏、
// Windows 上的 Edge）再收一封邮件只是打扰；而用户既不知道自己的手机走不走
// FCM，也不该知道。所以这件事由服务端判断，界面上不给他一个看不懂的开关。
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { MailState } from "@/lib/types";
import { Switch } from "@/components/console/ui";

/** 秒数 → 「9 分 12 秒」/「58 秒」。验证码有效期用，不精确到分也无所谓。 */
function fmtLeft(sec: number): string {
  if (sec <= 0) return "已过期";
  if (sec < 60) return `${sec} 秒`;
  return `${Math.floor(sec / 60)} 分 ${sec % 60} 秒`;
}

export default function MailAccess() {
  /** null = 还没读到（含请求失败）。和「读到但没配」是两件事，不能混 */
  const [state, setState] = useState<MailState | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [addr, setAddr] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  /** 验证码剩余秒数。发码后由 effect 递减，不在渲染里算时间差。 */
  const [left, setLeft] = useState(0);
  /** 处于「换个地址」状态：已验证的卡上点一下才会露出输入框 */
  const [editing, setEditing] = useState(false);

  const refresh = useCallback(async () => {
    const s = await api.mail.get();
    setState(s);
    setLoaded(true);
    setCode("");
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // 倒计时。setState 推在 setTimeout(...,0) 里：本项目开了
  // react-hooks/set-state-in-effect，直接在 effect 体里 set 会报错。
  useEffect(() => {
    if (!state?.pending) return;
    if (state.code_expires_in <= 0) return;
    const seed = window.setTimeout(() => setLeft(state.code_expires_in), 0);
    const timer = window.setInterval(() => setLeft((v) => (v > 0 ? v - 1 : 0)), 1000);
    return () => {
      window.clearTimeout(seed);
      window.clearInterval(timer);
    };
  }, [state]);

  const fail = useCallback((e: unknown) => {
    // 后端的 400/429 都带中文说明（地址不对、验证码错、今天发太多），
    // 原样显示比"操作失败"有用得多。只有网络错误才退回通用文案。
    const msg = (e as Error)?.message || "";
    setNote(msg && !/^HTTP \d+/.test(msg) ? msg : "没成功，稍后再试");
  }, []);

  const send = useCallback(async () => {
    if (busy) return;
    const target = addr.trim();
    if (!target) {
      setNote("先填一个邮箱");
      return;
    }
    setBusy(true);
    setNote("");
    try {
      const s = await api.mail.start(target);
      setState(s);
      setNote("验证码发出去了，去邮箱里看一下（可能在垃圾邮件里）");
    } catch (e) {
      fail(e);
    }
    setBusy(false);
  }, [addr, busy, fail]);

  const verify = useCallback(async () => {
    if (busy) return;
    if (code.trim().length !== 6) {
      setNote("验证码是 6 位数字");
      return;
    }
    setBusy(true);
    setNote("");
    try {
      const s = await api.mail.verify(code.trim());
      setState(s);
      setEditing(false);
      setAddr("");
      setNote("验证好了。到点的提醒会发到这个邮箱。");
    } catch (e) {
      fail(e);
    }
    setBusy(false);
  }, [busy, code, fail]);

  const toggle = useCallback(
    async (next: boolean) => {
      if (busy) return;
      setBusy(true);
      setNote("");
      try {
        setState(await api.mail.setEnabled(next));
        setNote(next ? "打开了" : "关掉了。邮箱里不会再收到提醒，待办和提醒都还在。");
      } catch (e) {
        fail(e);
      }
      setBusy(false);
    },
    [busy, fail],
  );

  const remove = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    setNote("");
    try {
      setState(await api.mail.remove());
      setEditing(false);
      setAddr("");
      setNote("已删掉。想再用的时候重新填一次就行。");
    } catch (e) {
      fail(e);
    }
    setBusy(false);
  }, [busy, fail]);

  // ---------- 渲染 ----------

  const ready = state?.ready ?? false;
  const verified = Boolean(state?.verified);
  const showInput = ready && (!verified || editing) && !state?.pending;

  return (
    <section className="card card-pad" style={{ marginTop: 12 }}>
      <div
        className="row"
        style={{ justifyContent: "space-between", alignItems: "center", gap: 10 }}
      >
        <h3 className="card-title">邮件提醒</h3>
        {verified ? (
          <Switch
            checked={Boolean(state?.enabled)}
            label="邮件提醒"
            disabled={busy}
            onChange={(next) => void toggle(next)}
          />
        ) : null}
      </div>

      <p className="mt-1 text-[var(--fs-xs)] text-[var(--text-3)]">
        {!loaded
          ? "正在读邮件设置…"
          : !ready
            ? "服务端还没配好发信账号，这台暂时收不到邮件提醒。"
            : verified
              ? state?.enabled
                ? `到点会发到 ${state?.address}。手机上收不到通知时，这封邮件就是你能看到的那一条。`
                : `现在关着。打开后到点的提醒会发到 ${state?.address}。`
              : "手机浏览器在国内收不到网页通知。填一个常用邮箱，到点的提醒会发到那里，发件人是「苞米」。"}
      </p>

      {/* 验证码已发出：填码 + 重发 */}
      {state?.pending ? (
        <div className="mt-3">
          <p className="text-[var(--fs-xs)] text-[var(--text-3)]">
            验证码已发到 {state.address}，{fmtLeft(left)}内有效
          </p>
          <div className="row gap-2 mt-2" style={{ alignItems: "center" }}>
            <input
              className="input"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              placeholder="6 位验证码"
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              onKeyDown={(e) => {
                if (e.key === "Enter") void verify();
              }}
            />
            <button
              type="button"
              className="btn btn-primary btn-sm"
              disabled={busy || code.length !== 6}
              onClick={() => void verify()}
            >
              验证
            </button>
          </div>
          <div className="row gap-3 mt-2" style={{ alignItems: "center" }}>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={busy}
              onClick={() => void send()}
            >
              重发一封
            </button>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={busy}
              onClick={() => {
                setEditing(false);
                setAddr("");
                setNote("");
                void refresh();
              }}
            >
              换地址
            </button>
          </div>
        </div>
      ) : null}

      {/* 填地址 */}
      {showInput ? (
        <div className="mt-3">
          <div className="row gap-2" style={{ alignItems: "center" }}>
            <input
              className="input"
              type="email"
              inputMode="email"
              autoComplete="email"
              placeholder="你的邮箱，比如 xuehao@stu.edu.cn"
              value={addr}
              onChange={(e) => setAddr(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void send();
              }}
            />
            <button
              type="button"
              className="btn btn-primary btn-sm"
              disabled={busy || !addr.trim()}
              onClick={() => void send()}
            >
              发验证码
            </button>
          </div>
        </div>
      ) : null}

      {/* 已验证时的两个次要动作 */}
      {verified && state?.pending !== true ? (
        <div className="row gap-3 mt-2" style={{ alignItems: "center" }}>
          {!editing ? (
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={busy}
              onClick={() => {
                setEditing(true);
                setAddr(state?.address ?? "");
                setNote("");
              }}
            >
              换一个邮箱
            </button>
          ) : null}
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            disabled={busy}
            onClick={() => void remove()}
          >
            删掉
          </button>
        </div>
      ) : null}

      {note ? (
        <p className="mt-2 text-[var(--fs-xs)] text-[var(--text-3)]">{note}</p>
      ) : null}
    </section>
  );
}
