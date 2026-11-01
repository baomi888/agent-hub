"use client";

/**
 * 「从对话里收几条」—— 手工沉淀弹层。
 *
 * ★ 这是板块 D 三条沉淀路（自动 / 工具 / 手工）里**手工**那条的界面。
 *   后端 `service.sediment.ingest_candidates()` 三条路共用同一套入库逻辑
 *   （见 docs/接口文档_2026-10-30_板块D… 第 179 行），所以这里存进去的东西
 *   和将来自动沉淀进库的是同一种，不是另起的口子。
 *
 * ★ 前端只传下标、不传正文：后端拿 sid 重新提取候选（防伪造）。所以流程必然是
 *   先拉 candidates → 用户勾选 → 提交 indexes，不能自己把 text 送上去。
 *
 * ★ 候选**默认一条都不勾**。后端判据是零 LLM 正则，实测 15 条里 9 条是
 *   「帮我做个计划」这类对助手说的话；默认全勾等于一键往库里灌垃圾，而垃圾
 *   事后只能一条条删文件。让用户自己挑，是当前判据精度下的必要设计。
 *
 * ★ 文案纪律：
 *   - 不写「智能」「AI 已为你提取」——它是正则不是理解，写大了用户一用就穿帮；
 *   - 不说「沉淀」——那是内部叫法，用户脑子里的话是「存起来」；
 *   - 候选原文**原样显示**，不摘要、不改写（后端注释：抽象会不可逆损失原文，
 *     资料库的价值恰恰在可溯源）。
 */
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { api } from "@/lib/api";
import type { Conversation, DialogueCandidate, KbInfo } from "@/lib/types";

interface Props {
  /** 已知会话（从对话页右栏进来）。为空时先让用户选一段对话（从资料库页进来）。 */
  sid?: string | null;
  /** 预选的目标库，通常是当前会话已绑的那个 */
  defaultKbId?: string | null;
  /**
   * 这段对话现在绑的是哪个库（null = 没绑）。
   *
   * ★ 为什么需要它：存进去 ≠ 查得到。检索只走会话绑定的那个库，会话没绑库时
   *   这几句话躺在库里但永远不会被召回。所以存完要顺手绑上（和 `onUpload`
   *   那条「导入成功就顺手绑上，建库的意图本来就是要用它」同一个道理），
   *   而"要不要绑"取决于它现在是不是已经绑着别的库。
   */
  boundKb?: string | null;
  /**
   * 把库绑到会话上（页面层的 `bindKb`）。
   *
   * ★ 有它就走它，不要自己调 `api.bindKb`：页面层的这份会同时更新 `sessions`
   *   里的 kb_id，顶栏那句「未绑定资料库 · 点此选择」才会跟着消失。只调 API
   *   的话后端已经绑上了、界面还写着没绑，用户会以为没生效又去点一遍。
   */
  onBind?: (kbId: string) => void | Promise<void>;
  /**
   * 从某条用户消息下的「存进资料库」进来时，传那条消息的 dbId 与原文。
   *
   * 有这个就走「单句模式」：界面上只显示这一句，确认后原样入库，
   * **不经过候选判据**。判据是给批量预览排序用的正则，认不出「周会固定在
   * 每周四三点」这种没有编号的事实；用户已经指着它说"就这句"，再拿正则
   * 去否掉他，等于当面告诉他这功能不好使。
   */
  focusRef?: number | null;
  focusText?: string | null;
  /**
   * 这条消息是谁说的。用户的短句走「单句原样存」，助手的回答走「分节列表」——
   * 一段 700 字的回答整条塞进向量库，以后召回一条就占满检索 top-k，
   * 所以按小节存，每节自带标题。
   */
  focusRole?: "user" | "assistant";
  onClose: () => void;
  /** 入库成功。调用方据此刷新库列表 / 提示 */
  onImported?: (kbId: string) => void;
}

/** 只在客户端 portal：SSR 阶段没有 document，用 getServerSnapshot 避免水合不一致 */
const subscribe = () => () => {};
const mountedSnapshot = () => true;
const serverSnapshot = () => false;

