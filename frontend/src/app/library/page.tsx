"use client";

/**
 * 公开知识库广场（/library）。
 *
 * 2026-11-13 起取代原来的「模板广场」（/templates 现在重定向到这里）。
 * 为什么换：模板广场回答的是「我不知道拿 AI 干什么」，但它给的是一套
 * 预置提示词；而用户真正缺的常常是**可以问的资料**。站内公开知识库上线后，
 * 别人的资料本来就能挂进自己的对话检索，缺的只是一个"能看见、能挑"的地方。
 * 所以把"挑模板"换成"挑资料库"——同一块位置、同一件事，但交付物更实。
 *
 * 三条边界（后端也在守，前端只是不误导）：
 *   1. 别人的库只能读：挂进对话做检索，看不到文件清单、改不了内容；
 *   2. 自己的库主动公开才会出现在这里，随时可以收回；
 *   3. 简介是唯一由库主写的展示信息 —— 不写就不编，卡片上直说"还没写简介"。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import MascotLogo from "@/components/chat/MascotLogo";
import CornIcon from "@/components/CornIcon";
import AuthGate from "@/components/AuthGate";
import { ToastHost } from "@/components/console/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/hooks/useAuth";
import { useToast } from "@/lib/hooks/useToast";
import { fmtNum, fmtWhen } from "@/lib/time";
import type { KbInfo, PublicKbInfo } from "@/lib/types";

/** 简介上限，与后端 VisibilityRequest.desc 的 max_length 保持一致 */
const DESC_MAX = 200;

type Sort = "new" | "big";

/** 卡片左侧那个方块里的字：取库名第一个字符，空名兜一个「库」 */
const markOf = (name: string) => (name || "").trim().slice(0, 1) || "库";

