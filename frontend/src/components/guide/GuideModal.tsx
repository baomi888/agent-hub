"use client";

/**
 * 首次进入平台时的「上手说明」弹窗（L0 引导）。
 *
 * 为什么在首页那张三步引导卡（OnboardBar）之外还要一个弹窗：
 *   那张卡一屏只说三件"下一步做什么"，而且只在**还没开口**的欢迎页出现；
 *   但新用户第一次进来，连「知穗是什么 / 资料放哪 / 左边那些页是干嘛的」都不知道，
 *   需要一个能把话一次讲完、节奏还由他自己控制的地方。
 *   弹窗是**一次性**的：走完或者跳过都按人记住，之后不再出现；
 *   想再看，设置页「账号」下面留了入口。
 *
 * 三条硬规矩（每条都是踩过的坑）：
 *  ① 未登录一律不渲染，也不写 key。否则会把"已读"记到公共 key 上，
 *     之后这台电脑上的新用户全都看不到说明。
 *  ② 用 useDismissed（useSyncExternalStore）：服务端 snapshot 固定"已读"，
 *     水合不会不一致；也避开 eslint 的 react-hooks/set-state-in-effect。
 *  ③ portal 到 body：首页右栏和控制台都可能处在带 transform 的容器里，
 *     fixed 会被裁成那个容器大小（FilePreviewModal 同一个坑）。
 *
 * 交互上两个刻意的选择：
 *  · 点遮罩**不**关闭。引导只弹一次，手抖点空白就再也看不到，代价太大；
 *    要退出请按「跳过」或 Esc —— 都是显式动作。
 *  · 方向键 ←/→ 可翻页，Esc 退出，Tab 锁在卡片内，焦点进来先落在卡片上
 *    （读屏会把整段念出来，而不是从页面顶上重新找起）。
 */

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import CornIcon, { type CornIconName } from "../CornIcon";
import { useDismissed } from "@/lib/hooks/useDismissed";
import { GUIDE_OPEN_EVENT } from "@/lib/guide";

const KEY = "baomi.guide.v1";

interface Step {
  icon: CornIconName;
  title: string;
  lead: string;
  points: string[];
}

/**
 * 四步，一步一件事。
 * 每一步都回答「这是什么 / 我要做什么 / 有什么坑」，不写形容词堆砌的介绍 ——
 * 说明弹窗最容易犯的错是写成产品宣传页，读完还是不知道从哪下手。
 */
const STEPS: Step[] = [
  {
    icon: "corn",
    title: "知穗是个会自己动手的学习助手",
    lead: "你把问题说清楚就行，不用学怎么写提示词。它会自己判断：直接回答、翻你的资料再答，还是先给你排一个计划。",
    points: [
      "输入框上方能手动切「检索 / 智能体 / 纯对话」，默认的「自动」对大多数问题够用",
      "回答里带引用编号，点开能翻回原文那一段核对，不是凭空编的",
    ],
  },
  {
    icon: "upload",
    // ★ 这一步的措辞只能跟着「后端真能读什么」走（api/kb.py 只放行 .txt/.md/.pdf，
    //   而且 PDF 走 PyPDFLoader 抽文字层）。所以不能写"把课件放进来"——
    //   课件里大半是图片、PPT 和扫描件，用户照做会接连撞"不支持的文件格式"和
    //   "未提取到任何文字内容"。先说清楚读得了什么、读不了什么、读不了怎么办。
    title: "资料放进右侧「资料库」",
    lead: "右侧「资料库」→ 新建 → 把文件拖进去。之后问到相关内容，知穗会先翻你的资料再答，不是凭空编。",
    points: [
      "现在只读得了文字：TXT / Markdown / 文字版 PDF。图片、PPT、Word 还读不了 —— 课件要先导出成文字，或另存成带文字层的 PDF",
      "板书、教材截图这类图片不用入库：贴进输入框的「+」，视觉模型当场看；但它不进资料库，之后翻不回原文",
      "一份都不放也能用，只是回答更泛，也没法翻回原文核对",
    ],
  },
  {
    icon: "clock",
    title: "每天那几件事，可以交给它",
    lead: "每日委托按你定好的时间自动跑，跑完的结果进当天的总结；打卡把学过的内容连成一条线，还能生成图文分享出去。",
    points: [
      "委托连续失败会自动停用并写明原因，不会每天默默失败还装作正常",
      "左侧「学习路径」12 关，条件是真实数据（资料份数 / 打卡天数 / 掌握词数 / 模考分数），到线自动过",
    ],
  },
  {
    icon: "tools",
    title: "还有一堆不用配置的小工具",
    lead: "单词卡、课程表、GPA 分析、番茄钟、倒计时、群抽签 —— 点开就能用，不用填任何设置。",
    points: [
      "「校园工具集」不用登录也能打开，手机上也能用，收藏一下比记路径快",
      "常用功能在左侧导航和首页三张大卡里；迷路了就回到首页，从「知穗能干什么」重新挑",
    ],
  },
];

// 只在客户端 portal：SSR 阶段没有 document，用 getServerSnapshot 给 false 避免水合不一致
const subscribe = () => () => {};
const mountedSnapshot = () => true;
const serverSnapshot = () => false;

