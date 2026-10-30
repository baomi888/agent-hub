"use client";

// 主页面：三栏布局（会话栏 + 对话区 + 知识库面板）与跨域编排
// 2026-09 重构：toast/偏好/会话/知识库各域逻辑拆到 lib/hooks/*，
// 本文件只保留：断点与主题、抽屉管理、聊天流（SSE）、联网建库编排、布局
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import Sidebar from "@/components/Sidebar";
import ChatArea, { type ChatAttachment } from "@/components/ChatArea";
import KnowledgePanel from "@/components/KnowledgePanel";
import UsagePanel from "@/components/UsagePanel";
import { api } from "@/lib/api";
import { streamChat } from "@/lib/sse";
import { uid } from "@/lib/id";
import { useToast } from "@/lib/hooks/useToast";
import { getSendGeo, getCachedGeo, isGeoEnabled, acquireGeo, reverseGeocodePlace, setGeoEnabled, updateCachedPlace, formatPlace, getManualCity, setManualCity } from "@/lib/geo";
import { usePrefs } from "@/lib/hooks/usePrefs";
import { useSessions } from "@/lib/hooks/useSessions";
import { useKnowledge } from "@/lib/hooks/useKnowledge";
import { useAuth } from "@/lib/hooks/useAuth";
import { detailToMessage } from "@/lib/errors";
import AuthGate from "@/components/AuthGate";
import type { UiMessage } from "@/components/chat/types";
import type { ChatMode, Grounding, RefSource, ToolEvent } from "@/lib/types";
import { MODE_LABELS } from "@/components/chat/constants";
import { isPlanRequest } from "@/components/chat/plan";
import { useHybridTrace } from "@/lib/useHybridTrace";
import type { ChunkParams } from "@/lib/chunkProfiles";

// ---- 断点订阅（matchMedia 作为外部数据源） ----
type Viewport = "wide" | "medium" | "narrow";

function getViewport(): Viewport {
  if (window.matchMedia("(max-width: 899px)").matches) return "narrow";
  if (window.matchMedia("(min-width: 1280px)").matches) return "wide";
  return "medium";
}

function subscribeViewport(onChange: () => void) {
  const narrow = window.matchMedia("(max-width: 899px)");
  const wide = window.matchMedia("(min-width: 1280px)");
  narrow.addEventListener("change", onChange);
  wide.addEventListener("change", onChange);
  return () => {
    narrow.removeEventListener("change", onChange);
    wide.removeEventListener("change", onChange);
  };
}

// ---- 主题：以 documentElement 上的 dark class 为唯一事实来源 ----
const themeListeners = new Set<() => void>();

function readTheme(): boolean {
  return document.documentElement.classList.contains("dark");
}

function writeTheme(next: boolean) {
  document.documentElement.classList.toggle("dark", next);
  try {
    localStorage.setItem("theme", next ? "dark" : "light");
  } catch {
    /* 隐私模式下写入失败可忽略 */
  }
  themeListeners.forEach((cb) => cb());
}

function subscribeTheme(cb: () => void) {
  themeListeners.add(cb);
  return () => {
    themeListeners.delete(cb);
  };
}