/**
 * 候选正文去掉排版标记（`## 一、基础` → `一、基础`，`- 精听真题…` → `精听真题…`）。
 *
 * 只作用于显示：入库的仍是原文。助手的小节标题需要保留层级感，
 * 但把 `##` `**` 原样摊在弹层里，满屏符号会把内容本身吃掉。
 */
function tidy(text: string): string {
  return text
    .split("\n")
    .map((l) =>
      l
        .replace(/^\s{0,3}#{1,6}\s+/, "")
        .replace(/^\s*[-*+•]\s+/, "")
        .replace(/^\s*\d{1,2}[.、)]\s+/, "")
        .replace(/\*\*/g, "")
        .trim()
    )
    .filter(Boolean)
    .join("\n");
}

/** 秒 → 「12 月 3 日」。后端全站用 Unix 秒，直接喂 Date 会变成 1970 年。 */
function dayLabel(ts?: number): string {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return `${d.getMonth() + 1} 月 ${d.getDate()} 日`;
}

/** 这条消息没命中候选时的空集：稳定引用，避免每帧新建 Set 让勾选状态重算 */
const EMPTY_SET: ReadonlySet<number> = new Set<number>();

export default function SedimentModal({
  sid,
  defaultKbId,
  boundKb,
  onBind,
  focusRef,
  focusText,
  focusRole = "user",
  onClose,
  onImported,
}: Props) {
  const mounted = useSyncExternalStore(subscribe, mountedSnapshot, serverSnapshot);
  const cardRef = useRef<HTMLDivElement>(null);
  // 惰性抓触发元素：autoFocus / 初次聚焦会在 commit 阶段抢走焦点，
  // 等到 effect 里再读 activeElement，拿到的是弹层自己的按钮
  const [opener] = useState<Element | null>(() =>
    typeof document === "undefined" ? null : document.activeElement
  );

  const [curSid, setCurSid] = useState<string | null>(sid ?? null);
  const [sessions, setSessions] = useState<Conversation[] | null>(null);
  const [kbs, setKbs] = useState<KbInfo[] | null>(null);

  // ★ 数据带 sid 标记：换会话时旧结果因为 sid 对不上被直接忽略，
  //   等价于清空，但不用在 effect 里同步 setState（会触发 set-state-in-effect）
  const [data, setData] = useState<{ sid: string; title: string; cands: DialogueCandidate[] } | null>(null);
  const [err, setErr] = useState<{ sid: string; msg: string } | null>(null);
  const [picked, setPicked] = useState<{ sid: string; idx: Set<number> }>({ sid: "", idx: new Set() });

  const [kbId, setKbId] = useState<string | null>(defaultKbId ?? null);
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState<string | null>(null);
  /** 一个库都没有时就地建：让「存进资料库」不因为没建库而断路 */
  const [newKb, setNewKb] = useState("");
  const [makingKb, setMakingKb] = useState(false);
  /** 「这条消息没挑出东西」那一屏，用户点了「看看这段对话里其它的」 */
  const [focusOff, setFocusOff] = useState(false);
  const [done, setDone] = useState<{
    imported: number;
    skipped: number;
    kbName: string;
    /** 这次之后，这段对话是否真的会用这个库来检索 */
    bound: boolean;
    /** 单句模式存的，文案说「这句」而不是「N 条」 */
    one: boolean;
  } | null>(null);

  const activeKbId = kbId ?? kbs?.[0]?.kb_id ?? null;
  const activeKbName = kbs?.find((k) => k.kb_id === activeKbId)?.name ?? "资料库";

  const cands = data && data.sid === curSid ? data.cands : null;
  const activeErr = err && err.sid === curSid ? err.msg : null;

  // 「从这条消息进来」只作用于最初那段对话：用户点了「换一段对话」之后，
  // 再拿上一条消息去存就是张冠李戴了。
  const focusOn = focusRef != null && !!sid && curSid === sid;
  /** 单句模式（只对用户的话）：他指的是这一整句，原样存，不列候选 */
  const one = focusOn && focusRole === "user" && !!focusText && !focusOff;

  /** 从某条助手回答进来：这条回答切出来的全部小节（带全局下标，勾选要用） */
  const focusBlocks = useMemo<number[]>(() => {
    if (!focusOn || focusRole !== "assistant" || !cands) return [];
    const target = Number(focusRef);
    const out: number[] = [];
    cands.forEach((c, i) => {
      if ((c.refs ?? []).some((r) => Number(r) === target)) out.push(i);
    });
    return out;
  }, [cands, focusOn, focusRole, focusRef]);

  /** 助手回答默认全勾 —— 他点的是「存这条回答」，不是「帮我挑几节」 */
  const defaultIdx = useMemo<ReadonlySet<number>>(
    () => (focusRole === "assistant" && focusBlocks.length > 0 ? new Set(focusBlocks) : EMPTY_SET),
    [focusRole, focusBlocks]
  );

  // 还没动过勾选时用默认值；一旦用户点过任何一格，就用他自己的选择
  const pickedIdx = picked.sid === curSid ? picked.idx : defaultIdx;

  /** 这次要展示的候选（带全局下标）。从某条回答点进来时只给这条的节 */
  const visible = useMemo<Array<{ c: DialogueCandidate; i: number }>>(() => {
    if (!cands) return [];
    const arr = cands.map((c, i) => ({ c, i }));
    if (focusOn && focusRole === "assistant" && !focusOff) {
      // 空集也要落到空态：让他知道"这条回答没切出小节"，
      // 而不是把整段对话的候选糊上来、看起来像点错了地方
      const keep = new Set(focusBlocks);
      return arr.filter(({ i }) => keep.has(i));
    }
    return arr;
  }, [cands, focusOn, focusRole, focusOff, focusBlocks]);

  const mine = visible.filter(({ c }) => c.role !== "assistant");
  const theirs = visible.filter(({ c }) => c.role === "assistant");

  // 目标库列表
  useEffect(() => {
    let alive = true;
    api.listKbs().then(
      (r) => alive && setKbs(r.kbs || []),
      () => alive && setKbs([])
    );
    return () => {
      alive = false;
    };
  }, []);

  // 会话列表（只有不知道 sid 时才要）
  useEffect(() => {
    if (sid) return;
    let alive = true;
    api.listSessions().then(
      // 8 条够了：一屏 12 条会把卡片撑满，看起来像"整页都是列表"而不是一个弹层
      (r) => alive && setSessions((r.sessions || []).slice(0, 8)),
      () => alive && setSessions([])
    );
    return () => {
      alive = false;
    };
  }, [sid]);

  const load = useCallback((target: string) => {
    let alive = true;
    api.getDialogueCandidates(target).then(
      (r) =>
        alive &&
        setData({
          sid: target,
          title: r.conv_title || "这段对话",
          cands: r.candidates || [],
        }),
      (e) =>
        alive &&
        setErr({ sid: target, msg: e instanceof Error ? e.message : "读取失败" })
    );
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!curSid) return;
    return load(curSid);
  }, [curSid, load]);

  // 关闭时把焦点还回去，否则键盘用户会掉回页面开头
  useEffect(
    () => () => {
      if (opener instanceof HTMLElement) opener.focus?.();
    },
    [opener]
  );

  // Esc 关闭 + Tab 锁在弹层内
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== "Tab" || !cardRef.current) return;
      const nodes = cardRef.current.querySelectorAll<HTMLElement>(
        'button, [href], input, textarea, select, [tabindex]:not([tabindex="-1"])'
      );
      if (nodes.length === 0) return;
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  const toggle = (i: number) => {
    if (!curSid) return;
    setPicked((prev) => {
      // 从「默认全勾」开始切，而不是从空集 —— 否则用户取消一节，
      // 另外几节会一起被清空（他看到的勾是 defaultIdx，点掉的却是空集里的一格）
      const base = prev.sid === curSid ? prev.idx : defaultIdx;
      const next = new Set(base);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return { sid: curSid, idx: next };
    });
  };

  const renderItem = ({ c, i }: { c: DialogueCandidate; i: number }) => {
    const on = pickedIdx.has(i);
    return (
      <label key={`${c.ts ?? 0}-${i}`} className={`sd-item${on ? " on" : ""}`}>
        <input type="checkbox" className="sd-box" checked={on} onChange={() => toggle(i)} />
        <span className="sd-body">
          <span className="sd-kind">{c.kind || "其它"}</span>
          <span className="sd-text">{tidy(c.text)}</span>
        </span>
      </label>
    );
  };

  const canSave = one
    ? !!activeKbId && !saving
    : pickedIdx.size > 0 && !!activeKbId && !saving;

  /**
   * 就地建一个库。
   *
   * 没有库时原来只有一句「先到资料库页建一个，再回来存」—— 用户已经勾完、
   * 决定要存了，却要跳走再跳回来，这一步会把一半人挡在门外。
   * 名字留空就拿这段对话的标题兜底，和板块 D「按主题建库」取同一个主意。
   */
  const makeKb = async () => {
    if (makingKb) return;
    const name = newKb.trim() || (data?.title ? `对话·${data.title}` : "我的资料库");
    setMakingKb(true);
    setSaveErr(null);
    try {
      const r = await api.createKb(name);
      setKbs((prev) => [
        ...(prev ?? []),
        { kb_id: r.kb_id, name: r.name || name, chunks: 0, files: [] },
      ]);
      setKbId(r.kb_id);
      setNewKb("");
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : "建库失败，稍后再试");
    } finally {
      setMakingKb(false);
    }
  };

  const submit = async () => {
    if (!curSid || !activeKbId) return;
    setSaving(true);
    setSaveErr(null);
    try {
      const indexes = [...pickedIdx].sort((a, b) => a - b);
      const r = one
        ? await api.importDialogueMessages(activeKbId, curSid, [Number(focusRef)])
        : await api.importDialogue(activeKbId, curSid, indexes);
      const skipped = r.skipped ?? Math.max(0, (one ? 1 : indexes.length) - r.imported);

      // 存进去 ≠ 查得到：检索只走会话绑定的那个库。这段对话本来没绑库就顺手绑上，
      // 否则用户下次提问根本不会用到刚存的这几条（文案也就不能承诺"能查到"）。
      // 已经绑了别的库时不改 —— 那可能是他有意为之，只把情况说清楚。
      let bound = !!boundKb && boundKb === activeKbId;
      if (!bound && !boundKb) {
        try {
          // 没换过会话就走页面层的 onBind，它会把 sessions 里的 kb_id 一起更新
          if (onBind && curSid === sid) await onBind(activeKbId);
          else await api.bindKb(curSid, activeKbId);
          bound = true;
        } catch {
          // 绑不上不影响"已经存进去了"这个事实，文案会退回到"要手动设一下"
        }
      }

      setDone({ imported: r.imported, skipped, kbName: activeKbName, bound, one });
      onImported?.(activeKbId);
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : "没能存进去，稍后再试一次");
    } finally {
      setSaving(false);
    }
  };

  /** 正看着「某条回答切出来的小节」这一屏 */
  const focusBlockView = focusOn && focusRole === "assistant" && !focusOff;

  const title = useMemo(() => {
    if (done) return "已经存好了";
    // 单句模式不说"收几条"——他只指了一条，说"几条"会让人怀疑点错了
    if (one) return "存进资料库";
    if (focusBlockView) return "存这条回答";
    return "从对话里收几条";
  }, [done, one, focusBlockView]);

  if (!mounted) return null;

  const sub = done
    ? `存进了「${done.kbName}」`
    : one
      ? "原样存，不改写"
      : focusBlockView
        ? `这条回答 · ${focusBlocks.length} 节`
        : data && data.sid === curSid
          ? `${data.title}${cands && cands.length > 0 ? ` · 找到 ${cands.length} 条` : ""}`
          : !curSid
            ? "先挑一段对话 —— 会从里面找出值得留的内容"
            : "正在翻这段对话…";

  return createPortal(
    <div
      className="pv-mask"
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={cardRef}
        className="pv-card sd-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="sd-title"
      >
        <header className="pv-head">
          <div className="min-w-0">
            <h2 id="sd-title" className="pv-title">
              {title}
            </h2>
            <p className="pv-meta">
              <span className="sd-sub">{sub}</span>
            </p>
          </div>
          <button type="button" className="pv-close" aria-label="关闭" onClick={onClose}>
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </header>

        <div className="pv-body">
          {done ? (
            <div className="sd-done">
              <p className="sd-done-n">
                {done.imported > 0
                  ? done.one
                    ? "这句已经进库了"
                    : `${done.imported} 条已经进库了`
                  : done.one
                    ? "这句之前存过了"
                    : "这几条之前都存过了"}
              </p>
              {done.imported > 0 && done.skipped > 0 && (
                <p className="sd-done-p">
                  另外 {done.skipped} 条之前存过，这次跳过了 —— 同一句话不会重复入库。
                </p>
              )}
              {done.imported > 0 ? (
                <p className="sd-done-p">
                  {done.bound
                    ? `这段对话已经用「${done.kbName}」来检索，下次提问就能查到。`
                    : `想让它被查到，得先把这段对话的资料库切成「${done.kbName}」。`}
                </p>
              ) : (
                <p className="sd-done-p">
                  同一句话不会重复入库，所以这次没写新的东西进去。
                </p>
              )}
              {done.one && done.imported > 0 && (
                <p className="sd-done-p">想再存几句，回到对话里，指着那句话再点一次就行。</p>
              )}
            </div>
          ) : !curSid ? (
            <div className="sd-sessions">
              {sessions === null ? (
                <p className="sd-hint">正在列你的对话…</p>
              ) : sessions.length === 0 ? (
                <p className="sd-hint">你还没有聊过的对话。</p>
              ) : (
                sessions.map((s) => (
                  <button
                    key={s.id}
                    type="button"
                    className="sd-session"
                    onClick={() => setCurSid(s.id)}
                  >
                    <span className="sd-session-t">{s.title || "未命名对话"}</span>
                    <span className="sd-session-d">{dayLabel(s.updated_at)}</span>
                  </button>
                ))
              )}
            </div>
          ) : one ? (
            /* 单句模式：只显示他指的这一句。原样，不摘要、不改写 ——
               资料库的价值在可溯源，改一个字就对不上他当时说的话。
               ★ 放在「候选加载中」之前：这条路根本不需要候选，等它就是白等。 */
            <div className="sd-one">
              <p className="sd-one-label">这一句</p>
              <p className="sd-one-text">{focusText}</p>
              <p className="sd-hint">
                原样存进去。以后问相关的事，我会先翻这里 ——
                所以值得存的是"以后还用得上"的那句，不是这一轮的诉求。
              </p>
              <button type="button" className="sd-switch" onClick={() => setFocusOff(true)}>
                或者从这段对话里多挑几条
              </button>
            </div>
          ) : activeErr ? (
            <div className="sd-err">
              <p>{activeErr}</p>
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => curSid && load(curSid)}>
                再试一次
              </button>
            </div>
          ) : cands === null ? (
            <p className="sd-hint">正在翻这段对话…</p>
          ) : cands.length === 0 ? (
            <div className="sd-empty">
              <p>这段对话里没找到能存的内容。</p>
              <p className="sd-empty-p">
                值得留的是讲清楚的事实、规定、偏好，以及助手整理过的分析。
                「帮我做个计划」这类交给助手去办的事不算。
              </p>
              {!sid && (
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => setCurSid(null)}>
                  换一段对话
                </button>
              )}
            </div>
          ) : visible.length === 0 ? (
            /* 从某条回答点进来，但这条回答没切出小节 —— 说清楚为什么，
               别把整段对话的候选糊上来，让人以为点错了地方 */
            <div className="sd-empty">
              <p>这条回答里没有能单独存下来的段落。</p>
              <p className="sd-empty-p">
                整条存进去会占掉检索的名额，所以按小节切；这条回答太短，没切出小节。
              </p>
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => setFocusOff(true)}>
                看看这段对话里其它的
              </button>
            </div>
          ) : focusOn && focusRole === "assistant" && !focusOff ? (
            /* 从某条回答点进来：只列这条回答切出来的小节，默认全勾 */
            <>
              <p className="sd-focus">
                这条回答拆成 {visible.length} 节，每节单独存 ——
                以后问到相关的事能直接翻到那一节，不会把整条灌进来。
              </p>
              <div className="sd-list">{visible.map(renderItem)}</div>
              {!sid && (
                <button type="button" className="sd-switch" onClick={() => setCurSid(null)}>
                  换一段对话
                </button>
              )}
              <button type="button" className="sd-switch" onClick={() => setFocusOff(true)}>
                这段对话里还有别的
              </button>
            </>
          ) : (
            <>
              {mine.length > 0 && (
                <div className="sd-group">
                  <p className="sd-group-t">你说的话</p>
                  <div className="sd-list">{mine.map(renderItem)}</div>
                </div>
              )}
              {theirs.length > 0 && (
                <div className="sd-group">
                  <p className="sd-group-t">助手的分析</p>
                  <div className="sd-list">{theirs.map(renderItem)}</div>
                </div>
              )}
              <p className="sd-hint sd-tip">
                助手整理过的要点最值得存 —— 下次问到相关的事，它能直接拿这些回答你。
                你的原话是另一类：一手的事实和规定，改一个字就对不上。
              </p>
              {!sid && (
                <button type="button" className="sd-switch" onClick={() => setCurSid(null)}>
                  换一段对话
                </button>
              )}
              {focusOn && focusOff && (
                <button type="button" className="sd-switch" onClick={() => setFocusOff(false)}>
                  只看我指的那一条
                </button>
              )}
            </>
          )}

          {saveErr && <p className="sd-save-err">{saveErr}</p>}
        </div>

        {(done || one || (cands && cands.length > 0)) && (
          <footer className="sd-foot">
            {done ? (
              <button type="button" className="btn btn-primary btn-sm sd-save" onClick={onClose}>
                好
              </button>
            ) : kbs === null ? (
              <span className="sd-hint">正在读你的资料库…</span>
            ) : kbs.length === 0 ? (
              /* 一个库都没有：原地建，别让人跳走再回来 */
              <div className="sd-newkb">
                <span className="sd-hint">
                  你还没有资料库 —— 现在建一个，{one ? "把这一句" : "把这几条"}放进去。
                </span>
                <div className="sd-newkb-row">
                  <input
                    className="sd-input"
                    value={newKb}
                    onChange={(e) => setNewKb(e.target.value)}
                    placeholder={data?.title ? `对话·${data.title}` : "资料库名字"}
                    aria-label="新资料库的名字（留空用对话标题）"
                    maxLength={40}
                  />
                  <button
                    type="button"
                    className="btn btn-primary btn-sm sd-save"
                    disabled={makingKb}
                    onClick={() => void makeKb()}
                  >
                    {makingKb ? "正在建…" : "建一个"}
                  </button>
                </div>
              </div>
            ) : (
              <>
                <label className="sd-target">
                  <span>存到</span>
                  <select
                    className="sd-select"
                    value={activeKbId ?? ""}
                    onChange={(e) => setKbId(e.target.value)}
                  >
                    {kbs.map((k) => (
                      <option key={k.kb_id} value={k.kb_id}>
                        {k.name}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  className="btn btn-primary btn-sm sd-save"
                  disabled={!canSave}
                  onClick={() => void submit()}
                >
                  {saving
                    ? "正在存…"
                    : one
                      ? "存进这个库"
                      : `存进这个库${pickedIdx.size > 0 ? `（${pickedIdx.size} 条）` : ""}`}
                </button>
              </>
            )}
          </footer>
        )}
      </div>
    </div>,
    document.body
  );
}
