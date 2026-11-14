"use client";

/**
 * 论坛中心（/forum）。
 *
 * 回答的是「公开知识库里讨论完，然后呢」：留言板挂在库下面，只能一条一条库去看，
 * 没有"最近大家在聊什么"。这个页面把有板块的话题汇到一处。
 *
 * 几条定下来的规矩（后端也在守，前端只是不误导）：
 *   1. **库内留言不进论坛**。留言是"说一句"（300 字、没有标题），搬上广场
 *      只能拿正文前几个字当标题，满屏都是"第三章中值定理那块……"。
 *      真要在论坛里讨论某个库，发话题时选上它 —— 那条会同时挂在库下面。
 *   2. **板块按话题类型分，不按课程分**。9 门官方课 + 同学分享的库会越滚越多，
 *      按库分栏最后左栏变成第二个资料广场。
 *   3. **每条帖子都能追回来源库**（`.fm-src` chip）。论坛不是平行世界。
 *   4. 允许开不挂库的纯话题（"周三晚图书馆缺两人"挂不到任何库上）。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import AuthGate from "@/components/AuthGate";
import CornIcon from "@/components/CornIcon";
import MascotLogo from "@/components/chat/MascotLogo";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/hooks/useAuth";
import { fmtNum, fmtWhen } from "@/lib/time";
import type { ForumCat, ForumSort, ForumTopic, PublicKbInfo } from "@/lib/types";

/** 标题上限，与后端 kb_board.MAX_TITLE 一致 */
const TITLE_MAX = 60;
/** 正文上限，与后端 kb_board.TOPIC_LEN 一致 */
const BODY_MAX = 2000;

/**
 * 顶部公告条「关掉过」的标记。
 *
 * 存的是**公告 id** 而不是一个布尔值：下一期公告发出来 id 变了，条子自己会
 * 重新出现。存 true/false 的话，关过一次以后所有人永远看不到任何公告。
 */
const NOTICE_KEY = "baomi:forum-notice-closed";

/**
 * 两种排序，没有「还没解决」。
 *
 * 这个论坛是拿来讨论和分享的，不是答疑系统 —— 没有"提问—解决"的闭环，
 * 挂一个"未解决"筛选只会让人以为该有人来认领这些问题。
 */
const SORTS: { key: ForumSort; label: string }[] = [
  { key: "new", label: "最新" },
  { key: "hot", label: "最热" },
];

/**
 * 左侧方块里的字（两个字，横排）。
 *
 * 为什么是两个字而不是一个：一个字的方案上线后截图一看，同一板块的三条帖
 * 左边全是"答、答、答"—— 一个字的信息量不足以区分，等于没有标识作用。
 * 两个字（"答疑 / 求资 / 经验 / 组队 / 反馈 / 公告"）刚好把板块说完，
 * 而且不用再给六个板块配色。
 *
 * 有来源库时用库名前两个字（"高等数学"→"高等"）：那条帖讨论的是这个库，
 * 方块就该说库而不是板块。
 */
const CAT_MARK: Record<string, string> = {
  ask: "答疑",
  res: "求资",
  exp: "经验",
  team: "组队",
  kbfb: "反馈",
  meta: "公告",
};

const markOf = (t: ForumTopic) =>
  (t.kb?.name || CAT_MARK[t.cat] || t.cat_label || "帖子").trim().slice(0, 2);

