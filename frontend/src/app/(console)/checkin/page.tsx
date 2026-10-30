"use client";

// P6 · 打卡
//
// 页面任务：把「我今天学了」变成一件值得发出去的事。
//
// 为什么刻意克制：**不做奖惩、不做排行榜** —— 参考作业帮的教训，
// 竞争性激励带来的是负体验，而不是学习动力。
//
// 后端 /api/checkin/* 现已上线（4 端点：streak / history / generate / share），
// 打卡数据**服务端优先**：连续天数、最长连续、历史都从服务端 daily_reports 派生，
// 端点未部署时退回**本地记录**（baomi.reportDays，总结页生成时会写），再退回演示数据。
// 知识结构图仍来自当日总结结论（真实）或演示内容（挂「演示数据」标记）。
// 分享兜底：公网 http 下 navigator.clipboard 不可用，统一走项目里的 copyText()。
import { useEffect, useState } from "react";
import PageShell from "@/components/console/PageShell";
import CornIcon from "@/components/CornIcon";
import { Callout, SkeletonLines, ToastHost } from "@/components/console/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/hooks/useAuth";
import { DEMO_CHECKIN_HISTORY, DEMO_KNOWLEDGE_TREE } from "@/lib/consoleDemo";
import { toProbe, type ProbeState } from "@/lib/probe";
import { fmtDate } from "@/lib/time";
import { useToast } from "@/lib/hooks/useToast";
import { copyText } from "@/lib/useCopy";
import QRCode from "qrcode";
import type { CheckinHistoryItem, CheckinStreak, DailyReport } from "@/lib/types";

/** 与报告页共用同一份本地记录 */
const DAYS_KEY = "baomi.reportDays";
/** 打卡最远回溯多少天（决定圆点数量） */
const WINDOW_DAYS = 12;

interface DayMark {
  date: string;
  count: number;
}

function readDays(): DayMark[] {
  try {
    const raw = localStorage.getItem(DAYS_KEY);
    const v = raw ? (JSON.parse(raw) as DayMark[]) : [];
    return Array.isArray(v) ? v : [];
  } catch {
    return [];
  }
}

/** 从「有记录的日期」推连续天数：从今天（或昨天）往回数连续几天没断 */
function computeStreak(dates: string[], todayStr: string): number {
  return walkStreak(dates, todayStr, new Date(`${todayStr}T00:00:00`));
}

function walkStreak(dates: string[], todayStr: string, start: Date): number {
  const set = new Set(dates);
  const cursor = new Date(start);
  // 今天还没记，就从昨天起算 —— 否则每天清晨打开都是 0，体验上像在惩罚用户
  if (fmtDate(cursor) === todayStr && !set.has(todayStr)) cursor.setDate(cursor.getDate() - 1);
  let n = 0;
  for (let i = 0; i < 400; i++) {
    if (!set.has(fmtDate(cursor))) break;
    n++;
    cursor.setDate(cursor.getDate() - 1);
  }
  return n;
}

/**
 * 从区间起点**往后**数，得到这一段连续的长度。
 *
 * ⚠️ 必须往后数。原先这里复用了 `walkStreak`（它只会从起点往回走），
 * 结果一段连续 N 天永远只数出 1 天 —— 于是出现「已连续 2 天」+「近期最长 1 天」
 * 这种自相矛盾（2026-10-13 实机发现）。
 */
function walkForward(set: Set<string>, start: Date): number {
  let n = 0;
  const cursor = new Date(start);
  for (let i = 0; i < 400; i++) {
    if (!set.has(fmtDate(cursor))) break;
    n++;
    cursor.setDate(cursor.getDate() + 1);
  }
  return n;
}

/**
 * 最长连续天数。本地记录最多留 30 天，所以这是「近 30 天内的最长」——
 * 页面上要如实写成「近期最长」，不能冒充历史最长。
 */
function computeLongest(dates: string[]): number {
  const set = new Set(dates);
  let best = 0;
  for (const d of [...set].sort()) {
    const cur = new Date(`${d}T00:00:00`);
    const prev = new Date(cur);
    prev.setDate(prev.getDate() - 1);
    // 只从「区间起点」（前一天没有记录）开始数，避免重复计算
    if (set.has(fmtDate(prev))) continue;
    best = Math.max(best, walkForward(set, cur));
  }
  return best;
}

