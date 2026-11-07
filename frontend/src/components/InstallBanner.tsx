"use client";

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { usePathname, useRouter } from "next/navigation";
import {
  detectPlatform,
  isInstalled,
  promptInstall,
  subscribePwa,
  type Platform,
} from "@/lib/pwa";

/* 首次进入时提示一次「装到桌面」。
 *
 * 设计取舍：
 *   - 只做手机（iOS / 安卓）。桌面浏览器地址栏右边本来就有安装入口，再弹一条会烦人。
 *   - 只做已登录之后：登录页上弹「装到桌面」很奇怪，用户还没决定要不要用。
 *     ★★ 这一条靠"挂载位置"保证，不靠组件自己判断：本组件只挂在 app/page.tsx
 *     的已登录分支里。早期挂在根布局、用 document.querySelector(".auth-stage")
 *     探测登录门，结果撞上水合竞态 —— 首屏那几百毫秒里登录门还没上屏，
 *     组件判定"可以弹"，渲染的同时就把 60 天冷却写进了 localStorage；
 *     用户随后登录进来，提示再也不出现（线上实测：时间戳比登录动作还早）。
 *     DOM 探测永远慢半拍，用"不渲染"来保证才是真的保证。
 *   - 每条会话最多提一次（localStorage 60 天冷却）：用户可能划走没看见，
 *     但反复弹更讨厌；没装的人可以去设置页的固定入口。
 *   - 挂在页面顶部而不是底部：手机聊天页底部是输入框，浮层压上去没法打字。
 */

// v2：v1 的冷却时间戳可能被上述竞态写脏（登录前就写进去了），
// 换个 key 让那些账号重新有机会看到一次。代价只是最多多弹一次。
const STORE_KEY = "baomi.installHintAt.v2";
const COOLDOWN = 60 * 24 * 3600 * 1000;

/* 挂载后先等一会儿再考虑露脸。
 * 「上手说明」和它是同一批挂载的（都在登录态确认之后）：React 渲染本组件时
 * 引导层的 DOM 还没进文档，此刻读 .guide-mask 永远是 null —— 两个浮层谁先出现
 * 完全看运气（线上两次验收一次叠加一次正常）。给引导层一个先上屏的机会，
 * 这段延迟里它必然已经进 DOM 了。 */
const ARM_DELAY = 1200;

/* 露脸后要连续显示这么久才记账。
 * 只"渲染过一帧"就写冷却太草率：更新公告是异步拉接口才弹的，可能在提示条
 * 出来之后才盖上；那种情况下用户根本没看清，不该扣掉 60 天一次的机会。
 * 被模态层挤掉时本组件会卸载，定时器随之取消，这一笔就不写了。 */
const CONFIRM_DELAY = 2500;

/* 本次会话是否已经真的把提示露出来了。
 * 原因：露出来之后要往 localStorage 记时间戳（不然下次刷新又弹），
 * 而一旦记进去，getSnapshot 再读就会返回 false —— 界面刚出来就自己消失了。
 * 用这个模块级标记把"本次会话已展示"钉住，刷新页面才会重新判断。
 *
 * ★ 记账必须发生在「用户已登录、提示真的显示出来」之后，不能放在组件挂载时：
 *   首次访客先到的是登录页，那时如果就记账，等他登录进来 60 天冷却已经生效，
 *   提示直接被自己挡掉 —— 线上实测就是这样，设置页入口正常但首页从来不弹。 */
let decidedThisSession = false;

function readLastShown(): number {
  try {
    return Number(window.localStorage.getItem(STORE_KEY) || 0);
  } catch {
    return 0; // 隐私模式读不到，当作没提示过
  }
}

function markShown() {
  if (decidedThisSession) return;
  decidedThisSession = true;
  try {
    window.localStorage.setItem(STORE_KEY, String(Date.now()));
  } catch {
    /* 存不了就只提示这一次 */
  }
}