export default function ForumPage() {
  const auth = useAuth();
  const router = useRouter();

  const [cats, setCats] = useState<ForumCat[]>([]);
  const [stat, setStat] = useState({ total: 0, replies: 0, today: 0 });
  const [topics, setTopics] = useState<ForumTopic[]>([]);
  /** 置顶的站务公告，最多 2 条。后端只在「全部话题 + 第一页」时给 */
  const [pinned, setPinned] = useState<ForumTopic[]>([]);
  /**
   * 用户关掉过哪一条公告（存 id）。空串 = 没关过。
   *
   * 初始化时直接读 localStorage 而不是在 effect 里补一次：公告条要等接口
   * 回来才有内容，首屏渲染时 `pinned` 一定是空的，所以这里早读晚读都不会
   * 出现"先闪一下再消失"。
   */
  const [closedNotice, setClosedNotice] = useState(() => {
    if (typeof window === "undefined") return "";
    try {
      return window.localStorage.getItem(NOTICE_KEY) || "";
    } catch {
      return "";
    }
  });
  const [hot, setHot] = useState<ForumTopic[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [sort, setSort] = useState<ForumSort>("new");
  /** 当前板块，空串 = 全部 */
  const [cat, setCat] = useState("");
  /**
   * 搜索框的字与真正发出去的字分开。
   * 直接绑一个 q 的话每敲一个字就是一次请求，中文输入法拼一个词要发四五次，
   * 而且先到后到的响应会互相覆盖（看到的结果和输入框里的字对不上）。
   */
  const [q, setQ] = useState("");
  const [dq, setDq] = useState("");
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [moreBusy, setMoreBusy] = useState(false);

  const [compOpen, setCompOpen] = useState(false);
  const [cTitle, setCTitle] = useState("");
  const [cBody, setCBody] = useState("");
  const [cCat, setCCat] = useState("");
  const [cKb, setCKb] = useState("");
  const [kbs, setKbs] = useState<PublicKbInfo[]>([]);
  const [sending, setSending] = useState(false);
  const [cTip, setCTip] = useState("");
  const compRef = useRef<HTMLDivElement | null>(null);

  // 搜索防抖：停止输入 280ms 才发请求。手速快的人一次请求都不会多发。
  useEffect(() => {
    const t = window.setTimeout(() => setDq(q.trim()), 280);
    return () => window.clearTimeout(t);
  }, [q]);

  const load = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const r = await api.listForumTopics({
        cat: cat || undefined,
        sort,
        q: dq || undefined,
        limit: 20,
      });
      setTopics(r.posts);
      setPinned(r.pinned ?? []);
      setHasMore(r.has_more);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "话题加载失败");
    } finally {
      setLoading(false);
    }
  }, [cat, sort, dq]);

  // 首屏：板块清单 + 热帖栏。两者互不依赖，失败也不该拖垮主列表。
  useEffect(() => {
    const t = window.setTimeout(() => {
      void (async () => {
        try {
          const c = await api.getForumCats();
          setCats(c.cats);
          setStat({ total: c.total, replies: c.replies, today: c.today });
        } catch {
          // 左栏计数缺失不影响读帖，静默降级
        }
        try {
          const h = await api.listForumTopics({ sort: "hot", limit: 5 });
          setHot(h.posts);
        } catch {
          // 右栏是辅助信息，挂了就不显示
        }
      })();
    }, 0);
    return () => window.clearTimeout(t);
  }, []);

  // 列表跟着 板块 / 排序 / 关键词 走。放宏任务里发起，避免在 effect 体里
  // 同步 setState 被判成级联渲染（和 /library 同一个处理）。
  useEffect(() => {
    const t = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(t);
  }, [load]);

  const loadMore = async () => {
    const last = topics[topics.length - 1];
    if (!last || moreBusy) return;
    setMoreBusy(true);
    try {
      const r = await api.listForumTopics({
        cat: cat || undefined,
        sort,
        q: dq || undefined,
        before: last.id,
        limit: 20,
      });
      setTopics((cur) => [...cur, ...r.posts]);
      setHasMore(r.has_more);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "加载失败");
    } finally {
      setMoreBusy(false);
    }
  };

  const openCompose = () => {
    setCompOpen(true);
    setCTip("");
    // 默认选中当前正在看的板块 —— 用户点进"求资料"再点发帖，意思已经很明显了。
    // 但「站务公告」只有管理员能发：普通人点进那个板块再点发帖时，不能把它
    // 带进下拉，否则选项里没有这个 value，下拉框会显示成空白。
    if (!cCat && cat && cats.some((c) => c.key === cat && c.postable)) setCCat(cat);
    window.requestAnimationFrame(() =>
      compRef.current?.scrollIntoView({ block: "center", behavior: "smooth" })
    );
    // 来源库清单只在第一次打开时拉：它是个几十条的下拉，不该每次开面板都拉一遍
    if (kbs.length === 0) {
      void (async () => {
        try {
          const r = await api.listPublicKbs();
          setKbs(r.kbs);
        } catch {
          // 选不到库也能发纯话题
        }
      })();
    }
  };

  const submit = async () => {
    if (sending) return;
    const title = cTitle.trim();
    const body = cBody.trim();
    if (!title) return setCTip("给话题起个标题");
    if (!cCat) return setCTip("先选一个板块");
    if (!body) return setCTip("正文不能为空");
    setSending(true);
    setCTip("");
    try {
      const r = await api.createForumTopic({
        title,
        body,
        cat: cCat,
        ...(cKb ? { kb_id: cKb } : {}),
      });
      setCompOpen(false);
      setCTitle("");
      setCBody("");
      setCKb("");
      if (r.topic) {
        // 发完直接进详情：比停在列表里更符合"我要看我的帖"
        router.push(`/forum/${r.topic.id}`);
      } else {
        void load();
      }
    } catch (e) {
      setCTip(e instanceof Error ? e.message : "发送失败，请稍后重试");
    } finally {
      setSending(false);
    }
  };

  if (auth.loading) {
    return (
      <div className="fm-boot">
        <p>正在确认登录状态...</p>
      </div>
    );
  }
  if (!auth.me) {
    return (
      <AuthGate allowSignup={auth.allowSignup} onLogin={auth.login} onRegister={auth.register} />
    );
  }

  const postable = cats.filter((c) => c.postable);
  const catName = cat ? (cats.find((c) => c.key === cat)?.label ?? "") : "";
  /** 顶部横幅用的那一条。关过一次就不再挡视线 */
  const notice = pinned[0] && String(pinned[0].id) !== closedNotice ? pinned[0] : null;

  const closeNotice = () => {
    if (!notice) return;
    const id = String(notice.id);
    setClosedNotice(id);
    try {
      window.localStorage.setItem(NOTICE_KEY, id);
    } catch {
      // 存不进去也不影响这一次关闭
    }
  };

  /**
   * 一行话题。
   *
   * 置顶行和普通行只有两点不同：多一枚「置顶」标、多一个 `is-pin` 类。
   * 抽成一个函数而不是写两份 JSX —— 两份的话，以后加个作者头像之类的改动，
   * 另一份一定会忘。
   */
  const renderRow = (t: ForumTopic, isPin = false) => (
    <li className={`fm-row${isPin ? " is-pin" : ""}`} key={isPin ? `pin-${t.id}` : t.id}>
      <div className="fm-mark" aria-hidden="true">
        {markOf(t)}
      </div>
      <div className="fm-body">
        <p className="fm-title">
          {isPin && <span className="fm-tag pin">置顶</span>}
          <span className="fm-title-t">{t.title}</span>
          <span className="fm-tag">{t.cat_label}</span>
        </p>
        <p className="fm-ex">{t.excerpt}</p>
        <div className="fm-meta">
          <b>{t.author.name}</b>
          {t.author.admin && <span className="fm-tag gold">管理员</span>}
          <span>{fmtWhen(t.at)}</span>
          {t.kb && (
            <Link className="fm-src" href={`/library/${encodeURIComponent(t.kb.id)}`}>
              <CornIcon name="book" className="h-3 w-3" />
              <span>{t.kb.name}</span>
            </Link>
          )}
        </div>
      </div>
      <div className={"fm-count" + (t.reply_n ? "" : " zero")}>
        <b>{t.reply_n}</b>
        <span>回复</span>
      </div>
      {/* 整行可点靠这张覆盖层链接：`<a>` 里不能嵌 `<button>`，
          而这一行里带着来源库链接，所以不能用 <a> 包内容 */}
      <Link href={`/forum/${t.id}`} className="fm-hit" aria-label={`打开话题 ${t.title}`} />
    </li>
  );

  return (
    <div className="fm-root">
      <header className="fm-top">
        <div className="fm-top-in">
          {/* logo 回工作台：手机上 `.fm-crumb` 是隐藏的，如果没有这个链接，
              从侧栏点进论坛之后整页没有第二个出口，只能靠系统返回手势 */}
          <Link href="/" aria-label="返回工作台">
            <MascotLogo className="fm-logo" />
          </Link>
          <div className="fm-head-text">
            <h1>论坛中心</h1>
          </div>
          <div className="fm-crumb">
            <Link href="/">工作台</Link>
            <i>/</i>
            <Link href="/library">公开知识库</Link>
            <i>·</i>
            <span>{catName || "全部话题"}</span>
          </div>
          <div className="fm-search">
            <CornIcon name="search" />
            <input
              type="search"
              value={q}
              placeholder="搜标题或正文"
              aria-label="搜索话题"
              onChange={(e) => setQ(e.target.value)}
            />
          </div>
          <button type="button" className="fm-new" onClick={openCompose}>
            <CornIcon name="plus" className="h-4 w-4" />
            发新帖
          </button>
        </div>
      </header>

      {/* 站务公告横幅：放在三栏之上、通栏，进论坛第一眼就是它。
          只显示最新一条 —— 两条公告叠在一起就成了公告区，反而没人读 */}
      {notice && (
        <div className="fm-notice">
          <div className="fm-notice-in">
            <span className="fm-notice-tag">
              <CornIcon name="bell" className="h-3.5 w-3.5" />
              公告
            </span>
            <Link className="fm-notice-t" href={`/forum/${notice.id}`}>
              {notice.title}
            </Link>
            <span className="fm-notice-d">{fmtWhen(notice.at)}</span>
            <Link className="fm-notice-more" href={`/forum/${notice.id}`}>
              查看
              <CornIcon name="arrow" className="h-3 w-3" />
            </Link>
            <button
              type="button"
              className="fm-notice-x"
              onClick={closeNotice}
              aria-label="关闭这条公告"
              title="关闭（下一条公告还会出现）"
            >
              <CornIcon name="close" className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      )}

      <div className="fm-wrap">
        {/* ---------- 左栏：板块 ---------- */}
        <aside className="fm-side" aria-label="板块">
          <div className="fm-box">
            <p className="fm-box-t">板块</p>
            <button
              type="button"
              className={"fm-cat" + (cat === "" ? " on" : "")}
              onClick={() => setCat("")}
            >
              全部话题
              <span className="fm-cat-n">{fmtNum(stat.total)}</span>
            </button>
            {cats.map((c) => (
              <button
                key={c.key}
                type="button"
                className={"fm-cat" + (cat === c.key ? " on" : "")}
                onClick={() => setCat(c.key)}
                title={c.hint}
              >
                {c.label}
                <span className="fm-cat-n">{fmtNum(c.n)}</span>
              </button>
            ))}
            <div className="fm-side-stat">
              <span>{fmtNum(stat.replies)} 条回复</span>
              {/* 数字后面必须带单位，否则线上看着像文字被截断 */}
              <span>今日新增 {fmtNum(stat.today)} 条</span>
            </div>
          </div>
        </aside>

        {/* ---------- 中间：列表 ---------- */}
        <main className="fm-main">
          <div className="fm-hero">
            <h2>{catName || "大家都在问什么"}</h2>
            <p>
              公开知识库里的讨论都汇到这里。
              <b>问问题、求资料、写经验、找人自习</b>，都在这儿；
              想讨论某个库里的资料，发帖时把它选上。
            </p>
          </div>

          {/* 手机上左栏隐藏，板块靠这一条横向 chip 选 */}
          <div className="fm-cats-m" role="tablist" aria-label="板块">
            <button
              type="button"
              className={"fm-chip" + (cat === "" ? " on" : "")}
              onClick={() => setCat("")}
            >
              全部
            </button>
            {cats.map((c) => (
              <button
                key={c.key}
                type="button"
                className={"fm-chip" + (cat === c.key ? " on" : "")}
                onClick={() => setCat(c.key)}
              >
                {c.label}
              </button>
            ))}
          </div>

          {compOpen && (
            <div className="fm-comp" ref={compRef}>
              <div className="fm-comp-h">
                <CornIcon name="pencil" className="h-4 w-4" />
                发一个新话题
                <button type="button" onClick={() => setCompOpen(false)}>
                  收起
                </button>
              </div>
              <input
                className="fm-comp-title"
                value={cTitle}
                maxLength={TITLE_MAX}
                placeholder="标题：一句话说清你要问什么"
                aria-label="话题标题"
                onChange={(e) => {
                  setCTitle(e.target.value);
                  if (cTip) setCTip("");
                }}
              />
              <textarea
                value={cBody}
                maxLength={BODY_MAX}
                rows={5}
                placeholder="正文：背景、你试过什么、卡在哪一步。写得越具体，越可能有人接话。"
                aria-label="话题正文"
                onChange={(e) => {
                  setCBody(e.target.value);
                  if (cTip) setCTip("");
                }}
              />
              <div className="fm-comp-bar">
                <div className="fm-cats-sel">
                  {postable.map((c) => (
                    <button
                      key={c.key}
                      type="button"
                      className={"fm-chip" + (cCat === c.key ? " on" : "")}
                      onClick={() => {
                        setCCat(c.key);
                        if (cTip) setCTip("");
                      }}
                    >
                      {c.label}
                    </button>
                  ))}
                </div>
                <span className="fm-comp-count">
                  {cBody.length}/{BODY_MAX}
                </span>
              </div>
              <div className="fm-comp-bar">
                <select
                  className="fm-chip"
                  value={cKb}
                  aria-label="关联资料库（可选）"
                  onChange={(e) => setCKb(e.target.value)}
                >
                  <option value="">不关联资料库</option>
                  {kbs.map((k) => (
                    <option key={k.kb_id} value={k.kb_id}>
                      {k.name}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className="fm-new"
                  style={{ marginLeft: "auto" }}
                  disabled={sending}
                  onClick={() => void submit()}
                >
                  {sending ? "发送中…" : "发布"}
                </button>
              </div>
              {cTip && <p className="fm-comp-tip">{cTip}</p>}
            </div>
          )}

          <div className="fm-bar">
            {SORTS.map((s) => (
              <button
                key={s.key}
                type="button"
                className={"fm-tab" + (sort === s.key ? " on" : "")}
                onClick={() => setSort(s.key)}
              >
                {s.label}
              </button>
            ))}
            <span className="fm-bar-stat">
              {fmtNum(stat.total)} 个话题 · 今日新增 {fmtNum(stat.today)} 条
            </span>
          </div>

          {loading && <p className="fm-empty">正在加载话题…</p>}
          {!loading && err && (
            <p className="fm-empty">
              {err}{" "}
              <button type="button" className="lib-ghost" onClick={() => void load()}>
                重试
              </button>
            </p>
          )}
          {!loading && !err && pinned.length + topics.length === 0 && (
            <p className="fm-empty">
              {dq
                ? `没有找到包含「${dq}」的话题。`
                : "这个板块还没有人发帖，做第一个。"}
            </p>
          )}

          {pinned.length + topics.length > 0 && (
            <ul className="fm-list">
              {pinned.map((t) => renderRow(t, true))}
              {topics.map((t) => renderRow(t))}
            </ul>
          )}

          {hasMore && sort === "new" && (
            <button
              type="button"
              className="lib-ghost fm-more"
              disabled={moreBusy}
              onClick={() => void loadMore()}
            >
              {moreBusy ? "加载中…" : "加载更早的话题"}
            </button>
          )}
        </main>

        {/* ---------- 右栏 ---------- */}
        <aside className="fm-hot" aria-label="热帖">
          <div className="fm-box">
            <p className="fm-box-t">本周热帖</p>
            {hot.length === 0 && <p className="fm-rules">还没有热帖，先发一个。</p>}
            {hot.map((t, i) => (
              <Link key={t.id} className="fm-hot-item" href={`/forum/${t.id}`}>
                <span className="fm-hot-n">{i + 1}</span>
                <span className="fm-hot-t">{t.title}</span>
              </Link>
            ))}
          </div>
          <div className="fm-box">
            <p className="fm-box-t">发帖须知</p>
            <ul className="fm-rules">
              <li>标题写清楚问题，别写「求助」两个字。</li>
              <li>同一件事不要重复开帖，在原帖里补。</li>
              <li>求到的资料请传进知识库，别在楼层里刷外链。</li>
              <li>想补充就在原帖里接着说，不必为同一件事另开一条。</li>
            </ul>
          </div>
        </aside>
      </div>
    </div>
  );
}