interface Props {
  /** 当前登录用户 id；为空（未登录 / 身份未确认）时不弹 */
  userId?: string | null;
}

export default function GuideModal({ userId = null }: Props) {
  const key = userId ? `${KEY}.${userId}` : KEY;
  const [dismissed, dismiss, restore] = useDismissed(key);
  const [step, setStep] = useState(0);
  const mounted = useSyncExternalStore(subscribe, mountedSnapshot, serverSnapshot);
  const cardRef = useRef<HTMLDivElement>(null);
  // 惰性初始值在首次 render 阶段抓触发元素：进 effect 再读 activeElement，
  // 拿到的已经是弹窗自己了（FilePreviewModal 同样的处理）
  const [opener] = useState<Element | null>(() =>
    typeof document === "undefined" ? null : document.activeElement
  );

  const open = mounted && !!userId && !dismissed;
  const last = step === STEPS.length - 1;

  // Esc 退出 + ←/→ 翻页 + Tab 锁在卡片内
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        dismiss();
        return;
      }
      if (e.key === "ArrowRight") {
        e.preventDefault();
        setStep((s) => Math.min(s + 1, STEPS.length - 1));
        return;
      }
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        setStep((s) => Math.max(s - 1, 0));
        return;
      }
      if (e.key !== "Tab" || !cardRef.current) return;
      const nodes = cardRef.current.querySelectorAll<HTMLElement>(
        'button, [href], input, textarea, [tabindex]:not([tabindex="-1"])'
      );
      if (nodes.length === 0) return;
      const first = nodes[0];
      const end = nodes[nodes.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        end.focus();
      } else if (!e.shiftKey && document.activeElement === end) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [open, dismiss]);

  // 打开时焦点给卡片（读屏整段念），关掉时还给进来前的元素。
  //
  // ★ 光 focus 一次不够：首页的输入框有自己的"挂载后自动聚焦"逻辑，
  //   它比弹窗晚一步跑，会把焦点从卡片抢回 textarea（第一版就是这么翻的：
  //   探针里 activeElement 是 TEXTAREA，按 ← → 变成在输入框里移光标）。
  //   所以弹窗开着的时候要守一层：焦点一旦跑出卡片就立刻拉回来。
  //   这也是 modal 该有的行为 —— 遮罩挡着背景，键盘焦点本来就不该在外面。
  useEffect(() => {
    if (!open) return;
    cardRef.current?.focus();
    const onFocusIn = (e: FocusEvent) => {
      const card = cardRef.current;
      if (!card) return;
      if (!card.contains(e.target as Node)) card.focus();
    };
    document.addEventListener("focusin", onFocusIn);
    return () => {
      document.removeEventListener("focusin", onFocusIn);
      if (opener instanceof HTMLElement) opener.focus?.();
    };
  }, [open, opener]);

  // 设置页的「再看一次」：清掉已读标记，并从第一步开始
  useEffect(() => {
    const onOpen = () => {
      setStep(0);
      restore();
    };
    window.addEventListener(GUIDE_OPEN_EVENT, onOpen);
    return () => window.removeEventListener(GUIDE_OPEN_EVENT, onOpen);
  }, [restore]);

  const close = useCallback(() => dismiss(), [dismiss]);

  if (!open) return null;

  const s = STEPS[step];

  return createPortal(
    <div className="guide-mask" role="presentation">
      <div
        ref={cardRef}
        className="guide-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="guide-title"
        tabIndex={-1}
      >
        <div className="guide-head">
          <span className="guide-eyebrow">上手说明</span>
          <span className="guide-brand">苞米地 · 知穗</span>
          <button
            type="button"
            className="guide-skip"
            onClick={close}
            title="关闭，之后不再自动弹出（设置里可以再看）"
          >
            跳过
          </button>
        </div>

        <div className="guide-body">
          <div className="guide-figure">
            <span className="guide-icon">
              <CornIcon name={s.icon} />
            </span>
            <span className="guide-dots" aria-hidden="true">
              {STEPS.map((_, i) => (
                <i key={i} className={i === step ? "on" : i < step ? "done" : ""} />
              ))}
            </span>
          </div>

          <h3 id="guide-title" className="guide-title">
            {s.title}
          </h3>
          <p className="guide-lead">{s.lead}</p>
          <ul className="guide-points">
            {s.points.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </div>

        <div className="guide-foot">
          <p className="guide-count">
            第 {step + 1} 步 · 共 {STEPS.length} 步
            <span>跳过也记住，下次不再自动弹</span>
          </p>
          <div className="guide-acts">
            {step > 0 && (
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => setStep((v) => Math.max(0, v - 1))}
              >
                上一步
              </button>
            )}
            {last ? (
              <button type="button" className="btn btn-primary btn-sm" onClick={close}>
                开始使用
              </button>
            ) : (
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={() => setStep((v) => Math.min(STEPS.length - 1, v + 1))}
              >
                下一步
              </button>
            )}
          </div>
        </div>
      </div>
    </div>,
    document.body
  );
}
