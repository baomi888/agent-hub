"use client";

// 主页面：三栏布局（会话栏 + 对话区 + 知识库面板）与跨域编排
// 2026-09 重构：toast/偏好/会话/知识库各域逻辑拆到 lib/hooks/*，
// 本文件只保留：断点与主题、抽屉管理、聊天流（SSE）、联网建库编排、布局
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import Sidebar from "@/components/Sidebar";
import ChatArea, { type ChatAttachment } from "@/components/ChatArea";
import KnowledgePanel from "@/components/KnowledgePanel";
import { api } from "@/lib/api";
import { streamChat } from "@/lib/sse";
import { uid } from "@/lib/id";
import { useToast } from "@/lib/hooks/useToast";
import { getSendGeo, getCachedGeo, isGeoEnabled, acquireGeo, reverseGeocodeCity, setGeoEnabled, updateCachedCity } from "@/lib/geo";
import { usePrefs } from "@/lib/hooks/usePrefs";
import { useSessions } from "@/lib/hooks/useSessions";
import { useKnowledge } from "@/lib/hooks/useKnowledge";
import { useAuth } from "@/lib/hooks/useAuth";
import AuthGate from "@/components/AuthGate";
import type { UiMessage } from "@/components/chat/types";
import type { ChatMode, RefSource, ToolEvent } from "@/lib/types";

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
  } = useKnowledge(showToast, chunkSize, chunkOverlap);

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
  // 当前定位城市（角标展示用）；从缓存里读出，避免刷新后角标丢失城市名
  const [geoCity, setGeoCity] = useState<string | null>(() => getCachedGeo()?.city ?? null);
  // 定位请求进行中：授权弹窗 + IP 兜底最长 18s，期间按钮转圈，否则看着像没反应
  const [geoPending, setGeoPending] = useState(false);
  // 联网建库后把建议问题预填进输入框（不自动发送），让用户确认/补充
  const [prefill, setPrefill] = useState<{ text: string; ts: number } | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // 抽屉容器：打开时把焦点送进去，关闭后还给触发按钮
  const sidebarRef = useRef<HTMLElement | null>(null);
  const kbRef = useRef<HTMLElement | null>(null);

  // 响应式三档：≥1280 三栏 / 1024–1279 右栏抽屉 / <900 左栏也抽屉
  // 用 useSyncExternalStore 订阅 matchMedia，避免 effect 里 setState 造成的二次渲染
  const viewport = useSyncExternalStore(subscribeViewport, getViewport, () => "wide" as const);
  const [sidebarPref, setSidebarPref] = useState(false);
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
    display?: string
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
    setMessages((prev) => [
      ...prev,
      { id: asstId, role: "assistant", content: "", streaming: true, toolCalls: [] },
    ]);
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
        { sid, question, top_k: topK, mode, attachments: sentAttachments, location: getSendGeo() },
        controller.signal
      );
      let finalRefs = "";
      for await (const ev of gen) {
        switch (ev.event) {
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
            setMessages((prev) =>
              prev.map((m) =>
                m.id === asstId
                  ? { ...m, streaming: false, refs: refs || m.refs, dbId: dbId ?? m.dbId }
                  : m
              )
            );
            break;
          }
          case "error": {
            const detail = String(ev.data.detail ?? "未知错误");
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
      void sendMessage(question);
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
      showToast(
        "error",
        "定位失败：当前环境不支持浏览器定位，后端也未能按 IP 推断城市（需 AMAP_API_KEY）"
      );
      return;
    }
    // IP 定位直接带城市名；浏览器定位只有坐标，要逆地理补一下（仅角标展示用，失败不影响）
    let city: string | null = r.geo.city ?? null;
    if (!city && typeof r.geo.lat === "number" && typeof r.geo.lon === "number") {
      try {
        city = await reverseGeocodeCity(r.geo.lat, r.geo.lon);
      } catch {
        city = null;
      }
    }
    if (city) {
      updateCachedCity(city);
      setGeoCity(city);
    }
    setGeoEnabled(true);
    setGeoEnabledState(true);
    setGeoPending(false);
    showToast(
      "success",
      city
        ? r.source === "ip"
          ? `已按 IP 定位到${city}（城市级），问天气无需再输城市`
          : `定位已开启（${city}），问天气无需再输城市`
        : "定位已开启，问天气无需再输城市"
    );
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
  const webImport = async (
    kbName: string,
    urls: string[],
    onProgress: (i: number, total: number, title: string) => void
  ): Promise<string> => {
    try {
      const { kb_id } = await api.createKb(kbName);
      let ok = 0;
      for (let i = 0; i < urls.length; i++) {
        const url = urls[i];
        try {
          const r = await api.importUrl(kb_id, url, chunkSize, chunkOverlap);
          ok += 1;
          onProgress(i + 1, urls.length, r.title || url);
        } catch {
          onProgress(i + 1, urls.length, "");
        }
      }
      await refreshKbs();
      // 不再用 if (activeSid) 挡一道：bindKb 自己会在没有会话时建一个，
      // 否则「联网建库成功但没绑上」会让人以为是导入失败了。
      await bindKb(kb_id);
      const suggested = `根据「${kbName}」里刚导入的 ${ok} 篇资料，帮我做个要点总结`;
      setPrefill({ text: suggested, ts: Date.now() });
      showToast("success", `联网建库完成：成功 ${ok} / ${urls.length}`);
      return kb_id;
    } catch (e) {
      showToast("error", "导入失败，请稍后重试");
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
    <main className="flex overflow-hidden">
      <Sidebar
        sessions={sessions}
        activeSid={activeSid}
        onSelect={(sid) => {
          selectSession(sid);
          setSidebarOpen(false); // 窄屏抽屉里点完会话就收起来
        }}
        onCreate={createSession}
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
        onLogout={() => void auth.logout()}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        <ChatArea
          messages={messages}
          mode={mode}
          sending={sending}
          onModeChange={setMode}
          onSend={sendMessage}
          onStop={stopSending}
          onRetry={retryMessage}
          onNotice={showToast}
          topK={topK}
          chunkSize={chunkSize}
          chunkOverlap={chunkOverlap}
          onParamChange={({ topK: t, chunkSize: c, chunkOverlap: o }) => {
            if (t !== undefined) setTopK(t);
            if (c !== undefined) setChunkSize(c);
            if (o !== undefined) setChunkOverlap(o);
          }}
          sessionTitle={sessions.find((s) => s.id === activeSid)?.title ?? null}
          boundKb={boundKb}
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
          sessionId={activeSid}
        />
      </div>

      <KnowledgePanel
        kbs={kbs}
        boundKb={boundKb}
        onUpload={uploadFiles}
        onSearch={searchPreview}
        onWebImport={webImport}
        onDeleteKb={deleteKb}
        onDeleteFile={deleteKbFile}
        onRebuildKb={rebuildKb}
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

      {/* Toast 容器 */}
      <div className="toast-container">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.type}`}>
            <span>{t.msg}</span>
          </div>
        ))}
      </div>
    </main>
  );
}