/** 近 N 天的打卡圆点（从最早到今天） */
function buildDots(dates: string[], todayStr: string, days: number) {
  const set = new Set(dates);
  const end = new Date(`${todayStr}T00:00:00`);
  const out: { key: string; on: boolean; today: boolean }[] = [];
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(end);
    d.setDate(d.getDate() - i);
    const key = fmtDate(d);
    out.push({ key, on: set.has(key), today: key === todayStr });
  }
  return out;
}

interface CardNode {
  name: string;
  level?: "ok" | "warn";
  children: string[];
}

export default function CheckinPage() {
  const { toasts, showToast } = useToast();
  const auth = useAuth();
  const authedId = auth.me?.user.id ?? null;
  const [todayStr, setTodayStr] = useState("");
  const [localDays, setLocalDays] = useState<DayMark[]>([]);
  const [report, setReport] = useState<ProbeState<DailyReport>>({ state: "loading" });
  const [busy, setBusy] = useState(false);
  // 服务端打卡数据（null = 端点未部署，页面退回本地/演示）
  const [streakData, setStreakData] = useState<CheckinStreak | null>(null);
  const [historyData, setHistoryData] = useState<CheckinHistoryItem[] | null>(null);
  const [genBusy, setGenBusy] = useState(false);

  useEffect(() => {
    // 延到下一个宏任务再 setState：直接在 effect 体内同步 setState 会被判成级联渲染
    // （eslint react-hooks/set-state-in-effect），这是本仓库既有的做法（见 useAuth）
    const t = window.setTimeout(() => {
      const d = fmtDate(new Date());
      setTodayStr(d);
      setLocalDays(readDays());
    }, 0);
    // 未登录不发请求：登录前发的请求必然是 401（会把卡片钉在「读取失败」上），
    // 登录成功会经 AUTHORIZED_EVENT 同步进 auth.me（见 useAuth / api.ts）→ 依赖变化补加载
    if (authedId) {
      api.report
        .today()
        .then((r) => setReport(toProbe(r)))
        .catch(() => setReport({ state: "error", message: "读取今日总结失败" }));
      // 打卡：服务端优先，拿不到（端点未部署）则 null → 页面退回本地/演示
      api.checkin
        .streak()
        .then((s) => setStreakData(s))
        .catch(() => setStreakData(null));
      api.checkin
        .history()
        .then((h) => setHistoryData(h))
        .catch(() => setHistoryData(null));
    }
    return () => window.clearTimeout(t);
  }, [authedId]);

  // ---- 卡片内容：优先用真实结论，退到演示结构 ----
  const liveReport =
    report.state === "ready" && report.data.status === "ready" && report.data.content.length > 0
      ? report.data
      : null;

  const usingDemoTree = !liveReport;
  const root = liveReport ? "今日所学" : DEMO_KNOWLEDGE_TREE.root;
  const nodes: CardNode[] = liveReport
    ? liveReport.content.map((c, i) => ({
        name: `结论 ${i + 1}`,
        children: [c.text.replace(/\*\*/g, "")],
      }))
    : DEMO_KNOWLEDGE_TREE.nodes.map((n) => ({
        name: n.name,
        level: n.level,
        children: n.children,
      }));

  const pointCount = nodes.reduce((s, n) => s + n.children.length, 0);

  // ---- 连续天数 ----
  // 「历史」在没有本机记录时会回退到演示数据，那么连续天数与圆点**必须取自同一份来源**，
  // 否则同一张卡里会同时出现「历史：10-13 已分享、10-12 已分享」和「0 天连续、12 个空点」
  // 这种互相打脸的组合（2026-10-13 实机发现）。
  const hasLocal = localDays.length > 0;
  // 服务端优先：两端都拿不到才退回本地记录 / 演示数据
  const fromServer = streakData !== null || historyData !== null;
  const serverHist = historyData;
  const dayMarks: DayMark[] = fromServer
    ? (serverHist ?? []).map((h) => ({ date: h.date, count: h.points }))
    : localDays;
  const dates: string[] = fromServer
    ? streakData?.dates ?? (serverHist ?? []).map((h) => h.date)
    : dayMarks.map((d) => d.date);
  // 连续天数优先用服务端算好的（口径统一在后端），否则本地推算兜底
  const streak = todayStr
    ? streakData
      ? streakData.streak
      : computeStreak(dates, todayStr)
    : 0;
  const longest = todayStr
    ? streakData
      ? streakData.longest
      : computeLongest(dates)
    : 0;
  const dots = todayStr ? buildDots(dates, todayStr, WINDOW_DAYS) : [];
  const dataSource = fromServer ? "服务端" : hasLocal ? "本地记录" : "演示数据";

  /** 文字版：分享兜底的第一步，也是唯一在公网 http 下 100% 可用的导出方式 */
  const toText = () =>
    [
      `今日所学 · ${todayStr}`,
      `知穗 · 知穗Agent`,
      "",
      root,
      ...nodes.flatMap((n) => [`· ${n.name}`, ...n.children.map((c) => `   - ${c}`)]),
      "",
      `共 ${pointCount} 个知识点${dataSource !== "演示数据" ? ` · 已连续 ${streak} 天` : ""}`,
      "知穗 · 每天进步一点点",
    ].join("\n");

  /** 生成今日打卡 = 触发当日总结（复用 /api/report/generate 同一份逻辑）。
   *  后端同步生成约 20–40 秒，用 genBusy 兜住；完成后重拉三块数据。 */
  const generateToday = async () => {
    if (!authedId) return;
    setGenBusy(true);
    try {
      await api.checkin.generate();
      // 重拉：今日总结（知识结构）+ 连续/历史
      const r = await api.report.today().catch(() => null);
      if (r) setReport(toProbe(r));
      const s = await api.checkin.streak().catch(() => null);
      if (s) setStreakData(s);
      const h = await api.checkin.history().catch(() => null);
      if (h) setHistoryData(h);
      showToast("success", "今日打卡已生成");
    } catch (e) {
      showToast("error", e instanceof Error ? e.message : "生成失败");
    } finally {
      setGenBusy(false);
    }
  };

  /** 标记今天已分享（幂等）。最佳努力：失败静默，不影响分享动作本身。 */
  const markSharedToday = () => {
    if (!todayStr) return;
    api.checkin
      .share(todayStr)
      .then((res) => {
        if (res?.ok) {
          api.checkin.history().then((h) => h && setHistoryData(h)).catch(() => {});
        }
      })
      .catch(() => {});
  };

  const copyCard = async () => {
    const ok = await copyText(toText());
    showToast(ok ? "success" : "error", ok ? "文字版已复制，可以直接粘贴分享" : "复制失败，请长按选择文字");
    if (ok) markSharedToday();
  };

  /* ============ 分享卡上的二维码 ============
     为什么把码放在这张卡上：它是全站唯一会**离开屏幕**的东西 ——
     用户会把卡片发进群、发朋友圈，别人看到卡就能扫进来，这才是拉新。
     首页放码只有自己看得见，意义小得多（所以那一处这次不做）。
     链接带 ?from=card，将来能统计「多少人是从别人的卡片进来的」。 */
  const SHARE_URL = "https://myzhisui.cn/?from=card";

  /* 码的几何常量提到组件作用域：探针要拿它算「每个模块占几个像素」——
     这是二维码能不能被扫出来的**物理指标**（低于 2px/格 任何解码器都不可靠），
     留在 buildCardSvg 里就只能靠"碰巧解出来了"反推，那不算验收。 */
  const QBOX = 214;
  const QPAD = 9;
  const QR_SIZE = QBOX - QPAD * 2;
  let _qrModules: number | null = null;
  const qrModules = (): number =>
    (_qrModules ??= QRCode.create(SHARE_URL, { errorCorrectionLevel: "H" }).modules.size);

  /** 二维码 → 一堆 <rect>，直接画进卡片 SVG，导出时跟着一起走（不依赖外链图片） */
  const qrRects = (size: number): string => {
    const qr = QRCode.create(SHARE_URL, { errorCorrectionLevel: "H" });
    const n = qr.modules.size;
    const cell = size / n;
    let out = "";
    for (let row = 0; row < n; row++) {
      for (let col = 0; col < n; col++) {
        if (!qr.modules.get(row, col)) continue;
        out +=
          `<rect x="${(col * cell).toFixed(2)}" y="${(row * cell).toFixed(2)}"` +
          ` width="${cell.toFixed(2)}" height="${cell.toFixed(2)}"/>`;
      }
    }
    return out;
  };


  /**
   * 卡片源码。PNG 与 SVG 两条导出共用这一份 —— 各画一份必然慢慢长得不一样。
   * ★ 二维码必须**白底深码**：卡片底色是米白 #FDFAF2，码直接画上去对比度不够，
   *   扫码率会掉（做宣传图那次踩过：纯白挖空会跟底色糊成一片，边界不成立）。
   */
  const buildCardSvg = () => {
    const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    // SVG 的 <text> 不换行：一行放不下就整段溢出画布。
    // 按 26 个全角字符手动折行（520px 宽、14px 字号下的安全值）
    const wrap = (s: string, per = 26): string[] => {
      if (s.length <= per) return [s];
      const out: string[] = [];
      for (let i = 0; i < s.length; i += per) out.push(s.slice(i, i + per));
      return out;
    };
    const lines: string[] = [];
    let y = 150;
    for (const n of nodes) {
      lines.push(
        `<text x="48" y="${y}" font-size="17" font-weight="700" fill="#3D2F1A">${esc(n.name)}${
          n.level === "warn" ? "   （待加强）" : ""
        }</text>`,
      );
      y += 26;
      for (const c of n.children) {
        for (const seg of wrap(c)) {
          lines.push(`<text x="72" y="${y}" font-size="14" fill="#5A4C33">- ${esc(seg)}</text>`);
          y += 22;
        }
      }
      y += 10;
    }

    /* 底部：居中一个大码 + 一行说明。尺寸是被实测逼出来的，两次：
       ① 第一版 100px 放右下角（和统计文字并排），导出 1040px 的 PNG 原图能扫，
         但整卡缩到 390px（微信里打开的正常显示宽度）就扫不出来 —— 码只有 75px、
         33 个模块，2.2px 一格，相机拿不到。
       ② 改成 160px 居中后 390/300/260 都稳，但 **195px 档是碰运气的**：
         60px ÷ 33 模块 = 1.8px/格，已低于可靠解码的 2px 下限；同一个文件在
         AREA/LINEAR/NEAREST 三种插值下通断结果互相打架（240 通、220 不通、210 又通）
         —— 那是在临界点上掷骰子，不是真能扫。
       现在 196px（QBOX 214 − 静区 9×2）：整卡缩到 195px 仍有 73px、2.2px 一格。
       代价是白底方块高了 36px，但分享卡的码本来就是主角，大一点不吃亏。 */
    const DIV = y + 6;
    const boxX = Math.round((520 - QBOX) / 2);
    const statY = DIV + 30;
    const boxY = statY + 14;
    const capY = boxY + QBOX + 22;
    const height = capY + 22;
    const streakNote = dataSource !== "演示数据" ? ` · 已连续 ${streak} 天` : "";

    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="520" height="${height}" viewBox="0 0 520 ${height}">
  <rect width="520" height="${height}" rx="16" fill="#FDFAF2" stroke="#D8CCB0"/>
  <text x="48" y="60" font-size="24" font-weight="700" fill="#3D2F1A" font-family="serif">今日所学</text>
  <text x="472" y="60" font-size="12" fill="#6F6044" text-anchor="end">${esc(todayStr)}</text>
  <line x1="48" y1="82" x2="472" y2="82" stroke="#D8CCB0" stroke-dasharray="6 5"/>
  <text x="48" y="112" font-size="15" font-weight="700" fill="#875B10">${esc(root)}</text>
  ${lines.join("\n  ")}
  <line x1="48" y1="${DIV}" x2="472" y2="${DIV}" stroke="#D8CCB0" stroke-dasharray="6 5"/>
  <text x="48" y="${statY}" font-size="13" fill="#5A4C33">共 ${pointCount} 个知识点${streakNote}</text>
  <text x="472" y="${statY}" font-size="12" fill="#6F6044" text-anchor="end">知穗 · 每天进步一点点</text>
  <rect x="${boxX}" y="${boxY}" width="${QBOX}" height="${QBOX}" rx="12" fill="#FFFFFF" stroke="#E4D6B8"/>
  <g transform="translate(${boxX + QPAD} ${boxY + QPAD})" fill="#2E2416">${qrRects(QR_SIZE)}</g>
  <text x="260" y="${capY}" font-size="12" fill="#6F6044" text-anchor="middle">扫码打开知穗 · 在手机上接着记</text>
</svg>`;
    return { svg, height };
  };

  const saveBlob = (blob: Blob, filename: string) => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  /**
   * SVG → PNG。
   * ★ 必须做：微信不认 SVG（聊天、朋友圈都发不出去），原来只给 SVG 等于
   *   「能下载但传不出去」。卡片上加了二维码之后这一条变得致命 ——
   *   卡传不出去，码就是白印。
   * scale=2 是为了看清：520 宽的卡出 1040px 的图，缩略图也不糊。
   */
  const svgToPng = (svg: string, w: number, h: number, scale = 2): Promise<Blob | null> =>
    new Promise((resolve) => {
      const url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml;charset=utf-8" }));
      const img = new Image();
      img.onload = () => {
        try {
          const canvas = document.createElement("canvas");
          canvas.width = Math.round(w * scale);
          canvas.height = Math.round(h * scale);
          const ctx = canvas.getContext("2d");
          if (!ctx) {
            URL.revokeObjectURL(url);
            resolve(null);
            return;
          }
          ctx.scale(scale, scale);
          ctx.drawImage(img, 0, 0, w, h);
          URL.revokeObjectURL(url);
          canvas.toBlob((b) => resolve(b), "image/png");
        } catch {
          URL.revokeObjectURL(url);
          resolve(null);
        }
      };
      // 加载失败也要落地，否则 busy 永远转下去
      img.onerror = () => {
        URL.revokeObjectURL(url);
        resolve(null);
      };
      img.src = url;
    });

  const downloadCard = async (fmt: "png" | "svg") => {
    setBusy(true);
    try {
      const { svg, height } = buildCardSvg();
      const base = `知穗打卡-${todayStr || "today"}`;
      if (fmt === "svg") {
        saveBlob(new Blob([svg], { type: "image/svg+xml;charset=utf-8" }), `${base}.svg`);
        showToast("success", "已保存 SVG（矢量版，适合再排版；微信里发不出去）");
      } else {
        const blob = await svgToPng(svg, 520, height, 2);
        if (!blob) throw new Error("png");
        saveBlob(blob, `${base}.png`);
        showToast("success", "已保存 PNG，可以直接发微信/朋友圈");
      }
      markSharedToday();
    } catch {
      showToast("error", "导出失败，可先用「复制文字版」");
    } finally {
      setBusy(false);
    }
  };

  const historyRows: { date: string; points: number; shared: boolean }[] = fromServer
    ? (serverHist ?? [])
    : hasLocal
      ? localDays.map((d) => ({ date: d.date, points: d.count, shared: false }))
      : DEMO_CHECKIN_HISTORY;

  return (
    <PageShell
      eyebrow="把今天记下来"
      title="打卡"
      sub={
        <>
          把今天的总结变成一张
          <b>只讲知识结构、不露个人内容</b>的图。分享出去的是「学习笔记」，不是「代做产物」。
        </>
      }
      actions={
        <>
          <button
            type="button"
            className="btn btn-ghost"
            disabled={genBusy}
            onClick={() => void generateToday()}
          >
            <CornIcon name="check" />
            {genBusy ? "生成中…" : "生成今日打卡"}
          </button>
          <button type="button" className="btn btn-ghost" onClick={() => void copyCard()}>
            <CornIcon name="link" />
            复制文字版
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy}
            onClick={() => void downloadCard("png")}
            /* 给验收探针算「每模块几个像素」用：卡片画布宽 520，码 QR_SIZE 见常量。
               低于 2px/格 时机器解码不可靠，这是物理下限，不是口味问题。 */
            data-qr-size={QR_SIZE}
            data-qr-modules={qrModules()}
          >
            <CornIcon name="download" />
            保存这张图
          </button>
          {/* PNG 是主路径（微信只认 PNG）；SVG 留给要再排版的人 */}
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            disabled={busy}
            title="矢量版，适合再排版；微信里发不出去"
            onClick={() => void downloadCard("svg")}
          >
            SVG
          </button>
        </>
      }
    >
      <div className="grid2">
        {/* ============ 左：打卡卡 ============ */}
        <div>
          <p className="sec-label">
            今日打卡卡
            {usingDemoTree && (
              <span className="ctag warn" style={{ marginLeft: 6 }}>
                演示数据
              </span>
            )}
          </p>

          <div className="share">
            <div className="share-top">
              <CornIcon name="corn" className="logo" title="知穗" />
              <b>今日所学</b>
              <span className="meta">
                {todayStr || "—"}
                <br />
                知穗 · 知穗Agent
              </span>
            </div>

            {report.state === "loading" ? (
              <div style={{ marginTop: 16 }}>
                <SkeletonLines widths={["70%", "92%", "58%"]} />
              </div>
            ) : (
              <div className="tree">
                <div className="root">
                  <CornIcon name="book" />
                  {root}
                </div>
                <ul>
                  {nodes.map((n) => (
                    <li key={n.name}>
                      <b>{n.name}</b>
                      {n.level === "ok" && (
                        <span className="ctag ok" style={{ marginLeft: 6 }}>
                          已掌握
                        </span>
                      )}
                      {n.level === "warn" && (
                        <span className="ctag warn" style={{ marginLeft: 6 }}>
                          待加强
                        </span>
                      )}
                      <ul>
                        {n.children.map((c) => (
                          <li key={c}>{c}</li>
                        ))}
                      </ul>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <div className="share-foot">
              <span>
                共 {pointCount} 个知识点
                {nodes.some((n) => n.level === "warn") ? " · 有 1 处待加强" : ""}
              </span>
              <span>知穗 · 每天进步一点点</span>
            </div>
          </div>

          <div style={{ marginTop: 12 }}>
            <Callout>
              <b>分享前自动脱敏</b>：这张图只保留<b>知识结构</b>（概念、章节、掌握度），
              自动剥离姓名 / 学号 / 手机号 / 身份证，以及你自己的原话与做错的题。
            </Callout>
          </div>

          <div style={{ marginTop: 12 }}>
            <Callout icon="ok">
              <b>连续天数按每天的总结自动算</b>，换设备也不会丢。
              点「生成今日打卡」会先生成当天的总结，这里随即更新。
            </Callout>
          </div>
        </div>

        {/* ============ 右：连续与历史 ============ */}
        <div>
          <p className="sec-label">
            连续打卡
            <span className="ctag" style={{ marginLeft: 6 }}>
              {dataSource}
            </span>
          </p>
          <div className="card card-pad">
            <div className="streak">
              <span className="n">{streak}</span>
              <span className="meta">
                天连续{longest > 0 ? ` · 近期最长 ${longest} 天` : ""}
              </span>
            </div>
            <div className="dots">
              {(dots.length > 0 ? dots : Array.from({ length: WINDOW_DAYS }, (_, i) => ({ key: `ph${i}`, on: false, today: false }))).map(
                (d) => (
                  <i key={d.key} className={`${d.on ? "on" : ""} ${d.today ? "today" : ""}`.trim()} />
                ),
              )}
            </div>
            <p className="hint">
              {fromServer
                ? `近 ${WINDOW_DAYS} 天。金色 = 已打卡，深金 + 描边 = 今天。数据来自服务端实时计算。`
                : `演示数据 —— 这 ${WINDOW_DAYS} 天里有 ${dots.filter((d) => d.on).length} 天记录。生成一次「每日总结」，这里就开始记你自己的。`}
              相邻判定只看<b>有没有生成那天的总结</b>，不看时长、不算分数。
            </p>
          </div>

          <p className="sec-label" style={{ marginTop: 20 }}>
            历史
          </p>
          <div className="card card-pad">
            {historyRows.length === 0 ? (
              <p className="hint" style={{ marginTop: 0 }}>
                完成第一次打卡后，这里会有记录。
              </p>
            ) : (
              historyRows.map((h, i) => (
                <div className="run" key={h.date} style={i === 0 ? { borderTop: "none" } : undefined}>
                  <span className="when">{h.date}</span>
                  <span>
                    {h.date === todayStr && <span className="ctag gold">今天</span>}{" "}
                    <span className="ctag">{h.points} 个知识点</span>
                  </span>
                  <span className="meta">{h.shared ? "已分享" : "未分享"}</span>
                </div>
              ))
            )}
          </div>

          <div style={{ marginTop: 12 }}>
            <Callout icon="warn">
              <b>不做排行榜和连击奖励</b>：只回答「今天到底学到了什么」。
            </Callout>
          </div>
        </div>
      </div>

      <ToastHost toasts={toasts} />
    </PageShell>
  );
}
