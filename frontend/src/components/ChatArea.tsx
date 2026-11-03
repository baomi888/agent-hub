"use client";

// 中央对话区：书本打开效果 + 欢迎页 + 底部输入框
// 视觉：暖米黄书本风格 · 大圆角 · 柔阴影 · 金色点缀
//
// 2026-09 重构：消息类型/常量/正文渲染/计划卡片/Bubble/Field 拆到 ./chat/*，
// 本文件只保留主组件（顶栏 + 欢迎页 + 消息区 + 输入区）。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import MascotLogo from "./chat/MascotLogo";
import CornIcon from "./CornIcon";
import { api } from "@/lib/api";
import type { RefSource, QuotaInfo } from "@/lib/types";
import { EXAMPLES, PRIMARY_ENTRIES, SECONDARY_ENTRIES, TOOLS_URL } from "./chat/constants";
import { isPlanRequest, stripPlanHint } from "./chat/plan";
import CapToggles, { type CapValue } from "./chat/CapToggles";
import Bubble from "./chat/Bubble";
import BubbleErrorBoundary from "./chat/BubbleErrorBoundary";
import TurnOutline from "./chat/TurnOutline";
import QueryEnhance from "./chat/QueryEnhance";
import GuideModal from "./guide/GuideModal";
import AnnounceModal from "./announce/AnnounceModal";
import FeedbackModal from "./feedback/FeedbackModal";
import type { ChatAttachment, UiMessage } from "./chat/types";
import FilePreviewModal from "./FilePreviewModal";

// 兼容导出：page.tsx 仍从 "@/components/ChatArea" 引入这些符号
export type { UiMessage, ChatAttachment } from "./chat/types";
export { stripPlanHint } from "./chat/plan";

const aid = () => Math.random().toString(36).slice(2, 10);

/**
 * 回答顶边与视口顶部之间留的空隙。
 * 不留会顶到边框上，读第一行时眼睛没有落点；留太多又会显得内容被"推下去"。
 */
const ANCHOR_GAP = 14;

/**
 * 跟随阶段回答底边与视口底部之间留的空隙。
 * 贴死会显得最后一行被裁掉半截，留 18px 读起来有收尾感。
 */
const TAIL_PAD = 18;

/**
 * 锚定后「先让开头留在视口里」的时长（毫秒）。
 *
 * 2026-11-02：原来写死 320ms，实测在手机上几乎等于没有 —— 回答长得比眼睛快，
 * 刚锚好就被下一帧顶走，用户根本来不及确认"这是开头"。1400ms 大约够读完 3 行，
 * 之后才转入跟随。不是越长越好：太长会让"正在写"的反馈断掉。
 */
const ANCHOR_HOLD_MS = 1400;

/**
 * 跟随滚动「追平一段落差」所用的时长（毫秒）。
 *
 * 2026-11-02 第三轮：手机上一行中文约 25~33px，每 0.3~0.4 秒来一行。
 * 原来的「每帧走剩余量 1/3、最多 6 帧」会在 100ms 内把一整行走完然后停 300ms ——
 * 节奏是"弹一下、静一下"，用户原话"一抽一抽"。
 *
 * 现在不管落差多大都用固定时长匀速走完：25px 摊到 280ms，每帧只动 1~2px，
 * 和"下一行什么时候来"基本同拍，观感是内容匀速上移。
 * 取 280ms 而不是更长：再长就跟不上新行，视野会被越拖越远。
 */
const SCROLL_DURATION_MS = 280;

/**
 * 检索条数也换成白话档位。
 * 「topK = 5」没人看得懂，「参考 5 段资料」一看就明白，
 * 顺带把「多参考 = 更全但更慢」的取舍讲清楚。
 */
const TOP_K_MODES = [
  { value: 3, hint: "更快" },
  { value: 5, hint: "均衡" },
  { value: 8, hint: "更全" },
] as const;

/** 顶栏胶囊文案：管理员显示「无限额」，否则「问答 x/y · 向量化 a/b」 */
function quotaLine(q: Record<string, QuotaInfo>): string {
  if (Object.values(q).some((x) => x.unlimited)) return "无限额 · 管理员";
  const fmt = (n: number) => (n === 0 ? "∞" : String(n));
  const parts: string[] = [];
  if (q["ask"]) parts.push(`问答 ${q["ask"].used}/${fmt(q["ask"].limit)}`);
  if (q["embed"]) parts.push(`向量化 ${q["embed"].used}/${fmt(q["embed"].limit)}`);
  return parts.join(" · ") || "用量";
}

interface Props {
  messages: UiMessage[];
  sending: boolean;
  /**
   * 回答是否还在**逐字播放**（网络已结束，打字机还在按 18~50 字/秒往外放）。
   *
   * ★ 为什么要单独传：`sending` 只跟网络请求的生死。1300 字的回答模型 2 秒推完，
   *   播放器还要放 30 秒 —— 这 30 秒里 `sending` 已经是 false，停止键就变回了发送键，
   *   而字还在一个个往外冒。用户点"截断"根本找不到按钮，这就是「截断不可用」的真身。
   */
  playing?: boolean;
  /**
   * 两枚能力开关的当前值（受控）。2026-10-31 批 B：四档模式条下架后，
   * 它成了输入区上方**唯一**的能力入口，所以是必填 —— 它要进请求体
   * （use_kb / use_web），住在本组件内部的 state 里就发不出去了。
   */
  capValue: CapValue;
  /** 开关变化。页面层写进 usePrefs，下一轮请求体生效 */
  onCapChange?: (next: CapValue) => void;
  onSend: (question: string, attachments?: ChatAttachment[], display?: string, plan?: boolean) => void;
  onStop: () => void;
  /** 重新生成：传入目标助手消息 id 与原始问题，覆盖原答案而不是追加 */
  onRetry?: (assistantId: string, question: string) => void;
  /** 轻量反馈出口：复制失败、赞踩等，交给页面出 toast */
  onNotice?: (type: "success" | "error" | "info", msg: string) => void;
  topK: number;
  /** 检索条数变化。资料怎么切片是「建库时」的事，在右侧资料库面板按库设置 */
  onTopKChange: (v: number) => void;
  sessionTitle?: string | null;
  boundKb?: string | null;
  /** 该用户拥有的资料库总数：>0 且当前未绑定时，顶栏给「未绑定」引导提示 */
  kbCount?: number;
  kbNameMap?: Record<string, string>;
  onUnbindKb?: () => void;
  kbPanelOpen?: boolean;
  onToggleKbPanel?: () => void;
  /** 窄屏下打开会话列表抽屉 */
  onOpenSidebar?: () => void;
  /** 外部把建议问题预填进输入框（如联网建库后），不自动发送 */
  prefill?: { text: string; ts: number };
  /** 删除某条消息及其所在的一轮对话（user + 后续 assistant） */
  onDeleteTurn?: (msgDbId: number) => void;
  /**
   * 把某条用户消息收进资料库。
   *
   * 入口长在消息自己的动作排里（不是右栏、不是资料库页）——这个功能最难的地方
   * 不是能不能存，而是用户想不起来它存在。它必须出现在「刚刚说清楚一件事」
   * 的那一刻，而那一刻用户的视线就在自己那条消息下面。
   */
  onSediment?: (msgDbId: number, text: string, role: "user" | "assistant") => void;
  /** 地理定位开关状态（开启后发送消息自动带坐标） */
  geoEnabled?: boolean;
  /** 当前定位城市（角标展示用，可能为 null） */
  geoCity?: string | null;
  /** 定位请求进行中：等授权 + IP 兜底最长要 18 秒，期间按钮禁用并转圈 */
  geoPending?: boolean;
  /** 切换定位开关（请求权限 / 关闭），由页面层处理权限与缓存 */
  onToggleGeo?: () => void;
  /** 手填城市的输入框是否展开（定位拿不到时由页面层直接展开，不弹报错） */
  cityEditOpen?: boolean;
  onCityEditOpenChange?: (v: boolean) => void;
  /** 保存手填的城市；传空串表示撤销手填，回到自动定位 */
  onSaveCity?: (name: string) => void;
  /** 当前会话 id：赞踩要按「会话 + 消息 id」落库，否则后端无从归档 */
  sessionId?: string | null;
  /** 当前账号配额概览：顶栏常驻胶囊用它显示今日用量 */
  quota?: Record<string, QuotaInfo>;
  /** 打开「用量与反馈」看板；不传则不显示顶栏胶囊 */
  onOpenUsage?: () => void;
  /** 当前用户 id：新手引导条的「已关闭」状态按人存，换人登录要重新看到 */
  userId?: string | null;
}