export default function Home() {
  // ---- 各域 hooks ----
  const { toasts, showToast } = useToast();
  const auth = useAuth();
  const prefs = usePrefs();
  const {
    mode, setMode, topK, setTopK, chunkSize, setChunkSize,
    chunkOverlap, setChunkOverlap, kbPanelPref, setKbPanelPref, savedRef,
    fontScale, setFontScale,
  } = prefs;
  // 能力开关（检索 / 智能联网）的**视觉预览**开关：`?caps=1`。
  // 后端契约（use_kb / use_web）还没上线，两枚开关点了只改本地状态、不影响请求，
  // 所以默认不显示 —— 不能给用户一个按了没反应的按钮。
  // 读 query 放在 effect 里而不是 useState 初始化函数：页面会被预渲染，
  // 初始化时读 window 会 hydration 不一致（服务端 false、客户端 true）。
  const [capToggles, setCapToggles] = useState(false);
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("caps") === "1") setCapToggles(true);
  }, []);
  const sessions_ = useSessions(showToast);
  const {
    sessions, setSessions, activeSid, setActiveSid, messages, setMessages,
    loading: sessionsLoading, loadError: bootError,
    refreshSessions, createSession, selectSession, renameSession,
    deleteSession, deleteTurn, reloadSessions,
  } = sessions_;
  const {
    kbs, kbNameMap, loading: kbsLoading,
    refreshKbs, uploadFiles, deleteKbFile, rebuildKb, searchPreview, deleteKb,
    rememberChunk, chunkParamsOf,
  } = useKnowledge(showToast, chunkSize, chunkOverlap);

  // ---------- 混合架构（mode=hybrid）----------
  //
  // hybrid 是「每轮一个实例」的归并器：消息发出去时 reset，流式过程中被三个事件喂，
  // done 时收尾。刻意不做成全局 state —— 轨迹属于**某一条消息**，
  // 放全局就会出现「A 轮的节点状态串到 B 轮」。
  const hybrid = useHybridTrace();
  // 轨迹是 hook 内部 state，但它要落到消息上（才能随消息持久化 / 刷新后重建），
  // 所以订阅它变化，把 items 同步进当前那条助手消息。
  const [hybridItems, setHybridItems] = useState<import("@/lib/types").TraceItem[]>([]);
  useEffect(() => setHybridItems(hybrid.trace.items), [hybrid.trace.items]);
  // 起跑前选中的工作流。用 ref 而不是 state：它是「发请求时要读一次」的值，
  // 放进 state 会让改选择时多触发一次渲染，而它对 UI 没有独立展示需求。
  const workflowIdRef = useRef<string | null>(null);
  // 本轮助手消息 id：轨迹同步要靠它定位「往哪条消息上写」
  const asstIdRef = useRef<string | null>(null);

  // 轨迹 → 消息。hybrid hook 内部持有 items，这里把它落到消息上，
  // 目的是让它随消息一起被持久化（刷新后还能拿到 hybridRunId 去 /logs 重建）。
  //
  // ⚠️ 用 ref 定位而不是在流式回调里顺手写：那样每个事件都要多一次 setMessages，
  // 会和 token 的累加打架（同一条消息被两个分支各自 map）。
  // 独立 effect 只在 items 真的变了时才动，语义单一。
  useEffect(() => {
    const target = asstIdRef.current;
    if (!target || hybridItems.length === 0) return;
    setMessages((prev) =>
      prev.map((m) => (m.id === target ? { ...m, hybridTrace: hybridItems } : m))
    );
  }, [hybridItems, setMessages]);

  // 登录成功后要重新拉一次：门后面的首屏请求是被 401 挡掉的，
  // 两个 hook 的 init effect 已经跑过了，不补这一次就是空列表。
  const authedId = auth.me?.user.id ?? null;
  useEffect(() => {
    if (!authedId) return;
    void reloadSessions();
    void refreshKbs();
    // 只在"换人/刚登录"时补拉，不跟着这两个函数的新引用反复跑
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authedId]);

  const [sending, setSending] = useState(false);
  // 地理定位开关：开启后发送消息自动附带坐标，天气/本地问答免手输城市
  const [geoEnabled, setGeoEnabledState] = useState(isGeoEnabled());
  // 当前定位地名（角标展示用）；从缓存里读出，避免刷新后角标丢失。两级：「茂名 · 茂南区」
  const [geoCity, setGeoCity] = useState<string | null>(() => formatPlace(getCachedGeo()) || null);
  // 定位请求进行中：授权弹窗 + IP 兜底最长 18s，期间按钮转圈，否则看着像没反应
  const [geoPending, setGeoPending] = useState(false);
  // 「手填城市」小输入框是否展开。定位拿不到时直接展开它 —— 不弹报错，给出路
  const [cityEdit, setCityEdit] = useState(false);
  // 场景预设（scenario state + localStorage 随会话保存）已随「场景选择撤出
  // 主界面」整体删除（10-14 晚）：场景化入口统一走模板广场（完整启动包），
  // 工作台不再给对话贴场景标签；后端 /api/chat 的 scenario 字段保留兼容。

  // 联网建库后把建议问题预填进输入框（不自动发送），让用户确认/补充
  const [prefill, setPrefill] = useState<{ text: string; ts: number } | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // 模板广场套用后跳回：?sid 自动选中新建会话，?q 预填引导问题
  // 放在 setPrefill 声明之后；依赖 sessions 列表先回来，列表没回来时这个
  // sid 还不在里面，过早 select 会选空。
  const applyRef = useRef<{ sid: string; q: string | null; mode?: string } | null>(null);
  useEffect(() => {
    const sp = new URLSearchParams(window.location.search);
    const sid = sp.get("sid");
    if (sid) applyRef.current = { sid, q: sp.get("q"), mode: sp.get("mode") ?? undefined };
    // 只跑一次：读 query 是幂等的，无需进 deps
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    const a = applyRef.current;
    if (!a) return;
    if (sessions.length === 0) return; // 列表还没回来
    if (!sessions.some((s) => s.id === a.sid)) return; // 这个会话还没进列表
    if (a.q) setPrefill({ text: a.q, ts: Date.now() });
    // 模板推荐的对话模式：落进偏好，让新建会话默认走该模式。
    // 白名单从 MODE_LABELS 派生（不再手写数组）—— 加 hybrid 那天就是漏了这里，
    // 硬编码一份模式列表必然随新模式漏改。
    const validModes = MODE_LABELS.map((m) => m.value) as readonly string[];
    if (a.mode && validModes.includes(a.mode)) {
      setMode(a.mode as ChatMode);
    }
    if (activeSid !== a.sid) void selectSession(a.sid);
    void refreshKbs();
    applyRef.current = null;
    // 清掉 query，避免刷新又跳一次
    window.history.replaceState({}, "", "/");
  }, [sessions, activeSid, selectSession, refreshKbs, setPrefill]);
  // 抽屉容器：打开时把焦点送进去，关闭后还给触发按钮
  const sidebarRef = useRef<HTMLElement | null>(null);
  const kbRef = useRef<HTMLElement | null>(null);

  // 响应式三档：≥1280 三栏 / 1024–1279 右栏抽屉 / <900 左栏也抽屉
  // 用 useSyncExternalStore 订阅 matchMedia，避免 effect 里 setState 造成的二次渲染
  const viewport = useSyncExternalStore(subscribeViewport, getViewport, () => "wide" as const);
  const [sidebarPref, setSidebarPref] = useState(false);
  // 用量与反馈看板（P2）：侧栏账号块点开
  const [usageOpen, setUsageOpen] = useState(false);
  const kbPanelOpen = kbPanelPref ?? viewport === "wide";
  const sidebarOpen = sidebarPref && viewport === "narrow";

  const setKbPanelOpen = useCallback((v: boolean) => setKbPanelPref(v), [setKbPanelPref]);
  const setSidebarOpen = useCallback((v: boolean) => setSidebarPref(v), []);

  // 深色模式：直接以 <html class="dark"> 为准（layout 内联脚本已写入），避免首帧闪白
  const dark = useSyncExternalStore(subscribeTheme, readTheme, () => false);
  const toggleDark = useCallback(() => writeTheme(!readTheme()), []);

  // 字号档位：与 layout 内联脚本同源（html[data-fs]），变化时同步到文档根
  useEffect(() => {
    document.documentElement.dataset.fs = fontScale;
  }, [fontScale]);

  // 四屏导航里点「知识库」会带 ?kb=1 落到本页。
  // 桌面视口资料库本来就常驻，窄屏则是抽屉 —— 不补这一下，窄屏用户点进来什么都看不到。
  // 只在挂载时读一次并立刻清掉 query，避免用户手动关掉抽屉后又被刷新拉回来。
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("kb") !== "1") return;
    setKbPanelOpen(true);
    window.history.replaceState(null, "", window.location.pathname);
  }, [setKbPanelOpen]);

  // 初始化 defaults：本地存过的参数优先，没存过才用后端默认值
  // （sessions/kbs 列表由各自 hook 的 effect 并发拉取）
  useEffect(() => {
    let alive = true;
    (async () => {
      const d = await api.defaults().catch(() => null);
      if (!alive || !d) return;
      const saved = savedRef.current;
      if (saved.topK === undefined) setTopK(d.top_k);
      if (saved.chunkSize === undefined) setChunkSize(d.chunk_size);
      if (saved.chunkOverlap === undefined) setChunkOverlap(d.chunk_overlap);
    })();
    return () => {
      alive = false;
    };
  }, [savedRef, setTopK, setChunkSize, setChunkOverlap]);

  const boundKb = sessions.find((s) => s.id === activeSid)?.kb_id ?? null;

  // 发送消息（SSE 流式）；attachments 先走 /api/chat/upload 上传，图片由视觉模型消费，
  // 非图片格式后端暂不解析。
  // display：气泡显示的文本（计划功能发送结构化提示时传原问题，提示只给 AI 看）
  // useCallback：保持引用稳定，配合 Bubble 的 memo，流式时不连带重渲染旧消息
  const sendMessage = useCallback(async (
    question: string,
    attachments?: ChatAttachment[],
    display?: string,
    plan?: boolean
  ) => {
    let sid = activeSid;
    // 无会话时自动新建
    if (!sid) {
      try {
        const conv = await api.createSession();
        sid = conv.id;
        setSessions((prev) => [conv, ...prev]);
        setActiveSid(conv.id);
      } catch (e) {
        showToast("error", "新建会话失败，请稍后重试");
        return;
      }
    }

    setMessages((prev) => [
      ...prev,
      { id: uid(), role: "user", content: display ?? question, attachments },
    ]);
    const asstId = uid();
    // 新一轮开始：先把上一轮的轨迹归零，否则新旧两轮的节点会串在一条时间轴上
    hybrid.reset();
    setMessages((prev) => [
      ...prev,
      { id: asstId, role: "assistant", content: "", streaming: true, toolCalls: [] },
    ]);
    // 记住本轮助手消息 id，供下面的轨迹同步 effect 定位
    asstIdRef.current = asstId;
    const controller = new AbortController();
    abortRef.current = controller;
    setSending(true);

    try {
      // 有附件时先上传到后端（图片理解用）
      let sentAttachments: { path: string; type: string; filename?: string }[] = [];
      if (attachments && attachments.length > 0) {
        const files = attachments.map((a) => a.file).filter((f): f is File => !!f);
        if (files.length > 0) {
          const up = await api.uploadChatAttachments(files);
          sentAttachments = up.files.map((f) => ({
            path: f.path,
            type: f.type,
            filename: f.filename,
          }));
        }
      }
      const gen = streamChat(
        {
          sid,
          question,
          top_k: topK,
          mode,
          attachments: sentAttachments,
          location: getSendGeo(),
          // 混合架构：只有 mode=hybrid 时这个字段才有意义。
          // 发之前必须确认选中的工作流还在列表里（刷新后可能已被删），
          // 否则后端会直接回 error 事件，整轮白跑。
          workflow_id: mode === "hybrid" ? (workflowIdRef.current ?? null) : null,
          plan: plan ?? false,
        },
        controller.signal
      );
      let finalRefs = "";
      for await (const ev of gen) {
        switch (ev.event) {
          // ---------- 混合架构三事件（仅 mode=hybrid 才有）----------
          case "hybrid_start": {
            hybrid.onStart(String(ev.data.run_id ?? ""), ev.data.mode === "workflow" ? "workflow" : "agent");
            break;
          }
          case "workflow_node": {
            const phase = ev.data.phase === "exit" ? "exit" : "enter";
            if (phase === "enter") {
              hybrid.onNode({
                phase: "enter",
                node_id: String(ev.data.node_id ?? ""),
                type: (ev.data.type ?? "llm") as never,
                name: String(ev.data.name ?? ""),
              });
            } else {
              hybrid.onNode({
                phase: "exit",
                node_id: String(ev.data.node_id ?? ""),
                status: ev.data.status === "failed" ? "failed" : "ok",
                on_fail: (ev.data.on_fail ?? null) as string | null,
                error: (ev.data.error ?? null) as string | null,
              });
            }
            break;
          }
          case "mode_switch": {
            hybrid.onSwitch({
              rule: String(ev.data.rule ?? ""),
              reason: String(ev.data.reason ?? ""),
              from: ev.data.from === "workflow" ? "workflow" : "agent",
              to: ev.data.to === "workflow" ? "workflow" : "agent",
            });
            break;
          }
          case "token": {
            // 后端现在只发增量，前端自己累加（以前每帧回传全文，长回答流量是平方级）
            const delta = String(ev.data.text ?? "");
            finalRefs = String(ev.data.refs ?? "") || finalRefs;
            setMessages((prev) =>
              prev.map((m) =>
                m.id === asstId
                  ? { ...m, content: (m.content ?? "") + delta, refs: finalRefs || m.refs }
                  : m
              )
            );
            break;
          }
          case "sources": {
            // 引用溯源：把检索到的原文片段挂到这条回答上，角标可展开
            const items = (ev.data.items ?? []) as RefSource[];
            if (items.length > 0) {
              setMessages((prev) =>
                prev.map((m) => (m.id === asstId ? { ...m, sources: items } : m))
              );
            }
            break;
          }
          case "grounding": {
            // 依据强度：后端判定本轮检索够不够格当依据。
            // 单独发一帧而不是塞进 done，是因为用户边流边读，
            // 「依据较弱」这个提示必须赶在正文之前出现在气泡里。
            const g = ev.data as unknown as Grounding;
            setMessages((prev) =>
              prev.map((m) => (m.id === asstId ? { ...m, grounding: g } : m))
            );
            break;
          }
          case "tool": {
            const t = ev.data as unknown as ToolEvent;
            setMessages((prev) =>
              prev.map((m) =>
                m.id === asstId
                  ? { ...m, toolCalls: [...(m.toolCalls ?? []), t] }
                  : m
              )
            );
            break;
          }
          case "title": {
            const title = String(ev.data.title ?? "");
            if (title) {
              setSessions((prev) =>
                prev.map((c) => (c.id === sid ? { ...c, title } : c))
              );
            }
            break;
          }
          case "done": {
            const refs = String(ev.data.refs ?? "");
            // 后端回传的 messages 表 id：赞踩要按它落库，
            // 前端自己生成的 uid 后端不认，只能在这里补挂
            const dbId = Number(ev.data.message_id ?? 0) || undefined;
            // 混合架构收尾：run_id 回传给消息（刷新后靠它 + /logs 重建轨迹），
            // done.mode 才是**最终**模式（静默回退时这里是 agent）
            const doneMode = String(ev.data.mode ?? "");
            const doneRunId = String(ev.data.run_id ?? "");
            hybrid.onDone(doneMode || null);
            // 只有**本轮真的请求了 hybrid** 时，done.mode=agent 才代表"混合模式回落"。
            // 模式入口收敛成 自动/检索/纯对话 之后，auto 常由后端路由到 agent，
            // 那是正常执行，不是回落 —— 以前这里不看请求模式，于是每条 auto 回答
            // 都挂一块「混合模式当前不可用」，用户看到的是假故障（2026-10-30 修）。
            const requestedHybrid = mode === "hybrid";
            if (doneRunId || (requestedHybrid && (doneMode === "workflow" || doneMode === "agent"))) {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === asstId
                    ? {
                        ...m,
                        hybridRunId: doneRunId || m.hybridRunId,
                        // 静默回退时这里是 agent：用户选了 hybrid 却没走混合，
                        // 界面要能据此提示，而不是假装一切正常
                        hybridMode: requestedHybrid
                          ? (doneMode === "workflow" || doneMode === "agent" ? doneMode : m.hybridMode ?? null)
                          : null,
                      }
                    : m
                )
              );
            }
            // 计划主题名（P2）：主题可能是后端从历史推断的，前端猜不到，只能靠它回填
            const planTopic = String(ev.data.plan_topic ?? "");
            setMessages((prev) =>
              prev.map((m) =>
                m.id === asstId
                  ? {
                      ...m,
                      streaming: false,
                      refs: refs || m.refs,
                      dbId: dbId ?? m.dbId,
                      planTopic: planTopic || m.planTopic,
                    }
                  : m
              )
            );
            break;
          }
          case "error": {
            const detail = detailToMessage(ev.data.detail, "未知错误");
            setMessages((prev) =>
              prev.map((m) =>
                m.id === asstId ? { ...m, error: detail, streaming: false } : m
              )
            );
            break;
          }
        }
      }
      // 兜底：结束流式状态
      setMessages((prev) =>
        prev.map((m) => (m.id === asstId ? { ...m, streaming: false } : m))
      );
    } catch (e) {
      // 用户主动停止（AbortError）不视为错误
      if ((e as Error)?.name !== "AbortError") {
        const detail = e instanceof Error ? e.message : String(e);
        setMessages((prev) =>
          prev.map((m) =>
            m.id === asstId ? { ...m, error: detail, streaming: false } : m
          )
        );
      } else {
        // 用户主动停止：标记一下，气泡上要显示"已停止生成"
        setMessages((prev) =>
          prev.map((m) => (m.id === asstId ? { ...m, streaming: false, stopped: true } : m))
        );
      }
    } finally {
      abortRef.current = null;
      setSending(false);
      refreshSessions();
    }
  }, [activeSid, mode, topK, setSessions, setActiveSid, setMessages, showToast, refreshSessions]);

  // 消息镜像：重试需要读当前列表，但不能让回调依赖 messages，否则每个 token
  // 都会生成新的 onRetry，把 Bubble 的 memo 全部打穿
  const messagesRef = useRef<UiMessage[]>(messages);
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  // 重新生成：先移除原来的「提问 + 回答」对，再用同一个问题重发，
  // 否则每次重试都会在历史里追加一条新问答，点几次就翻几倍
  const retryMessage = useCallback(
    (assistantId: string, question: string) => {
      if (!question || sending) return;
      const list = messagesRef.current;
      const i = list.findIndex((m) => m.id === assistantId);
      if (i < 0) return;
      const start = i > 0 && list[i - 1].role === "user" ? i - 1 : i;
      setMessages(list.slice(0, start));
      // 重试沿用同一问题：计划类请求要重新带上 plan 标志，否则卡片不会再生成
      void sendMessage(question, undefined, undefined, isPlanRequest(question));
    },
    [sendMessage, sending, setMessages]
  );

  // 停止当前流式生成（中断 SSE 请求）
  const stopSending = () => {
    abortRef.current?.abort();
  };

  // 定位开关：开 → 先试浏览器定位，公网 http 下自动退到服务端 IP 定位；关 → 仅关闭开关（保留缓存）
  const toggleGeo = async () => {
    if (geoEnabled) {
      setGeoEnabled(false);
      setGeoEnabledState(false);
      return;
    }
    // 这一段最长要 18 秒：等浏览器授权弹窗 10s（用户不点允许/拒绝就一直等到超时），
    // 拿不到再退服务端 IP 定位 8s。不给 pending 反馈的话，用户会以为按钮坏了。
    setGeoPending(true);
    const r = await acquireGeo();
    if (!r) {
      setGeoPending(false);
      /* ★ 这里以前弹一句「定位失败：当前环境不支持浏览器定位，后端也未能按 IP 推断城市
         （需 AMAP_API_KEY）」—— 这是把服务端的配置缺失念给用户听。他既看不懂也修不了，
         只会得出"这应用坏了"的结论。定位拿不到绝大多数是环境限制（http 下浏览器不给坐标、
         服务端没配高德 key），不是用户做错了什么。
         现在改成：不报错，直接把「你在哪个城市」的输入框展开。给出路比说原因有用。 */
      setCityEdit(true);
      return;
    }
    // IP 定位只有市级名（高德 IP 接口的上限）；浏览器定位只有坐标，
    // 要逆地理补出「市 + 区县」——仅角标展示用，失败不影响定位本身。
    // ★ 区县只用于**展示**，绝不进 getSendGeo 的 city 字段：
    //   agent/builder.py 会把它拼成 get_weather(city='...')，带「·」的串查不到城市。
    const manual = getManualCity();
    if (manual) {
      // 用户手填过城市：定位照开（坐标对天气精度仍有价值），但**地名不覆盖他改的**
      setGeoEnabled(true);
      setGeoEnabledState(true);
      setGeoPending(false);
      // 提示语不再说「定位已开启」：针已经变金色、角标已经出现，再说一遍是重复。
      // 这句话只留一件用户真正想知道的事 —— 他手填的城市没被覆盖。
      showToast("success", `城市用你填的${manual}，问天气不用再输`);
      return;
    }
    let place: { city: string; district: string } | null = null;
    if (r.geo.city) {
      place = { city: r.geo.city, district: "" }; // IP 兜底：城市级就到此为止，不编区县
    } else if (typeof r.geo.lat === "number" && typeof r.geo.lon === "number") {
      try {
        place = await reverseGeocodePlace(r.geo.lat, r.geo.lon);
      } catch {
        place = null;
      }
    }
    const label = formatPlace(place);
    if (place) {
      updateCachedPlace(place);
      setGeoCity(label || null);
    }
    setGeoEnabled(true);
    setGeoEnabledState(true);
    setGeoPending(false);
    showToast(
      "success",
      label
        ? r.source === "ip"
          ? `已按 IP 定位到${label}（城市级），问天气无需再输城市`
          : `定位到${label}，问天气无需再输城市`
        : // 拿到了坐标但认不出城市名：角标不会出现，这句话是唯一的反馈，
          // 所以不能吹「问天气无需再输城市」—— 直接告诉他下一步怎么做
          "拿到了位置，但没认出城市名 —— 问天气时直接说城市就行"
    );
  };

  /**
   * 手填城市：用户说了算。
   * 只保存城市名、不保存坐标 —— getSendGeo 会在有手填值时只送 city，
   * 否则后端拿坐标重新逆地理，会把用户改的结果又推回原样，等于没得改。
   */
  const saveCity = (name: string) => {
    const v = setManualCity(name);
    setCityEdit(false);
    if (!v) {
      // 清空 = 撤销手填，角标回到自动定位的结果（可能为空 —— 那就没有地名可显示，
      // 角标整块不出现；定位针仍是金色，状态不丢）
      setGeoCity(formatPlace(getCachedGeo()) || null);
      return;
    }
    setGeoEnabled(true);
    setGeoEnabledState(true);
    setGeoCity(v);
    showToast("success", `已设为${v}，问天气不用再输城市`);
  };

  // 绑定/解绑知识库（跨域：改会话，所以留在编排层）
  const bindKb = async (kbId: string | null) => {
    let sid = activeSid;
    // 绑定是「按会话生效」的，一个会话都没有时先自动建一个（与 sendMessage 的做法一致）。
    // 不能像之前那样直接 return：静默失败等于让用户对着一个看起来坏掉的勾选框反复点。
    if (!sid) {
      try {
        const conv = await api.createSession();
        sid = conv.id;
        setSessions((prev) => [conv, ...prev]);
        setActiveSid(conv.id);
      } catch {
        showToast("error", "绑定失败：无法新建会话，请稍后重试");
        return;
      }
    }
    try {
      const conv = await api.bindKb(sid, kbId);
      setSessions((prev) => prev.map((c) => (c.id === sid ? conv : c)));
      showToast(kbId ? "success" : "info", kbId ? "已绑定知识库" : "已解绑知识库");
    } catch (e) {
      showToast("error", "绑定失败，请稍后重试");
    }
  };

  // 联网建库：逐条抓（带进度）→ 自动绑定当前会话 → 预填一个建议问题（不自动发送）
  // params：面板上选的「处理方式」档位；没传则用全局兜底值
  const webImport = async (
    kbName: string,
    urls: string[],
    onProgress: (i: number, total: number, title: string) => void,
    params?: ChunkParams
  ): Promise<string> => {
    const cs = params?.size ?? chunkSize;
    const co = params?.overlap ?? chunkOverlap;
    // 本次是否新建了库：全失败时要回滚，否则会留一个里面空空的库
    let createdId: string | null = null;
    try {
      // 先把名字对上当前用户已有的库：手打的名字只要大小写/首尾空格略有出入，
      // 直连 createKb 就会用不同 kb_id 再建一个同名库。命中则复用，不新建。
      const norm = kbName.trim().toLowerCase();
      const existing = kbs.find((k) => (k.name || "").trim().toLowerCase() === norm);
      let kb_id: string;
      if (existing) {
        kb_id = existing.kb_id;
      } else {
        kb_id = (await api.createKb(kbName)).kb_id;
        createdId = kb_id;
      }
      let ok = 0;
      for (let i = 0; i < urls.length; i++) {
        const url = urls[i];
        try {
          const r = await api.importUrl(kb_id, url, cs, co);
          ok += 1;
          onProgress(i + 1, urls.length, r.title || url);
        } catch {
          onProgress(i + 1, urls.length, "");
        }
      }
      // 一篇都没抓到、库又是这次新建的：删掉空壳并明确报错，别让用户以为导进去了
      if (ok === 0 && createdId) {
        await api.deleteKb(createdId).catch(() => {});
        createdId = null; // 已清理，外层 catch 不要重复删
        await refreshKbs();
        showToast("error", `这 ${urls.length} 篇都没抓到正文，已取消建库`);
        throw new Error("web-import-empty");
      }
      // 至少抓到一篇才算用上了这套参数，记下来供列表标签和「重新整理」复用
      if (ok > 0) rememberChunk(kb_id, { size: cs, overlap: co });
      await refreshKbs();
      // 不再用 if (activeSid) 挡一道：bindKb 自己会在没有会话时建一个，
      // 否则「联网建库成功但没绑上」会让人以为是导入失败了。
      await bindKb(kb_id);
      const suggested = `根据「${kbName}」里刚导入的 ${ok} 篇资料，帮我做个要点总结`;
      setPrefill({ text: suggested, ts: Date.now() });
      showToast("success", `联网建库完成：成功 ${ok} / ${urls.length}`);
      return kb_id;
    } catch (e) {
      if (createdId) {
        // 其它异常（如 bindKb 失败）同样把空壳清掉
        await api.deleteKb(createdId).catch(() => {});
        createdId = null;
        await refreshKbs();
        showToast("error", "导入失败，已取消建库，请稍后重试");
      } else if ((e as Error)?.message !== "web-import-empty") {
        showToast("error", "导入失败，请稍后重试");
      }
      throw e;
    }
  };

  // 首屏三件套都完成才算 boot 完成（骨架屏统一消失）
  const booting = sessionsLoading || kbsLoading;

  // 抽屉遮罩：窄屏开了左栏，或中/窄屏开了右栏时显示
  const overlayOpen =
    (viewport === "narrow" && sidebarOpen) || (viewport !== "wide" && kbPanelOpen);

  const closeOverlays = useCallback(() => {
    setSidebarOpen(false);
    if (viewport !== "wide") setKbPanelOpen(false);
  }, [viewport, setKbPanelOpen]);

  // 抽屉：Esc 关闭，打开时焦点移入面板，关闭后归还给触发它的按钮
  useEffect(() => {
    if (!overlayOpen) return;
    const opener = document.activeElement as HTMLElement | null;
    const panel = sidebarOpen ? sidebarRef.current : kbRef.current;
    // 等浮层开始滑入再抢焦点，否则会被 transform 过渡打断
    const t = window.setTimeout(() => panel?.focus(), 60);
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      closeOverlays();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      window.clearTimeout(t);
      document.removeEventListener("keydown", onKey);
      opener?.focus?.();
    };
  }, [overlayOpen, sidebarOpen, closeOverlays]);

  // ---- 登录门 ----
  // 必须放在所有 hooks 之后：React 不允许条件调用 hook，但允许提前 return。
  // 身份没确认完先不给工作台，免得空列表闪一下又跳回登录页。
  if (auth.loading) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-canvas">
        <p className="text-fs-sm text-faint">正在确认登录状态...</p>
      </div>
    );
  }
  if (!auth.me) {
    return (
      <AuthGate
        allowSignup={auth.allowSignup}
        onLogin={auth.login}
        onRegister={auth.register}
      />
    );
  }

  return (
    <main className="workbench-main flex overflow-hidden">
      <Sidebar
        sessions={sessions}
        activeSid={activeSid}
        onSelect={(sid) => {
          selectSession(sid);
          setSidebarOpen(false); // 窄屏抽屉里点完会话就收起来
        }}
        onCreate={createSession}
        onOpenTemplates={() => {
          window.location.href = "/templates";
        }}
        onRename={renameSession}
        onDelete={deleteSession}
        kbNameMap={kbNameMap}
        containerRef={sidebarRef}
        loading={booting}
        loadError={bootError}
        onRetryLoad={reloadSessions}
        dark={dark}
        fontScale={fontScale}
        onFontScaleChange={setFontScale}
        onToggleDark={toggleDark}
        overlay={viewport === "narrow"}
        open={sidebarOpen}
        username={auth.me?.user.username}
        quota={auth.me?.quota}
        onOpenUsage={() => setUsageOpen(true)}
        onLogout={() => void auth.logout()}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        <ChatArea
          messages={messages}
          mode={mode}
          sending={sending}
          onModeChange={setMode}
          capToggles={capToggles}
          onSend={sendMessage}
          onStop={stopSending}
          onRetry={retryMessage}
          onNotice={showToast}
          topK={topK}
          onTopKChange={setTopK}
          sessionTitle={sessions.find((s) => s.id === activeSid)?.title ?? null}
          boundKb={boundKb}
          kbCount={kbs.length}
          kbNameMap={kbNameMap}
          onUnbindKb={() => bindKb(null)}
          onToggleKbPanel={() => setKbPanelOpen(!kbPanelOpen)}
          kbPanelOpen={kbPanelOpen}
          onOpenSidebar={viewport === "narrow" ? () => setSidebarOpen(true) : undefined}
          prefill={prefill ?? undefined}
          onDeleteTurn={deleteTurn}
          geoEnabled={geoEnabled}
          geoCity={geoCity}
          geoPending={geoPending}
          onToggleGeo={toggleGeo}
          cityEditOpen={cityEdit}
          onCityEditOpenChange={setCityEdit}
          onSaveCity={saveCity}
          sessionId={activeSid}
          quota={auth.me?.quota}
          onOpenUsage={() => setUsageOpen(true)}
          userId={authedId}
        />
      </div>

      <KnowledgePanel
        kbs={kbs}
        boundKb={boundKb}
        onUpload={async (kbName, files, size, overlap) => {
          const kbId = await uploadFiles(kbName, files, size, overlap);
          // 导入成功就顺手绑上：建库的意图本来就是「要用它」，
          // 之前让用户自己去勾方框，等于把系统分内事推给用户。
          if (kbId) await bindKb(kbId);
        }}
        onSearch={searchPreview}
        onWebImport={webImport}
        onDeleteKb={deleteKb}
        onDeleteFile={deleteKbFile}
        onRebuildKb={rebuildKb}
        kbChunkOf={chunkParamsOf}
        onBind={bindKb}
        containerRef={kbRef}
        loading={booting}
        chunkSize={chunkSize}
        chunkOverlap={chunkOverlap}
        onCollapse={() => setKbPanelOpen(false)}
        overlay={viewport !== "wide"}
        open={kbPanelOpen}
      />

      {/* 抽屉遮罩：点击关闭浮层 */}
      <div
        className={`drawer-backdrop ${overlayOpen ? "show" : ""}`}
        onClick={closeOverlays}
        aria-hidden="true"
      />

      {/* Toast 容器：role/aria-live 让屏幕阅读器能播报操作结果，
          与 console/ui.tsx 的 ToastHost 保持同一份语义（两个渲染点都要带） */}
      <div className="toast-container" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.type}`}>
            <span>{t.msg}</span>
          </div>
        ))}
      </div>

      {/* 用量与反馈看板（portal 到 body，不受三栏布局裁切） */}
      {usageOpen && <UsagePanel onClose={() => setUsageOpen(false)} />}
    </main>
  );
}