export default function LibraryPage() {
  const auth = useAuth();
  const router = useRouter();
  const { toasts, showToast } = useToast();

  const [pub, setPub] = useState<PublicKbInfo[]>([]);
  const [mine, setMine] = useState<KbInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<Sort>("new");
  /** 正在为哪个库建会话（卡片按钮转圈用） */
  const [opening, setOpening] = useState<string | null>(null);

  const [shareOpen, setShareOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      // 两个接口互不依赖，并行拉：广场列表 + 我的库（"我分享的"分组要用）
      const [p, m] = await Promise.all([api.listPublicKbs(), api.listKbs()]);
      setPub(p.kbs);
      setMine(m.kbs);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "加载失败，请稍后重试");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!auth.me) return;
    // 放宏任务里再跑：load 一进去就 setLoading，直接在 effect 体里调会被
    // react-hooks/set-state-in-effect 判成级联渲染（useAuth 里同样的处理）
    const t = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(t);
  }, [auth.me, load]);

  const minePublic = useMemo(() => mine.filter((k) => k.public), [mine]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const hit = pub.filter(
      (k) =>
        !q ||
        k.name.toLowerCase().includes(q) ||
        (k.desc || "").toLowerCase().includes(q) ||
        (k.owner_name || "").toLowerCase().includes(q),
    );
    // 排序都带第二关键字：刚分享的几个库如果段数一样，顺序不该每次刷新都变
    return [...hit].sort((a, b) =>
      sort === "new" ? b.at - a.at || b.chunks - a.chunks : b.chunks - a.chunks || b.at - a.at,
    );
  }, [pub, query, sort]);

  /** 从广场挂一个库：建好会话时就把 kb_id 绑上，进去直接能问 */
  const openInChat = async (k: PublicKbInfo) => {
    if (opening) return;
    setOpening(k.kb_id);
    try {
      const conv = await api.createSession(`关于「${k.name}」的提问`, k.kb_id);
      router.push(`/?sid=${encodeURIComponent(conv.id)}`);
    } catch (e) {
      showToast("error", e instanceof Error ? e.message : "打开对话失败，请稍后重试");
      setOpening(null);
    }
  };

  if (auth.loading) {
    return (
      <div className="lib-boot">
        <p>正在确认登录状态...</p>
      </div>
    );
  }
  if (!auth.me) {
    return <AuthGate allowSignup={auth.allowSignup} onLogin={auth.login} onRegister={auth.register} />;
  }

  return (
    <div className="lib-root">
      <header className="lib-top">
        <div className="lib-top-in">
          <MascotLogo className="lib-logo" />
          <div className="lib-head-text">
            <h1>公开知识库</h1>
            <p>别人整理好的资料，挂进对话就能问</p>
          </div>
          <Link href="/" className="lib-back" aria-label="返回工作台">
            <CornIcon name="back" className="h-4 w-4" />
            <span>工作台</span>
          </Link>
        </div>
      </header>

      <main className="lib-main">
        <section className="lib-hero">
          <div>
            <h2>这里放的是站内用户主动分享的资料库</h2>
            <p>
              挑一个挂进自己的对话，它就会拿里面的内容回答你。
              <strong>你能查，但看不到文件清单、也改不了内容</strong>——这些仍然只有库主能做。
            </p>
          </div>
          <button type="button" className="lib-share" onClick={() => setShareOpen(true)}>
            <CornIcon name="upload" className="h-4 w-4" />
            分享我的知识库
          </button>
        </section>

        {minePublic.length > 0 && (
          <section className="lib-sec">
            <div className="lib-sec-head">
              <span className="eyebrow">我分享的</span>
              <span className="rule" />
              <span className="lib-count">{minePublic.length} 个</span>
            </div>
            <ul className="lib-mine">
              {minePublic.map((k) => (
                <li key={k.kb_id} className="lib-mine-row">
                  <span className="lib-mark lib-mark-sm" aria-hidden="true">
                    {markOf(k.name)}
                  </span>
                  <b className="lib-mine-name">{k.name}</b>
                  <span className="lib-mine-desc">{k.public_desc?.trim() || "还没写简介"}</span>
                  <span className="lib-mine-badge">公开中</span>
                  <Link href="/knowledge" className="lib-link">
                    管理
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        )}

        <div className="lib-tools">
          <label className="lib-search">
            <CornIcon name="search" className="h-4 w-4" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜库名、简介或作者"
              aria-label="搜索公开知识库"
            />
            {query && (
              <button type="button" onClick={() => setQuery("")} aria-label="清空搜索">
                <CornIcon name="close" className="h-3.5 w-3.5" />
              </button>
            )}
          </label>
          <div className="lib-sort" role="group" aria-label="排序方式">
            <button type="button" aria-pressed={sort === "new"} onClick={() => setSort("new")}>
              最近分享
            </button>
            <button type="button" aria-pressed={sort === "big"} onClick={() => setSort("big")}>
              资料最多
            </button>
          </div>
          <span className="lib-count">{filtered.length} 个库</span>
        </div>

        {loading && (
          <div className="lib-grid" aria-busy="true">
            {[0, 1, 2, 3, 4, 5].map((i) => (
              <div key={i} className="lib-card lib-skel" />
            ))}
          </div>
        )}

        {!loading && err && (
          <div className="lib-empty">
            <p className="lib-empty-t">{err}</p>
            <button type="button" className="lib-share" onClick={() => void load()}>
              <CornIcon name="refresh" className="h-4 w-4" />
              重试
            </button>
          </div>
        )}

        {!loading && !err && filtered.length === 0 && (
          <div className="lib-empty">
            <CornIcon name="store" className="lib-empty-icon" />
            {query ? (
              <>
                <p className="lib-empty-t">没搜到「{query}」相关的库</p>
                <p className="lib-empty-d">换个词试试，或者清空搜索看看全部。</p>
                <button type="button" className="lib-ghost" onClick={() => setQuery("")}>
                  清空搜索
                </button>
              </>
            ) : (
              <>
                <p className="lib-empty-t">还没有人分享资料库</p>
                <p className="lib-empty-d">
                  你可以做第一个：把整理好的资料公开出来，别人能查、改不了。
                </p>
                <button type="button" className="lib-share" onClick={() => setShareOpen(true)}>
                  <CornIcon name="upload" className="h-4 w-4" />
                  分享我的知识库
                </button>
              </>
            )}
          </div>
        )}

        {!loading && !err && filtered.length > 0 && (
          <ul className="lib-grid">
            {filtered.map((k) => (
              <li key={k.kb_id} className="lib-card">
                <div className="lib-card-h">
                  <span className="lib-mark" aria-hidden="true">
                    {markOf(k.name)}
                  </span>
                  <h3 className="lib-name" title={k.name}>
                    {k.name}
                  </h3>
                </div>
                <p className={"lib-card-d" + (k.desc?.trim() ? "" : " is-none")}>
                  {k.desc?.trim() || "这个库还没写简介"}
                </p>
                <div className="lib-card-m">
                  <span>{fmtNum(k.files)} 份资料</span>
                  <span className="lib-dot" />
                  <span>{fmtNum(k.chunks)} 段</span>
                  <span className="lib-owner" title="分享者">
                    <CornIcon name="user" className="h-3 w-3" />
                    {k.owner_name || "某位同学"}
                  </span>
                </div>
                <div className="lib-card-f">
                  <span className="lib-when">{k.at ? fmtWhen(k.at) + "分享" : "较早分享"}</span>
                  <button
                    type="button"
                    className="lib-open"
                    disabled={opening !== null}
                    onClick={() => void openInChat(k)}
                  >
                    {opening === k.kb_id ? "打开中…" : "挂到我的对话"}
                    {opening !== k.kb_id && <CornIcon name="arrow" className="h-3.5 w-3.5" />}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}

        <p className="lib-foot">
          只能检索，不能修改。分享是自愿的，库主随时可以收回 —— 收回后这些库会立刻从这里消失。
        </p>
      </main>

      {shareOpen && (
        <ShareSheet
          kbs={mine}
          onClose={() => setShareOpen(false)}
          onDone={async (name) => {
            setShareOpen(false);
            await load();
            showToast("success", `「${name}」已出现在广场上`);
          }}
        />
      )}

      <ToastHost toasts={toasts} />
    </div>
  );
}

/** 分享弹层：选一个库 → 写一句简介 → 公开。
 *
 *  为什么做成弹层而不是跳去 /knowledge：分享是"在广场上顺手做的一件事"，
 *  跳走再回来会丢掉用户刚才在看的列表和搜索词。 */
function ShareSheet({
  kbs,
  onClose,
  onDone,
}: {
  kbs: KbInfo[];
  onClose: () => void;
  onDone: (name: string) => Promise<void> | void;
}) {
  const usable = useMemo(() => kbs.filter((k) => k.chunks > 0), [kbs]);
  const [picked, setPicked] = useState<string | null>(usable[0]?.kb_id ?? null);
  const [desc, setDesc] = useState(usable[0]?.public_desc ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const firstRef = useRef<HTMLButtonElement | null>(null);

  // 打开就把焦点放进来：键盘用户不该还停在背后那张卡片上
  useEffect(() => {
    firstRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const pick = (k: KbInfo) => {
    setPicked(k.kb_id);
    // 之前公开过、写过简介的，接着用；没写过的从空白开始
    setDesc(k.public_desc ?? "");
    setErr(null);
  };

  const submit = async () => {
    const k = usable.find((x) => x.kb_id === picked);
    if (!k || busy) return;
    setBusy(true);
    setErr(null);
    try {
      await api.setKbPublic(k.kb_id, true, desc.trim());
      await onDone(k.name);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "分享失败，请稍后重试");
      setBusy(false);
    }
  };

  return (
    <div className="lib-veil" onClick={onClose} role="presentation">
      <div
        className="lib-sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby="lib-share-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="lib-sheet-h">
          <h2 id="lib-share-title">分享我的知识库</h2>
          <button type="button" className="lib-sheet-x" onClick={onClose} aria-label="关闭">
            <CornIcon name="close" className="h-4 w-4" />
          </button>
        </div>

        {usable.length === 0 ? (
          <div className="lib-sheet-empty">
            <p className="lib-empty-t">
              {kbs.length === 0 ? "你还没有知识库" : "你的知识库里还没有资料"}
            </p>
            <p className="lib-empty-d">
              {kbs.length === 0
                ? "先去知识库传一份资料，再回来分享。"
                : "空库分享出去别人也检索不到东西，先传几份资料吧。"}
            </p>
            <Link href="/knowledge" className="lib-share">
              <CornIcon name="book" className="h-4 w-4" />
              去知识库
            </Link>
          </div>
        ) : (
          <>
            <p className="lib-sheet-lead">
              选一个库，写一句它是干什么的。公开后全站登录用户都能在对话里检索它，
              <strong>文件清单和修改仍然只有你能做</strong>，随时可以收回。
            </p>

            <div className="lib-sheet-label">选哪个库</div>
            <ul className="lib-pick">
              {usable.map((k, i) => (
                <li key={k.kb_id}>
                  <button
                    type="button"
                    ref={i === 0 ? firstRef : undefined}
                    className={"lib-pick-item" + (picked === k.kb_id ? " on" : "")}
                    aria-pressed={picked === k.kb_id}
                    onClick={() => pick(k)}
                  >
                    <span className="lib-mark lib-mark-sm" aria-hidden="true">
                      {markOf(k.name)}
                    </span>
                    <span className="lib-pick-main">
                      <b>{k.name}</b>
                      <span>
                        {fmtNum(k.files.length)} 份资料 · {fmtNum(k.chunks)} 段
                        {k.public ? " · 已公开" : ""}
                      </span>
                    </span>
                    {picked === k.kb_id && <CornIcon name="check" className="h-4 w-4" />}
                  </button>
                </li>
              ))}
            </ul>

            <label className="lib-sheet-label" htmlFor="lib-desc">
              一句话简介
              <span className="lib-sheet-count">
                {desc.length}/{DESC_MAX}
              </span>
            </label>
            <textarea
              id="lib-desc"
              className="lib-desc"
              value={desc}
              maxLength={DESC_MAX}
              rows={3}
              placeholder="例：考研数学一，近十年真题按章节整理，含错题标注"
              onChange={(e) => setDesc(e.target.value)}
            />

            {err && <p className="lib-sheet-err">{err}</p>}

            <div className="lib-sheet-f">
              <button type="button" className="lib-ghost" onClick={onClose} disabled={busy}>
                先不分享
              </button>
              <button
                type="button"
                className="lib-share"
                disabled={busy || !picked}
                onClick={() => void submit()}
              >
                {busy ? "分享中…" : "确认分享"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