export default function InstallBanner() {
  const [dismissed, setDismissed] = useState(false);
  // 只取 setter：轮询变化时触发重渲染，让 getSnapshot 重新求值
  const [, setTick] = useState(0);
  // 过了 ARM_DELAY 才允许露脸（见常量注释）。用 setTimeout 而不是同步 setState，
  // 前者是异步回调，不触发 react-hooks/set-state-in-effect
  const [armed, setArmed] = useState(false);

  useEffect(() => {
    const t = window.setTimeout(() => setArmed(true), ARM_DELAY);
    return () => window.clearTimeout(t);
  }, []);

  /* 首次登录会先弹「上手说明」引导层。两个浮层同时出现会叠在一起，
     所以有模态时让位，等它关掉再出来。
     GuideModal 没有"关闭"事件可订阅，只能定期回来看 DOM 里的遮罩还在不在 ——
     800ms 一次。上限 2 分钟：真要有人盯着引导层读两分钟以上，就等他下次进首页再说，
     不值得让定时器无限跑。记账一旦落地就没必要再探了，立刻停。 */
  useEffect(() => {
    let n = 0;
    const timer = window.setInterval(() => {
      if (decidedThisSession) {
        window.clearInterval(timer);
        return;
      }
      n += 1;
      setTick(n);
      if (n >= 150) window.clearInterval(timer);
    }, 800);
    return () => window.clearInterval(timer);
  }, []);

  const eligible = useSyncExternalStore(
    subscribePwa,
    () => {
      if (dismissed) return false;
      // 刚挂载的那一小会儿先别露脸，等引导层把 DOM 上齐（见 ARM_DELAY）
      if (!armed) return false;
      // 只在首页提一次。设置页/工具页自己有固定入口，同一屏出现两个一样的入口很冗余。
      // （正常情况下本组件只挂在首页，这条是防止将来被挂到根布局后到处冒出来。）
      if (window.location.pathname !== "/") return false;
      // 有模态层（上手说明 / 更新说明）时一律让位。
      // ★ 这一条必须排在 decidedThisSession 之前：提示一旦露过面就会把标记置位，
      //   若让标记抢先返回 true，引导层随后打开时提示仍然叠在上面（线上就是这么复现的）。
      if (document.querySelector('.guide-mask, [aria-modal="true"]')) return false;
      // 本次会话已决定显示 → 保持显示（记账写进 localStorage 后不能让自己消失）
      if (decidedThisSession) return true;
      if (isInstalled()) return false;
      const platform = detectPlatform();
      if (platform !== "ios" && platform !== "android") return false;
      const last = readLastShown();
      return !(last && Date.now() - last < COOLDOWN);
    },
    () => false
  );

  // 用户主动关掉（×）或装完了：这是"确实看见过"的最强证据，直接记账，
  // 不等 CONFIRM_DELAY —— 否则他先点了关闭，定时器随卸载取消，下次进来又弹一遍
  const close = useCallback(() => {
    markShown();
    setDismissed(true);
  }, []);

  if (!eligible || dismissed) return null;
  return <Banner onClose={close} />;
}

/** 参数固定，避免每次渲染新建函数导致 useSyncExternalStore 反复重订阅 */
const getPlatform = (): Platform => detectPlatform();
const getCanPrompt = (): boolean => !!window.__baomiInstallPrompt;

function Banner({ onClose }: { onClose: () => void }) {
  const platform = useSyncExternalStore(subscribePwa, getPlatform, () => "other" as Platform);
  const canPrompt = useSyncExternalStore(subscribePwa, getCanPrompt, () => false);
  // 教程页有两个，按"在不在登录页"分流：
  //   · 首页 = 已登录 → /settings/install，应用内那一页，左导航还在
  //     （公开页 /install 是独立页，左导航会消失，从应用里点过去就像退出了）
  //   · 登录页 → /install。注意 eligible 里有 `pathname !== "/"` 的守卫，
  //     所以登录页那份（app/login/page.tsx 挂的）当前根本不会显示，这一支是保险：
  //     将来若放开到登录页，未登录的人去 /settings/install 只会被登录门弹回来。
  const router = useRouter();
  const pathname = usePathname();
  const tutorial = pathname?.startsWith("/login") ? "/install" : "/settings/install";
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    window.addEventListener("baomi:installed", onClose);
    return () => window.removeEventListener("baomi:installed", onClose);
  }, [onClose]);

  // 连续露脸够久才记账；被模态层挤掉时组件卸载，定时器取消，这一笔不写
  useEffect(() => {
    const t = window.setTimeout(markShown, CONFIRM_DELAY);
    return () => window.clearTimeout(t);
  }, []);

  const openGuide = () => {
    // 先记账再跳走：跳页会卸载本组件，来不及等 CONFIRM_DELAY
    markShown();
    router.push(tutorial);
  };

  const onAction = async () => {
    if (!canPrompt) {
      openGuide();
      return;
    }
    setBusy(true);
    const ok = await promptInstall();
    setBusy(false);
    // 没装成（用户取消 / 浏览器不给装）就把他送到分步教程
    if (ok) onClose();
    else openGuide();
  };

  return (
    <div
      role="dialog"
      aria-label="把知穗装到桌面"
      style={{
        position: "fixed",
        top: 0,
        left: 0,
        right: 0,
        zIndex: 90,
        padding: "calc(8px + env(safe-area-inset-top, 0px)) 10px 0",
        pointerEvents: "none",
      }}
    >
      <div
        style={{
          pointerEvents: "auto",
          background: "var(--bg-canvas)",
          border: "1px solid var(--line)",
          borderRadius: "var(--radius-lg)",
          boxShadow: "var(--shadow-2)",
          padding: "10px 12px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/mascot.svg" alt="" width={34} height={34} style={{ flex: "none" }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: "var(--fs-lg)", fontWeight: 600, lineHeight: 1.3 }}>
              把知穗装到桌面
            </div>
            <div
              className="hint"
              style={{ margin: "2px 0 0", fontSize: "var(--fs-sm)", lineHeight: 1.4 }}
            >
              {canPrompt
                ? "点一下装到桌面"
                : platform === "ios"
                  ? "全屏使用，不用输网址"
                  : "桌面点一下就能用"}
            </div>
          </div>
          <button
            type="button"
            className="btn btn-sm"
            onClick={onAction}
            disabled={busy}
            style={{ flex: "none" }}
          >
            {busy ? "安装中…" : canPrompt ? "安装" : "怎么装"}
          </button>
          <button
            type="button"
            onClick={onClose}
            aria-label="不再提示"
            style={{
              flex: "none",
              border: 0,
              background: "transparent",
              color: "var(--ink)",
              opacity: 0.45,
              fontSize: 18,
              lineHeight: 1,
              padding: "4px 2px 4px 6px",
              cursor: "pointer",
            }}
          >
            ×
          </button>
        </div>
      </div>
    </div>
  );
}