export default function ChatArea({
  messages,
  sending,
  playing = false,
  capValue,
  onCapChange,
  onSend,
  onStop,
  onRetry,
  onNotice,
  topK,
  onTopKChange,
  sessionTitle,
  boundKb,
  kbCount,
  kbNameMap,
  onUnbindKb,
  kbPanelOpen,
  onToggleKbPanel,
  onOpenSidebar,
  prefill,
  onDeleteTurn,
  onSediment,
  geoEnabled = false,
  geoCity = null,
  geoPending = false,
  onToggleGeo,
  cityEditOpen = false,
  onCityEditOpenChange,
  onSaveCity,
  sessionId = null,
  quota,
  onOpenUsage,
  userId = null,
}: Props) {
  const [input, setInput] = useState("");
  const [showParams, setShowParams] = useState(false);
  // 「自己填」展开：想直接敲数字的少数人才会点
  const [advancedTopK, setAdvancedTopK] = useState(false);
  const [pending, setPending] = useState<ChatAttachment[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const attachRef = useRef<HTMLInputElement>(null);
  // 输入历史：↑/↓ 在历史里前后翻，Esc 清空输入框
  // 本地历史用惰性初始值读，不在 effect 里 setState（会触发 react-hooks/set-state-in-effect）
  const [history, setHistory] = useState<string[]>(() => {
    try {
      const raw = typeof localStorage === "undefined" ? null : localStorage.getItem("baomi.history");
      if (!raw) return [];
      const arr = JSON.parse(raw);
      return Array.isArray(arr) ? arr.filter((x) => typeof x === "string").slice(0, 50) : [];
    } catch {
      // 历史是锦上添花，读不到（隐私模式下 localStorage 不可用）不影响主流程
      return [];
    }
  });
  const histIdxRef = useRef(-1); // -1 = 正在编辑草稿
  const draftRef = useRef("");
  const stickRef = useRef(true);
  const [atBottom, setAtBottom] = useState(true);
  /** 已经锚定过顶部的助理消息 id：锚定只做一次，之后回到正常跟随 */
  const anchoredRef = useRef<string | null>(null);
  /** 程序自己设的 scrollTop 目标：onScroll 靠它区分"用户在滚"和"我们在滚" */
  const progTopRef = useRef<number | null>(null);
  /** 平滑滚动的截止时刻：期间不把滚动事件算成用户意图 */
  const smoothUntilRef = useRef(0);
  /** 锚定后的开头保护截止时刻：这段时间内不跟随，让"开头"在视口里停一会儿 */
  const anchorUntilRef = useRef(0);
  /** 锚定落地的 scrollTop：跟随阶段不能比它更靠上，否则回答还没长满一屏开头就被顶走 */
  const anchoredPosRef = useRef<number | null>(null);
  const scrollRafRef = useRef<number | null>(null);
  /** 我们最后一次把 scrollTop 设成的值。onScroll 用它认领"这是我们自己滚的" ——
   *  比时间窗可靠：手机上 rAF 慢、scroll 事件有延迟，时间窗经常开错。 */
  const appliedTopRef = useRef<number | null>(null);
  /** 消息末尾的留白元素：让"回答不足一屏"时也滚得到顶部（详见下方 effect） */
  const tailRef = useRef<HTMLDivElement | null>(null);
  // 从引用角标打开的全文预览（文件名来自 RefSource，kb 用当前会话绑定的）
  const [previewFile, setPreviewFile] = useState<string | null>(null);
  // 顶栏「反馈」弹窗。**必须是 useCallback**：FeedbackModal 的 Esc / 焦点 effect
  // 依赖 onClose，每次渲染都换引用会让 effect 反复重跑、把焦点从文本框里抢回来。
  const [fbOpen, setFbOpen] = useState(false);
  const closeFeedback = useCallback(() => setFbOpen(false), []);
  // 联网建库后把建议问题预填进输入框：渲染期基于 prop 调整 state（用 state 去重，避免 ref 在渲染期被读写），
  // 不自动发送，让用户确认/补充后再发
  const [appliedPrefillTs, setAppliedPrefillTs] = useState<number>(-1);
  if (prefill && prefill.ts !== appliedPrefillTs) {
    setAppliedPrefillTs(prefill.ts);
    setInput(prefill.text);
  }

  // 高度自适应统一在这里做：历史回填时也会跟着变高
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 160) + "px";
  }, [input]);

  // 进来就能打字：省掉"先点一下输入框"这一步
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // 预填建议问题后把焦点送进输入框（仅副作用，不在此 setState）
  useEffect(() => {
    if (!prefill) return;
    inputRef.current?.focus();
  }, [prefill]);

  // 按时段问候：SSR 与水合各算一次可能跨过整点，首帧用安全默认值，挂载后再校准
  const [greeting, setGreeting] = useState("你好呀");
  useEffect(() => {
    const h = new Date().getHours();
    setGreeting(
      h < 6 ? "夜深了" : h < 11 ? "早上好" : h < 13 ? "中午好" : h < 18 ? "下午好" : "晚上好"
    );
  }, []);

  const pendingImageCount = pending.filter((a) => a.type.startsWith("image/")).length;
  const pendingOtherCount = pending.length - pendingImageCount;

  // capValue 是受控 props（页面层持有）：它要进请求体，住在这里就发不出去。
  // 输入框下面那行提示跟着它变 —— 这是四档模式条下架后用户唯一能看到的
  // "这一轮会怎么答"的说明，所以两枚开关的四种组合都要说清楚。
  // 每句都控制在 9 字以内：这行右侧还钉着「Enter 发送 · ↑ 上一条 · Esc 清空」，
  // 390px 下超了就被截成「…」（第一版「先翻你的资料库，不够再上网」就被截）
  const capHint = capValue.kb
    ? capValue.web
      ? "先查资料，再联网"
      : "只查你的资料库"
    : capValue.web
      ? "会联网查最新信息"
      : "都不勾＝纯对话";

  // 程序滚动：同一帧里多次触发只落一次，且把目标值记下来供 onScroll 认领
  //
  // ★ 2026-11-02 第二次重做（手机端「还是在底部一抽一抽」）：
  //   上一版是「每帧走剩余量的 1/3，最多 6 帧后直接 snap 到位」。线上采样暴露了问题：
  //   一行中文约 33px，先在 ~100ms 内一口气走完 6 帧，然后停 300ms 等下一行 ——
  //   节奏是「弹一下、静一下、弹一下」。桌面一行字多（~40 字），0.9 秒才弹一次，
  //   察觉不到；手机一行只有 21 字，0.4 秒弹一次，就成了"一抽一抽"。
  //   实测位移分布：一共 732 个 50ms 采样点，只有 108 个点在动（15%），
  //   动的那些中位 16.5px、90 分位 25px —— 典型的"脉冲式"位移。
  //
  //   现在的做法：**匀速追平**。不管落差是 25px 还是 800px，都在固定的
  //   SCROLL_DURATION_MS 内以恒定速度走完 —— 每帧的步长完全一样，
  //   视觉上是一条匀速上移的带子，而不是"起步一冲、尾巴拖沓"。
  //
  //   ★ 为什么不用指数缓动（试过，不行）：指数缓动第一帧就走掉剩余量的 20%，
  //     25px 的一行会变成「100ms 内冲 20px，然后 3px、1px」—— 还是起步一顿。
  //     实测缓动版：25px 分成 20/3/1 三段；匀速版每一帧都是 1~2px。
  //   ★ 为什么必须按时间算、不能按帧数算：手机 60fps、ProMotion 120fps、
  //     无头浏览器甚至 240fps，同一个"每帧走固定比例"在不同设备上速度差 4 倍。
  //     （这条是踩出来的：探针里量到帧间隔中位 4.2ms ≈ 240fps。）
  const programScroll = useCallback((el: HTMLDivElement, top: number) => {
    const max = Math.max(0, el.scrollHeight - el.clientHeight);
    const target = Math.max(0, Math.min(top, max));
    progTopRef.current = target;
    // 已经在追赶：让循环自己读最新的 progTopRef，不要重排（重排会把
    // 走了一半的位移丢掉，反而更抖）
    if (scrollRafRef.current != null) return;

    let lastT = 0;
    const tick = (now: number) => {
      scrollRafRef.current = null;
      const goal = progTopRef.current;
      if (goal == null) return;
      // 追赶途中用户可能已经接管了（手指比 rAF 快），这时立刻让开
      if (!stickRef.current) return;
      // 首帧没有上一帧时间，按 16ms 估；切后台再回来 dt 可能上千毫秒，
      // 夹到 64ms 免得一下子跳过头
      const dt = lastT ? Math.min(now - lastT, 64) : 16;
      lastT = now;
      const cur = el.scrollTop;
      const diff = goal - cur;
      if (Math.abs(diff) <= 0.6) {
        el.scrollTop = goal;
        appliedTopRef.current = el.scrollTop;
        return;
      }
      // 速度 = 剩余量 / 固定时长；落差越大走得越快，但**全程匀速**。
      // 下限 0.02px/ms 只为避免极小落差拖太久。
      const speed = Math.max(0.02, Math.abs(diff) / SCROLL_DURATION_MS);
      const step = Math.sign(diff) * Math.min(Math.abs(diff), dt * speed);
      el.scrollTop = cur + step;
      // 记下"我们刚把它设成了多少"，onScroll 靠这个认领自己的位移。
      // 比时间窗可靠：手机上 rAF 慢、scroll 事件有延迟，时间窗经常开错。
      appliedTopRef.current = el.scrollTop;
      scrollRafRef.current = requestAnimationFrame(tick);
    };
    scrollRafRef.current = requestAnimationFrame(tick);
  }, []);

  // 尾部留白：让「回答不足一屏」时也滚得到顶部（2026-11-02 重做版）
  //
  // 为什么必须有它：内容总高不足一屏时 maxScrollTop 太小，把回答顶边锚到视口顶
  // 会被浏览器 clamp 掉 —— 结果回答只能贴在视口最下方一字一字长出来，
  // 用户看到的就是"回答一直在屏幕最下面"。实测（390×844）：回答 121px 时
  // 顶边停在 y=505（视口 580），而不是应有的 y=75。
  //
  // ★ 为什么上一版会"一抽一抽"、这一版不会：
  //   上一版在渲染 effect 里每帧 `getBoundingClientRect()` 量一次气泡高度。
  //   DOM 还没更新时量到旧值或 0，tail 就瞬间跳几百像素（实测单次 517px）；
  //   tail 一改 scrollHeight，浏览器立刻把 scrollTop 夹回去 —— 这就是抽搐。
  //   这版改用 ResizeObserver：只在气泡**尺寸真的变了**之后才回调，量的是新值。
  //   于是「气泡高度 + 留白 = 一屏」恒成立，scrollHeight 恒定，scrollTop 不动。
  //
  //   效果：回答在下方"填满"这段留白，顶边稳稳停在视口顶部，一直到写满一屏
  //   才开始跟随 —— 正是豆包那种从上往下淌的观感。
  const lastMsg = messages[messages.length - 1];
  const lastId = lastMsg?.id ?? "";
  const lastStreaming = !!lastMsg?.streaming;
  useEffect(() => {
    const el = scrollRef.current;
    const tail = tailRef.current;
    if (!el || !tail) return;
    const node =
      lastStreaming && lastId
        ? el.querySelector<HTMLElement>(`[data-mid="${lastId}"]`)
        : null;
    if (!node) {
      tail.style.height = "0px";
      return;
    }
    const apply = () => {
      const bh = node.getBoundingClientRect().height;
      const h = Math.max(0, Math.round(el.clientHeight - bh));
      if (Math.abs((parseFloat(tail.style.height) || 0) - h) > 1) {
        tail.style.height = `${h}px`;
      }
    };
    apply();
    const ro = new ResizeObserver(apply);
    ro.observe(node);
    return () => ro.disconnect();
    // 只在「哪一条在流式」变化时重建，内容增长交给 ResizeObserver ——
    // 依赖 messages 会让每个 token 都重建一次 observer。
  }, [lastId, lastStreaming]);

  // 新消息 / 新一段文字进来时的视野策略（2026-11-01 重写）
  //
  // 改动前是每来一个字 `scrollTop = scrollHeight` —— 实测 131 个采样点里 130 个
  // 「距底 ≤2px」。1080×629 的窗口下可视区只有 350px，一条 953px 的回答从第一个字
  // 开始，顶边就在视口上方 185px：用户从头到尾只看得见最后一行。这不是流式坏了，
  // 是视野落点错了。
  //
  // 现在的策略（对齐豆包）：
  //   ① 一条回答的**第一个字**出现时，把这条气泡的顶边放到视口顶部（留 14px），
  //      而不是贴底 —— 贴底意味着第 1 行永远落在视口最下方，越写越往上跑。
  //      回答不足一屏时靠尾部留白（见上方 effect）补出滚动余量，同样能锚到顶。
  //   ② 锚完先停 ANCHOR_HOLD_MS，让开头在视口里待够，再转入跟随；
  //   ③ 跟随阶段让回答底边贴视口底，文字由 typewriter 按阅读速度往外放；
  //   ④ 用户一旦上滚（stickRef=false）就彻底冻结，视野归他。
  //
  // 空态（欢迎页）仍然跳过：欢迎页比可视区高，滚到底会把标题推出视口。
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (messages.length === 0) {
      anchoredRef.current = null;
      anchoredPosRef.current = null;
      programScroll(el, 0);
      return;
    }
    if (!stickRef.current) return;

    const last = messages[messages.length - 1];
    // ★ 只有「正在流式」的那条才锚定（2026-11-02 第四次修正）。
    //   之前只看"有文字"，打开会话时会对**最后一条历史回答**也锚一次，
    //   scrollTop 从 0 直接甩到 25231 —— 用户看到的就是"一打开就跳到底部"。
    //   历史消息的落点由下面的"切会话回到底部"逻辑负责，不该走锚定。
    const hasText =
      last?.role === "assistant" && last.streaming && (last.content ?? "").length > 0;
    if (hasText && anchoredRef.current !== last.id) {
      const node = el.querySelector<HTMLElement>(`[data-mid="${last.id}"]`);
      if (node) {
        // ★ 标记必须放在"确认拿到节点"之后（2026-11-02）：
        //   原来先设 anchoredRef 再找节点，第一个 token 那一帧如果 DOM 还没挂上
        //   （querySelector 返回 null），这次锚定机会就**永久丢掉**了 ——
        //   后续每一帧都因为 id 相同而跳过锚定，回答只能贴在最底下长出来。
        anchoredRef.current = last.id;
        // ★ 先把留白**同步**补上，再算滚动范围（2026-11-02 关键修复）。
        //   留白平时由 ResizeObserver 异步设置，第一个 token 落地那一帧它还是 0，
        //   于是这一刻的 scrollHeight 少了一段，max 不够，锚定被 clamp ——
        //   实测回答顶停在 y=503（视口 580）而不是 y=76，看起来就是"贴着最底下写"。
        //   写 style 后立刻读 scrollHeight 会强制一次同步 layout，取到的就是新值。
        const tailEl = tailRef.current;
        if (tailEl) {
          const th = Math.max(
            0,
            Math.round(el.clientHeight - node.getBoundingClientRect().height)
          );
          tailEl.style.height = `${th}px`;
        }
        const rect = node.getBoundingClientRect();
        const elRect = el.getBoundingClientRect();
        const max = Math.max(0, el.scrollHeight - el.clientHeight);
        // ★ 用「相对当前位置的增量」而不是「绝对 scrollTop」（2026-11-02 第三次修正）。
        //   绝对值的算法依赖 rel 与 max 的精确对齐，tail 是异步写入的，
        //   差一截就被 clamp，实测偏移 428px（回答顶停在 504 而不是 76）。
        //   增量只关心"回答顶边现在在视口哪儿、要上移多少"，不受 scrollHeight 影响。
        const delta = rect.top - elRect.top - ANCHOR_GAP;
        const target = Math.max(0, Math.min(el.scrollTop + delta, max));
        if (typeof window !== "undefined") {
          (window as unknown as Record<string, unknown>).__anchorDbg = {
            rectTop: Math.round(rect.top),
            elTop: Math.round(elRect.top),
            delta: Math.round(delta),
            from: Math.round(el.scrollTop),
            to: Math.round(target),
            max,
            sh: el.scrollHeight,
            ch: el.clientHeight,
            tail: tailEl ? tailEl.style.height : "no-tail",
          };
        }
        // ★ 必须同步落地，不能排进 rAF：下一个 token 通常 16ms 内就到，
        //   进来又会调 programScroll 把排队的这一帧 cancel 掉 ——
        //   实测就是这样：锚定永远被取消，用户看到的仍是 -113px 的末尾。
        progTopRef.current = target;
        if (scrollRafRef.current != null) {
          cancelAnimationFrame(scrollRafRef.current);
          scrollRafRef.current = null;
        }
        el.scrollTop = target;
        // 同上：这一下也是我们自己设的，onScroll 必须认得，否则锚定刚落地的
        // scroll 事件会把 stick 关掉，后面每个字都不再跟随。
        appliedTopRef.current = el.scrollTop;
        anchoredPosRef.current = target;
        // 开头护住一会儿：内容长得比视线快，刚锚好就被下一帧顶走的话，
        // 用户连"这行是开头"都没看清。
        anchorUntilRef.current = performance.now() + ANCHOR_HOLD_MS;
        return;
      }
    }
    // 还没到跟随时间：让开头留在视口里
    if (performance.now() < anchorUntilRef.current) return;

    // 跟随：目标是让这条回答的**底边**贴住视口底部，而不是无脑滚到 scrollHeight。
    //
    // ★ 为什么不能用 scrollHeight：内容末尾还有父容器 padding，滚到 scrollHeight 等于
    //   比锚定位还往下多走十几像素 —— 实测锚定刚落地的 y=14，两个采样点后就变成
    //   -40，看着还是"从半截开始读"。改用"气泡底边对齐视口底"后，回答不足一屏时
    //   这个值小于锚定位，被 Math.max 挡住，开头就钉在视口顶部不动。
    const node = last ? el.querySelector<HTMLElement>(`[data-mid="${last.id}"]`) : null;
    if (node) {
      const box = node.getBoundingClientRect();
      const elBox = el.getBoundingClientRect();
      const bottom = box.bottom - elBox.top + el.scrollTop;
      const want = bottom - el.clientHeight + TAIL_PAD;
      const floor = anchoredPosRef.current ?? 0;
      programScroll(el, Math.max(floor, want));
      return;
    }
    programScroll(el, el.scrollHeight);
  }, [messages, programScroll]);

  // 切换会话（首条消息换了）时强制回到底部，否则会停在上一会话的滚动位置
  const firstId = messages[0]?.id ?? "";
  const prevFirstRef = useRef(firstId);
  useEffect(() => {
    if (firstId === prevFirstRef.current) return;
    prevFirstRef.current = firstId;
    stickRef.current = true;
    anchoredRef.current = null;
    anchoredPosRef.current = null;
    setAtBottom(true);
    const el = scrollRef.current;
    if (el) programScroll(el, el.scrollHeight);
  }, [firstId, programScroll]);

  const onScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    // ① 这就是我们上一帧刚设进去的值 —— 认领，直接返回。
    //   ★ 原来判的是「落点离目标 <= 4px」，但平滑追赶途中 scrollTop 离目标
    //     还有几十像素，判定必然失败 → 被当成"用户接管" → cancelAnimationFrame
    //     把正在跑的动画掐掉。手机上 rAF 慢，动画本来就跑不完，于是每次都是
    //     「挪一点 → 被掐 → 攒够再挪一点」，这就是底部那种一抽一抽。
    //     改成比对"我们设过的值"，和动画进行到哪一步无关。
    const applied = appliedTopRef.current;
    if (applied != null && Math.abs(el.scrollTop - applied) <= 2) return;

    // 平滑滚动途中（点「回到最新」）：朝目标靠近的过程不该算成"用户接管"，
    // 否则按钮会在中途闪一下又冒出来
    if (smoothUntilRef.current && performance.now() < smoothUntilRef.current) {
      const goal = progTopRef.current ?? 0;
      if (Math.abs(el.scrollTop - goal) <= 2) {
        smoothUntilRef.current = 0;
        progTopRef.current = null;
      }
      return;
    }

    // ② 落点和我们设的对不上 = 用户真的动了。把还排着的程序滚动撤掉，
    //    否则它会在 16ms 后把人按回原处（上滚 300px 被拽回去就是这么来的）。
    appliedTopRef.current = null;
    progTopRef.current = null;
    if (scrollRafRef.current != null) {
      cancelAnimationFrame(scrollRafRef.current);
      scrollRafRef.current = null;
    }
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    stickRef.current = near;
    setAtBottom((prev) => (prev === near ? prev : near));
  }, []);

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    stickRef.current = true;
    setAtBottom(true);
    const target = Math.max(0, el.scrollHeight - el.clientHeight);
    progTopRef.current = target;
    smoothUntilRef.current = performance.now() + 700;
    el.scrollTo({ top: target, behavior: "smooth" });
  }, []);

  // ---- 对话目录（跳回前面某一问）----
  // 只把「用户提问」当锚点：回答归到它上面那一问，和 DeepSeek 的目录一致。
  const turns = useMemo(
    () =>
      messages
        .filter((m) => m.role === "user")
        .map((m, i) => ({
          mid: m.id,
          n: i + 1,
          text: (m.content || "").replace(/\s+/g, " ").trim() || "（附件提问）",
        })),
    [messages]
  );
  // 刻度位置随内容变化，流式期间每来一批 token 都得重量（组件内防抖 120ms）
  const outlineKey = `${messages.length}:${messages[messages.length - 1]?.content?.length ?? 0}`;

  const jumpToTurn = useCallback((mid: string) => {
    const el = scrollRef.current;
    if (!el) return;
    const node = el.querySelector<HTMLElement>(`[data-mid="${mid}"]`);
    if (!node) return;
    const top = node.getBoundingClientRect().top - el.getBoundingClientRect().top + el.scrollTop;
    const target = Math.max(0, Math.min(top - ANCHOR_GAP, el.scrollHeight - el.clientHeight));
    // ★ 先把「跟随」关掉再滚：回答还在流式时，跟随逻辑每来一个字就会把视野拽回底部,
    //   不关的话用户刚跳上去立刻被拉下来 —— 这正是最容易回归的地方。
    stickRef.current = false;
    anchoredRef.current = null;
    anchorUntilRef.current = 0;
    if (scrollRafRef.current != null) {
      cancelAnimationFrame(scrollRafRef.current);
      scrollRafRef.current = null;
    }
    progTopRef.current = null;
    // 清掉「上一帧是我们设的」那条豁免，让 onScroll 按新位置重算 stick
    appliedTopRef.current = null;
    el.scrollTo({ top: target, behavior: "smooth" });
  }, []);

  const addFiles = (files: FileList | File[]) => {    const arr = Array.from(files);
    if (arr.length === 0) return;
    setPending((prev) => {
      const next = [...prev];
      for (const f of arr) {
        if (next.some((a) => a.name === f.name && a.size === f.size)) continue;
        next.push({
          id: aid(),
          name: f.name,
          type: f.type || "application/octet-stream",
          size: f.size,
          url: URL.createObjectURL(f),
          file: f,
        });
      }
      return next;
    });
  };

  const removeAttachment = (id: string) => {
    setPending((prev) => {
      const t = prev.find((a) => a.id === id);
      if (t) URL.revokeObjectURL(t.url);
      return prev.filter((a) => a.id !== id);
    });
  };

  const pushHistory = (q: string) => {
    if (!q) return;
    setHistory((prev) => {
      const next = [q, ...prev.filter((x) => x !== q)].slice(0, 50);
      try {
        localStorage.setItem("baomi.history", JSON.stringify(next));
      } catch {
        /* 存不下就不持久化，本次会话内仍可用 */
      }
      return next;
    });
  };

  // dir = -1 往上翻（更早/更新的一条），dir = 1 往下翻回草稿
  const recallHistory = (dir: -1 | 1) => {
    if (history.length === 0) return;
    let idx = histIdxRef.current;
    if (dir === -1) {
      if (idx === -1) {
        draftRef.current = input; // 第一次上翻，先把草稿留住
        idx = 0;
      } else if (idx < history.length - 1) {
        idx += 1;
      } else {
        return;
      }
    } else {
      if (idx <= -1) return;
      idx -= 1; // 回到 -1 即草稿
    }
    histIdxRef.current = idx;
    setInput(idx === -1 ? draftRef.current : history[idx]);
    // 回填后把光标放到末尾，否则长文本会停在开头
    requestAnimationFrame(() => {
      const el = inputRef.current;
      if (!el) return;
      el.setSelectionRange(el.value.length, el.value.length);
    });
  };

  const submit = () => {
    const q = input.trim();
    if ((!q && pending.length === 0) || sending) return;
    const atts = pending;
    setInput("");
    setPending([]);
    pushHistory(q);
    histIdxRef.current = -1;
    draftRef.current = "";
    // 自己发的内容一定要看到，重置为贴底跟随
    stickRef.current = true;
    setAtBottom(true);
    // 计划类请求：只传标志位，格式要求由后端在拼好对话历史之后注入。
    // （前端改写 question 文本会让当前轮丢上下文，并把格式块写进历史污染后续轮次。）
    onSend(q, atts, undefined, isPlanRequest(q));
  };

  const fillExample = (q: string) => {
    setInput(q);
    inputRef.current?.focus();
  };

  // 进料路径②：让它带着题目去联网研究——光标留在末尾，等用户补上题目
  const startResearch = () => {
    setInput("帮我研究一下：");
    requestAnimationFrame(() => {
      const el = inputRef.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
    });
  };

  // 进料路径③：用户自己给资料——把资料库面板推到他面前
  const openUpload = () => {
    if (kbPanelOpen) return;
    onToggleKbPanel?.();
  };

  // 首屏功能入口：模板广场 / 校园工具集 / 上传建库 / 让它研究
  const handleIntroEntry = (key: string) => {
    switch (key) {
      case "template":
        window.location.href = "/templates";
        break;
      case "tools":
        window.open(TOOLS_URL, "_blank", "noopener,noreferrer");
        break;
      case "upload":
        openUpload();
        break;
      case "research":
        startResearch();
        break;
    }
  };

  const handleForward = useCallback((text: string) => {
    setInput(text);
    inputRef.current?.focus();
  }, []);

  // 用 useCallback 固定引用，否则每次渲染都会让 Bubble 的 memo 失效
  const handleCopyFail = useCallback(
    () => onNotice?.("error", "复制失败，请手动选择文本"),
    [onNotice]
  );
  // 赞踩落库：失败不影响界面（本地高亮已经点了），只提示一声让用户知道没存上
  const handleFeedback = useCallback(
    async (msgDbId: number, rating: "up" | "down", comment?: string) => {
      if (!sessionId) return;
      try {
        await api.sendFeedback(sessionId, msgDbId, rating, comment);
        onNotice?.(
          "success",
          rating === "up"
            ? "已记录：这条回答有帮助"
            : comment
              ? "已记录：谢谢你的补充，我会用来看板里复盘"
              : "已记录：这条回答不太行"
        );
      } catch {
        onNotice?.("error", "反馈没能保存，稍后可再点一次");
      }
    },
    [sessionId, onNotice]
  );
  // 从引用角标跳到全文预览：引用块只给文件名，kb 用当前会话绑定的那个
  const openPreviewFile = useCallback((source: string) => setPreviewFile(source), []);

  /**
   * 只有真会走检索的模式才提示"检索词会被怎么理解"。
   * llm 模式不检索、工具路径走工具调用，在它们下面提示换词是纯粹的噪音。
   * auto 目前按"有没有绑库"估：绑了库时后端会强制检索（含闲聊），
   * 没绑库只可能走工具或直接答，不会检索。判断口径见 core/router.py。
   */
  // 检索词校准面板只在「这一轮真的会检索」时出现：
  // 开了开关 + 会话确实绑了库。只判断开关不够 —— 没绑库时检索必然空，
  // 提前让用户校准检索词是空承诺。
  const willRetrieve = capValue.kb && !!boundKb;

  return (
    <section className="book-spine flex h-full min-w-0 flex-1 flex-col">
      {/* 顶栏 */}
      <div className="flex items-center gap-2 border-b border-line px-5 py-3">
        {onOpenSidebar && (
          <button
            onClick={onOpenSidebar}
            title="打开会话列表"
            aria-label="打开会话列表"
            className="icon-btn hamburger -ml-1 shrink-0"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M3 6h18M3 12h18M3 18h18" />
            </svg>
          </button>
        )}
        <span className="truncate font-serif text-sm font-medium text-ink">
          {sessionTitle ?? "新对话"}
        </span>
        {boundKb ? (
          <span
            className="kb-chip min-w-0 max-w-[44vw]"
            title={kbNameMap?.[boundKb] ?? boundKb}
          >
            <span className="dot" />
            <span className="kb-chip-name min-w-0 truncate">
              已绑定：{kbNameMap?.[boundKb] ?? boundKb}
            </span>
            {onUnbindKb && (
              <button
                onClick={onUnbindKb}
                title="解绑知识库"
                aria-label="解绑知识库"
                className="kb-unbind ml-0.5 text-faint transition hover:text-red"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" className="h-3 w-3" aria-hidden="true">
                  <path d="M6 6l12 12M18 6L6 18" />
                </svg>
              </button>
            )}
          </span>
        ) : (kbCount ?? 0) > 0 && onToggleKbPanel ? (
          // P3：手上有资料库却没绑到本会话 —— 必须说出来，否则用户以为资料"丢了"
          // （线上复现：传过资料的库还在，模型却说当前会话没绑定）。
          // 一个库都没有时不提示：没东西可绑，提示只会变成噪音。
          <button
            type="button"
            onClick={onToggleKbPanel}
            className="kb-chip kb-chip-unbound min-w-0 max-w-[44vw]"
            title="当前会话未绑定资料库，点此从右侧选择"
            aria-label="当前会话未绑定资料库，点此选择"
          >
            <span className="kb-chip-name min-w-0 truncate">
              未绑定资料库 · 点此选择
            </span>
          </button>
        ) : null}
        <div className="flex-1" />
        {/* 顶栏用量入口：常驻显示今日额度，点开看板。
            侧栏那个入口太折叠，用户找不到——这里给一个一眼可见的。 */}
        {onOpenUsage && (
          <button
            type="button"
            onClick={onOpenUsage}
            className="usage-chip"
            title="查看用量与反馈"
            aria-label="查看用量与反馈"
          >
            <CornIcon name="target" className="h-3.5 w-3.5 shrink-0" title="用量" />
            <span className="usage-chip-text">{quota ? quotaLine(quota) : "用量"}</span>
          </button>
        )}
        {/* 顶栏「能力」快捷弹层与它的触发按钮已在 2026-10-31 批 B 删除：
            它提供的是「切模式 + 预填示例」，而模式条已下架、能力由两枚开关表达。
            留着它等于教用户点一个已经不存在的概念（还带着"算数"这种已删工具）。 */}
        {onToggleKbPanel && (
          <button
            onClick={onToggleKbPanel}
            title={kbPanelOpen ? "收起资料库" : "展开资料库"}
            aria-label={kbPanelOpen ? "收起资料库" : "展开资料库"}
            aria-expanded={!!kbPanelOpen}
            className="icon-btn"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              {kbPanelOpen ? (
                <>
                  <path d="M9 18V5l12-2v13" />
                  <circle cx="6" cy="18" r="3" />
                  <circle cx="18" cy="16" r="3" />
                </>
              ) : (
                <>
                  <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
                  <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
                </>
              )}
            </svg>
          </button>
        )}
      </div>

      {/* 消息区 */}
      <div className="relative min-h-0 flex-1">
        {/* overflow-x-hidden 是兜底：一长串不可断的字符（文件名、URL、表格）
            只要把子元素顶宽，移动端整页就能横向拖动、观感像“画幅歪了”。
            宁可在这里裁掉，也不许把文档撑宽。

            [overflow-anchor:none] 关掉浏览器的「滚动锚定」：流式时内容每帧都在长，
            浏览器会自作主张微调 scrollTop 来保持可见位置，和我们自己的平滑滚动互相拉扯，
            也是手机端抖动的来源之一。 */}
        <div
          ref={scrollRef}
          onScroll={onScroll}
          className="h-full overflow-y-auto overflow-x-hidden [overflow-anchor:none]"
        >
          <div className="chat-measure mx-auto flex min-h-full flex-col px-6 pb-4 pt-6">
          {messages.length === 0 ? (
            // 欢迎页：主路径是「直接问」，首屏摆两块——
            // ① 功能入口（持续陪伴三卡 + 一次性动作小胶囊）② 三句示例
            // （场景快选已撤出主界面：场景化入口统一走模板广场，10-14 晚）
            <div className="welcome">
              <MascotLogo
                className="hero-logo mx-auto mb-2 h-[52px] w-[52px] object-contain"
                mood={messages.some((m) => m.streaming) ? "thinking" : "idle"}
                tiltKey={`${capValue.kb ? 1 : 0}${capValue.web ? 1 : 0}`}
              />
              <div className="eyebrow mb-1.5">苞米地 · 智能体工作台</div>
              <h1 className="hero-title font-serif font-bold leading-snug text-ink">
                {greeting}，我是<span className="hero-name">知穗</span>
              </h1>
              {/* 25 字在 390 宽下会折成两行、第二行只剩一个「你。」——
                  收到 14 字就单行放下了（390 - 48 padding = 342px，14×15=210px）。 */}
              <p className="welcome-sub mt-1.5 max-w-[30rem] text-msg leading-relaxed text-muted">
                一句话就行，我来判断怎么帮你
              </p>

              {/* 原来的「第一次来？三步就能用起来」引导条（OnboardBar）已删除：
                  它和首次进入的上手说明弹窗（GuideModal）讲的是同一件事，
                  新用户先看弹窗、关掉又看到一张三步卡，属于重复告知；
                  而老用户早就不需要它。删掉后 390×844 上首页不再需要滚动，
                  三张主线卡和输入框能同屏。 */}

              {/* ② 主线：三个持续陪伴功能。原来这里是四宫格，四件平级，
                  结果「模板广场」这种一次性动作和「每日委托」这种天天用的能力
                  一样醒目 —— 而知穗的差别恰恰在后面那类。改成两层之后，
                  主线是大卡 + 有标题栏，辅路缩成一行小胶囊。 */}
              <div className="welcome-daily mt-4 w-full">
                <div className="welcome-section-head">
                  <span className="eyebrow">让知穗每天做点什么</span>
                  <span className="rule" />
                </div>
                <div className="mt-2.5 grid w-full grid-cols-1 gap-2.5 sm:grid-cols-3">
                  {PRIMARY_ENTRIES.map((e) => (
                    <Link
                      key={e.href}
                      href={e.href}
                      className="entry-card"
                      aria-label={`${e.label}：${e.hint}`}
                    >
                      <span className="ec-icon">
                        <CornIcon name={e.icon} className="h-6 w-6" title={e.label} />
                      </span>
                      {/* 横排：图标在左、文字在右。label 与 hint 必须包成一列，
                          否则三个 span 会并排站成三栏（横排改造时踩过）。 */}
                      <span className="ec-text">
                        <span className="ec-label">{e.label}</span>
                        <span className="ec-hint">{e.hint}</span>
                      </span>
                    </Link>
                  ))}
                </div>
              </div>

              {/* ③ 辅路：一次性动作，收成一行小入口 */}
              <div className="welcome-mini mt-2.5 flex w-full flex-wrap justify-center gap-2">
                {SECONDARY_ENTRIES.map((e) => (
                  <button
                    key={e.key}
                    type="button"
                    onClick={() => handleIntroEntry(e.key)}
                    className="entry-mini"
                    title={e.label}
                  >
                    <CornIcon name={e.icon} className="h-4 w-4" />
                    {e.label}
                  </button>
                ))}
              </div>

              {/* ③ 次级示例：三句能立刻点的话，三列等宽便签；min-w-0 让长句在卡内截断不溢出
                  矮屏（≤700px 高）整行让位：一屏放得下「品牌 + 三张日更大卡」比多摆三句示例更值 */}
              <div className="welcome-egs mt-3 grid w-full grid-cols-1 gap-2 sm:grid-cols-3">
                {EXAMPLES.map((e) => (
                  <button
                    key={e.q}
                    onClick={() => fillExample(e.q)}
                    className="welcome-eg"
                    title={e.q}
                  >
                    <span className="welcome-eg-hint">{e.hint}</span>
                    <span className="q">{e.q}</span>
                  </button>
                ))}
              </div>

              <p className="welcome-foot">问完的每一句，都能翻回原文。</p>
            </div>
          ) : (
            <div className="space-y-6">
              {messages.map((m, idx) => {
                let userQ: string | undefined;
                if (m.role === "assistant") {
                  for (let i = idx - 1; i >= 0; i--) {
                    if (messages[i].role === "user") {
                      userQ = messages[i].content;
                      break;
                    }
                  }
                }
                return (
                  // 错误边界：一条坏消息渲染抛错时只降级这一条，不白屏整个会话
                  // data-mid：滚动锚定要按 id 找到这条气泡的顶边，没有它只能贴底
                  <div key={m.id} data-mid={m.id} className="min-w-0">
                    <BubbleErrorBoundary>
                      <Bubble
                        m={m}
                        userQuestion={userQ}
                        onForward={handleForward}
                        onCopyFail={handleCopyFail}
                        onFeedback={handleFeedback}
                        onRetry={onRetry}
                        onPreviewFile={boundKb ? openPreviewFile : undefined}
                        onDeleteTurn={onDeleteTurn}
                        onSediment={onSediment}
                      />
                    </BubbleErrorBoundary>
                  </div>
                );
              })}
              {/* 尾部留白：高度 = 一屏 − 本条回答高度，只在流式且不足一屏时给。
                  没有它，回答不足一屏就没有滚动余量，只能贴在视口最下方 ——
                  用户看到的就是"回答一直在屏幕最下面长出来"。 */}
              <div className="chat-tail" ref={tailRef} aria-hidden="true" />
            </div>
          )}
          </div>
        </div>
        {/* 对话目录：桌面走右侧页边刻度，手机走右下胶囊 + 半屏目录。
            放在滚动容器外面 —— 它要贴住视口，不能跟着内容滚走 */}
        <TurnOutline turns={turns} scrollRef={scrollRef} onJump={jumpToTurn} layoutKey={outlineKey} />
        {!atBottom && (
          <button
            type="button"
            onClick={scrollToBottom}
            className={messages.some((m) => m.streaming) ? "scroll-down is-live" : "scroll-down"}
            aria-label="回到最新消息"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M12 5v14" />
              <path d="M19 12l-7 7-7-7" />
            </svg>
            {/* 还在生成时先说"正在生成"：用户上滚之后就冻结了，不告诉他有新内容
                长在下面，他会以为卡住了 */}
            <span>{messages.some((m) => m.streaming) ? "正在生成" : "回到最新"}</span>
          </button>
        )}
      </div>

      {/* 检索设置：只留「参考几段资料」。
          切片参数是建库时的事，已经移到右侧资料库面板按库单独设置 */}
      {showParams && (
        <div className="chat-measure mx-auto px-6 pb-2">
          <div className="fade-up rounded-xl border border-line-strong bg-panel p-4">
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-sm font-medium text-ink">每次回答参考几段资料？</span>
              <button
                type="button"
                onClick={() => setAdvancedTopK((a) => !a)}
                aria-expanded={advancedTopK}
                className="shrink-0 text-xs text-faint transition hover:text-ink"
              >
                {advancedTopK ? "收起" : "自己填"}
              </button>
            </div>

            <div className="mt-2 grid grid-cols-3 gap-2" role="group" aria-label="参考资料段数">
              {TOP_K_MODES.map((m) => {
                const on = topK === m.value;
                return (
                  <button
                    key={m.value}
                    type="button"
                    onClick={() => onTopKChange(m.value)}
                    aria-pressed={on}
                    className={`flex flex-col items-center gap-px rounded-lg border py-2 transition ${
                      on
                        ? "border-accent bg-accent-soft"
                        : "border-line bg-bg hover:border-line-strong hover:bg-fill-2"
                    }`}
                  >
                    <span className="text-sm font-medium text-ink">{m.value} 段</span>
                    <span className="text-fs-badge text-faint">{m.hint}</span>
                  </button>
                );
              })}
            </div>

            <p className="mt-2 text-xs text-faint">
              参考得多，答得全但更慢；参考得少，答得快，但可能漏掉资料里的细节。
            </p>

            {advancedTopK && (
              <label className="mt-2 flex items-center gap-2 rounded-lg border border-line bg-fill-3 px-2 py-1.5">
                <span className="text-fs-badge text-muted">精确段数</span>
                <input
                  type="number"
                  min={1}
                  max={10}
                  value={topK}
                  onChange={(e) => {
                    const v = Number(e.target.value);
                    if (!Number.isNaN(v)) onTopKChange(v);
                  }}
                  className="underline-input w-16 py-0.5 text-xs"
                />
              </label>
            )}

            <p className="mt-2 border-t border-line pt-2 text-xs text-faint">
              资料怎么分段、切多细，在右侧「资料库」里按每个库单独设置。
            </p>
          </div>
        </div>
      )}

      {/* 底部：外链入口 + 模式切换 + 输入框 */}
      <div className="chat-measure mx-auto px-6 pb-5">
        {/* 校园工具集：从侧栏搬来（10-14）。它是「发消息之前顺手用一下」的入口，
            挂在对话栏这一行才够得着；放侧栏等于让它跟会话列表抢同一条视线。
            场景 chips 撤出后（10-14 晚）这一行只剩它一个，保持靠右的胶囊观感。 */}
        <div className="mb-2 flex items-center gap-2 flex-wrap">
          <a
            href={TOOLS_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="tools-link"
            title="校园工具集：GPA 计算 · 番茄钟 · 课表……（免登录，独立页）"
          >
            <CornIcon name="tools" className="h-[15px] w-[15px]" />
            校园工具集
          </a>
        </div>
        <div className="mb-2 flex items-center gap-2 sm:gap-3">
          {/* 2026-10-31 批 B：四档模式条下架，两枚能力开关常驻。
              原因：模式条把「检索 / 联网 / 走流程」混在一个字段里，用户选「检索」
              其实还想不想联网、允不允许调工具都说不清；后端新契约只有两个开关，
              与其做四档→两开关的翻译，不如直接让用户回答这两个问题。 */}
          <CapToggles value={capValue} onChange={onCapChange ?? (() => {})} />
          {/* 反馈：紧贴能力开关右侧，不塞回顶栏那排灰图标里。
              理由：想说话的那一刻人正在输入框这儿（刚被答错、刚发现按钮不好用），
              顶栏右上的小图标太不显眼，内测第一批人的意见最容易在这里丢掉。
              内容和设置页共用 FeedbackCard，只是装在弹窗里。 */}
          <button
            type="button"
            onClick={() => setFbOpen(true)}
            title="每条意见开发者都会看"
            aria-label="提交反馈"
            aria-haspopup="dialog"
            className="fb-mode-cta"
          >
            <span className="fb-mode-dot" aria-hidden="true" />
            {/* 只写「反馈」：原来 30px 宽的「内测反馈」在窄屏要缩成另一份文案，
                现在两个宽度都放得下，就不必再留一份 .fb-mode-text-sm 的影子副本。 */}
            <span className="fb-mode-text">反馈</span>
          </button>
          <button
            onClick={() => setShowParams((s) => !s)}
            title="检索参数设置"
            aria-label="检索参数设置"
            aria-expanded={showParams}
            className={`gear ml-auto ${showParams ? "text-accent" : ""}`}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <circle cx="12" cy="12" r="3" />
              <path d="M12 1v6M12 17v6M4.2 4.2l4.3 4.3M15.5 15.5l4.3 4.3M1 12h6M17 12h6M4.2 19.8l4.3-4.3M15.5 8.5l4.3-4.3" />
            </svg>
          </button>
        </div>

        {pending.length > 0 && (
          <div className="attach-strip">
            {pending.map((a) => (
              <div key={a.id} className="attach-item">
                {a.type.startsWith("image/") ? (
                  <img src={a.url} alt={a.name} />
                ) : (
                  <span className="attach-file-badge">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round">
                      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                      <path d="M14 2v6h6" />
                      <path d="M8 13h8M8 17h5" />
                    </svg>
                  </span>
                )}
                <span className="max-w-[150px] truncate">{a.name}</span>
                <button
                  onClick={() => removeAttachment(a.id)}
                  title="移除附件"
                  aria-label={`移除附件：${a.name}`}
                  className="attach-rm"
                >
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden="true">
                    <path d="M6 6l12 12M18 6L6 18" />
                  </svg>
                </button>
              </div>
            ))}
            <p className="attach-note">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <circle cx="12" cy="12" r="9" />
                <path d="M12 16v-4M12 8h.01" />
              </svg>
              {pendingOtherCount > 0
                ? `仅图片会参与回答（${pendingImageCount} 张）；其余 ${pendingOtherCount} 个文件暂不支持解析`
                : `${pendingImageCount} 张图片将上传，由视觉模型结合问题一起分析`}
            </p>
          </div>
        )}

        {/* 手填城市的一行输入。定位拿不到时页面层直接把它展开 ——
            不弹「定位失败」，因为那是环境限制（http 下浏览器不给坐标 / 服务端没配 key），
            不是用户做错事，红字只会让人以为应用坏了。给出路比说原因有用。 */}
        {cityEditOpen ? (
          <form
            className="geo-edit"
            onSubmit={(e) => {
              e.preventDefault();
              const el = e.currentTarget.elements.namedItem("city") as HTMLInputElement | null;
              onSaveCity?.((el?.value || "").trim());
            }}
          >
            <label htmlFor="geo-city">你在哪个城市？</label>
            <input
              id="geo-city"
              name="city"
              defaultValue={(geoCity || "").split("·")[0].trim()}
              placeholder="例如 茂名 / 大理 / 北京"
              maxLength={20}
              autoComplete="off"
              autoFocus
            />
            <button type="submit" className="geo-edit-ok">
              用这个
            </button>
            <button
              type="button"
              className="geo-edit-cancel"
              onClick={() => onCityEditOpenChange?.(false)}
            >
              取消
            </button>
          </form>
        ) : (
          geoEnabled &&
          geoCity && (
            /* 角标只说地名（「茂名 · 茂南区」）。原来前面挂着「定位已开启，当前在」——
               定位开着这件事，左边那根定位针已经用金色实心说清楚了，再写一遍就是废话，
               而且把地名挤到窄屏第二行。地名拿不到时整块不出现（针仍是金色，状态没丢）。 */
            <div className="geo-badge" role="status" aria-live="polite">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M12 21s7-6.4 7-11a7 7 0 1 0-14 0c0 4.6 7 11 7 11z" />
                <circle cx="12" cy="10" r="2.6" />
              </svg>
              <span>
                {/* geoCity 由上层用 formatPlace() 拼成两级：「茂名 · 茂南区」 */}
                {geoCity}
              </span>
              {/* 屏幕上只剩地名，读屏仍要听得出这是什么 —— 补一句不可见的前缀。
                  必须放在地名 span **之后**：视觉上靠 clip 隐藏、不占位，
                  DOM 顺序上又不影响「第一个 span 就是地名」这条约定。 */}
              <span className="geo-sr">当前定位</span>
              {onCityEditOpenChange && (
                <button
                  type="button"
                  className="geo-badge-edit"
                  onClick={() => onCityEditOpenChange(true)}
                  title="定位不准？自己填一个城市"
                >
                  改
                </button>
              )}
            </div>
          )
        )}

        {/* 检索词校准（B5）：锚点高度 0，面板向上弹出，不推挤输入框 */}
        {willRetrieve && (
          <div className="qe-anchor">
            <QueryEnhance text={input} />
          </div>
        )}

        <div className="input-row">
          <button
            onClick={onToggleGeo}
            disabled={geoPending}
            title={
              geoPending
                ? "正在获取定位…"
                : geoEnabled
                  ? "定位已开启：发送时附带你的位置"
                  : "开启定位：问天气无需手输城市"
            }
            aria-label={geoPending ? "正在获取定位" : geoEnabled ? "关闭定位" : "开启定位"}
            aria-pressed={geoEnabled}
            aria-busy={geoPending}
            className={`geo-btn ${geoEnabled ? "on" : ""} ${geoPending ? "busy" : ""}`}
          >
            {/* 定位针：开启时填充实心，关闭时空心，与「+ 我的文件」同一套线条语言 */}
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M12 21s7-6.4 7-11a7 7 0 1 0-14 0c0 4.6 7 11 7 11z" />
              <circle cx="12" cy="10" r="2.6" />
            </svg>
          </button>
          <button
            onClick={() => attachRef.current?.click()}
            title="上传附件（图片会由视觉模型分析）"
            aria-label="上传附件，图片会由视觉模型分析"
            className="attach"
          >
            {/* 加号：与「+ 新建对话 / + 我的文件」同一套进料语义，替代原回形针 */}
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
              <path d="M12 5v14M5 12h14" />
            </svg>
          </button>
          <input
            ref={attachRef}
            type="file"
            multiple
            // 后端只有视觉模型会消费附件，非图片格式上传了也不会参与回答，
            // 与其让用户选完才发现没用，不如在选择器层面就只放行图片
            accept="image/*"
            className="hidden"
            onChange={(e) => {
              const fs = e.target.files;
              if (fs && fs.length > 0) addFiles(fs);
              if (attachRef.current) attachRef.current.value = "";
            }}
          />
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onPaste={(e) => {
              const items = e.clipboardData?.items;
              if (!items) return;
              const files: File[] = [];
              for (const it of Array.from(items)) {
                if (it.kind === "file") {
                  const f = it.getAsFile();
                  if (f) files.push(f);
                }
              }
              if (files.length > 0) {
                e.preventDefault();
                addFiles(files);
              }
            }}
            onKeyDown={(e) => {
              // 输入法组合期间的按键是"选候选词"，一律不拦
              if ((e.nativeEvent as KeyboardEvent).isComposing) return;
              const el = e.currentTarget;
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
                return;
              }
              if (e.key === "Escape") {
                if (input) {
                  e.preventDefault();
                  histIdxRef.current = -1;
                  setInput("");
                } else if (pending.length > 0) {
                  e.preventDefault();
                  setPending((prev) => {
                    prev.forEach((a) => URL.revokeObjectURL(a.url));
                    return [];
                  });
                }
                return;
              }
              if (e.key === "ArrowUp" && !e.shiftKey) {
                // 光标在开头（或输入框是空的）才取历史，否则交给 textarea 正常移动光标
                if (input === "" || (el.selectionStart === 0 && el.selectionEnd === 0)) {
                  e.preventDefault();
                  recallHistory(-1);
                }
                return;
              }
              if (e.key === "ArrowDown" && !e.shiftKey) {
                if (el.selectionStart === input.length && el.selectionEnd === input.length) {
                  e.preventDefault();
                  recallHistory(1);
                }
              }
            }}
            rows={1}
            placeholder="随便问点什么…"
            aria-label="输入问题"
            className="max-h-40 flex-1 resize-none bg-transparent px-2 py-2.5 text-msg leading-relaxed text-text outline-none placeholder:text-faint"
          />
          {sending || playing ? (
            <button
              onClick={onStop}
              title={
                sending
                  ? "停止生成（截断当前输出，保留已生成内容）"
                  : "立即全部显示（不再逐字播放）"
              }
              aria-label={sending ? "停止生成，截断当前输出" : "立即全部显示"}
              className="send stop"
            >
              <svg viewBox="0 0 24 24" fill="currentColor" className="h-3.5 w-3.5" aria-hidden="true">
                <rect x="6" y="6" width="12" height="12" rx="2" />
              </svg>
            </button>
          ) : (
            <button
              onClick={submit}
              disabled={!input.trim() && pending.length === 0}
              title="发送"
              aria-label="发送"
              className="send"
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M12 19V5" />
                <path d="M5 12l7-7 7 7" />
              </svg>
            </button>
          )}
        </div>

        {/* 键盘提示从 placeholder 里挪出来：placeholder 要短，才像"随便问点什么" */}
        <div className="input-hint">
          <span className="truncate">{capHint}</span>
          {/* 生成中把快捷键提示换成截断提示：停止键只有生成/播放时才出现，
              不写一句"点右侧按钮截断"，用户很容易把它当成另一个发送键。 */}
          <span className="ml-1 shrink-0 whitespace-nowrap">
            {sending
              ? "生成中 · 点右侧按钮截断输出"
              : playing
                ? "仍在逐字显示 · 点右侧按钮立刻全显"
                : "Enter 发送 · ↑ 上一条 · Esc 清空"}
          </span>
        </div>
      </div>

      {previewFile && boundKb && (
        <FilePreviewModal
          kbId={boundKb}
          name={previewFile}
          onClose={() => setPreviewFile(null)}
        />
      )}

      {/* 首次进平台的上手说明：portal 到 body，和有没有对话无关；
          未登录（userId 为空）时它自己不渲染 */}
      <GuideModal userId={userId} />

      {/* 更新公告：同一处挂载。它自己判断「引导还没读完就先不弹」，
          所以两者不会叠在一起；并且它比 GuideModal 高一层（z 96 / 95） */}
      <AnnounceModal userId={userId} />

      {/* 顶栏「反馈」打开的弹窗：内容与设置页是同一个 FeedbackCard，
          区别只是这里没有外层卡片（chrome=false） */}
      <FeedbackModal open={fbOpen} onClose={closeFeedback} page="/" />
    </section>
  );
}
