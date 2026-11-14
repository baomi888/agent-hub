"use client";

/**
 * 论坛中心（/forum）· 贴吧三栏版。
 *
 * 回答的是「公开知识库里讨论完，然后呢」：留言板挂在库下面，只能一条一条库去看，
 * 没有"最近大家在聊什么"。这个页面把有板块的话题汇到一处。
 *
 * 几条定下来的规矩（后端也在守，前端只是不误导）：
 *   1. **库内留言不进论坛**。留言是"说一句"（300 字、没有标题），搬上广场
 *      只能拿正文前几个字当标题，满屏都是"第三章中值定理那块……"。
 *   2. **板块按话题类型分，不按课程分**。9 门官方课 + 同学分享的库会越滚越多，
 *      按库分栏最后左栏变成第二个资料广场。
 *   3. **每条帖子都能追回来源库**（操作行右侧的库名 chip）。论坛不是平行世界。
 *   4. **免登录**。没登录的人拿设备 cookie，昵称「玉米NNNN」；登录的人署真名。
 *   5. **先发后审**。发出去就是公开的，管理员事后看到违规才下架，下架必填原因，
 *      作者在「我发的帖」里能看到原因并申诉。所以这里没有"审核中"这个状态。
 *
 * 视觉在 styles/forum-tb.css（前缀 tb-），不再用旧的 fm-* 列表皮肤。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import CornIcon from "@/components/CornIcon";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/hooks/useAuth";
import { fmtNum, fmtWhen } from "@/lib/time";
import type {
  ForumCat,
  ForumMinePage,
  ForumModQueue,
  ForumSort,
  ForumTopic,
  PublicKbInfo,
} from "@/lib/types";

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
  { key: "new", label: "最新回复" },
  { key: "hot", label: "最热" },
];

/**
 * 左栏方块里的一个字。
 *
 * 一个字的方案在旧版上线时验过：同一板块三条帖左边全是"答、答、答"，
 * 一个字不足以区分板块 —— 但那是**帖子列表**里的方块。左栏是七个板块
 * 并排给选，一行一个名字跟着，方块只是个可扫视的锚点，一个字够了。
 */
const CAT_MARK: Record<string, string> = {
  ask: "答",
  res: "求",
  exp: "经",
  team: "组",
  kbfb: "反",
  meta: "站",
};

/**
 * 头像里的字与配色。
 *
 * 颜色按名字哈希取，不随机：随机的话刷新一次头像就换一个色，比全同色更乱。
 * 免登录的昵称「玉米4217」取首字恒为"玉"，所以不同人会长得一样 —— 这是
 * 免登录的代价，但配上时间和板块仍能分清"是不是同一个人刚发的"。
 */
const avatarOf = (name: string) => {
  const n = (name || "?").trim();
  let h = 0;
  for (let i = 0; i < n.length; i += 1) h = (h * 31 + n.charCodeAt(i)) % 4;
  return { ch: n.slice(0, 1), cls: h === 0 ? "" : ` alt${h}` };
};

/** 一行话题在列表里的可点区域就这两处：标题、回复数。不做整卡覆盖层，
 *  因为卡里还有板块按钮，覆盖层会把它盖住。 */
const topicHref = (id: number) => `/forum/${id}`;

export default function ForumPage() {
  const router = useRouter();
  const auth = useAuth();
  /** 登录用户里的管理员。免登录的人恒为 false；后端仍会独立校验，这里只决定显不显示入口 */
  const isAdmin = Boolean(auth.me?.is_admin);

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

  // ---- 发帖 ----
  const [compOpen, setCompOpen] = useState(false);
  const [cTitle, setCTitle] = useState("");
  const [cBody, setCBody] = useState("");
  const [cCat, setCCat] = useState("");
  const [cKb, setCKb] = useState("");
  const [kbs, setKbs] = useState<PublicKbInfo[]>([]);
  const [sending, setSending] = useState(false);
  const [cTip, setCTip] = useState("");
  const titleRef = useRef<HTMLInputElement | null>(null);

  // ---- 我发的帖 ----
  const [mineOpen, setMineOpen] = useState(false);
  const [mine, setMine] = useState<ForumMinePage | null>(null);
  const [mineBusy, setMineBusy] = useState(false);
  const [mineErr, setMineErr] = useState("");
  const [appealing, setAppealing] = useState(0);

  // ---- 管理台 ----
  const [admOpen, setAdmOpen] = useState(false);
  const [adm, setAdm] = useState<ForumModQueue | null>(null);
  const [admBusy, setAdmBusy] = useState(false);
  const [admErr, setAdmErr] = useState("");
  const [admTab, setAdmTab] = useState<"pending" | "appeals">("pending");
  /** 正在选下架原因的那条 id，0 = 没在选 */
  const [picking, setPicking] = useState(0);
  const [reason, setReason] = useState("");
  /** 已处理过的行 → 行内说明。刷新弹层后这些行会自然消失（reviewed_at 已写） */
  const [admDone, setAdmDone] = useState<Record<number, string>>({});

  const rootRef = useRef<HTMLDivElement | null>(null);
  /** 手机上的板块条是否已滚到最右 —— 决定右边那条渐隐遮罩要不要留着 */
  const [chipEnd, setChipEnd] = useState(false);
  const railRef = useRef<HTMLDivElement | null>(null);
  /** 顶部公告条本体。列表底那句「另有 N 条在顶部」点一下要能回去 */
  const noticeRef = useRef<HTMLDivElement | null>(null);

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

  // 手机上的板块条一定放不下七个 chip：右侧不给渐隐，最后那个板块看起来
  // 就是"被切坏了"。滚到尽头再把遮罩撤掉，否则最后一个 chip 会永远淡着半边。
  useEffect(() => {
    const el = railRef.current;
    if (!el) return;
    const sync = () => setChipEnd(el.scrollLeft + el.clientWidth >= el.scrollWidth - 2);
    sync();
    el.addEventListener("scroll", sync, { passive: true });
    window.addEventListener("resize", sync);
    return () => {
      el.removeEventListener("scroll", sync);
      window.removeEventListener("resize", sync);
    };
  }, [cats.length]);

  /**
   * 弹层打开时锁住页面滚动。
   *
   * 锁的是 `.tb-root` 而不是 body —— body 本来就是 overflow:hidden，
   * 真正在滚的是这个容器。锁 body 的话背景照样能滚，手指在遮罩上一划
   * 底下的列表就跟着走。
   */
  const anyModal = compOpen || mineOpen || admOpen;
  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    el.style.overflowY = anyModal ? "hidden" : "";
    return () => {
      el.style.overflowY = "";
    };
  }, [anyModal]);

  // ESC 关最上层那一个。分开写依赖，避免按一次关掉两层。
  useEffect(() => {
    if (!anyModal) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (admOpen) setAdmOpen(false);
      else if (mineOpen) setMineOpen(false);
      else setCompOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [anyModal, admOpen, mineOpen]);

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
    window.requestAnimationFrame(() => titleRef.current?.focus());
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
    // 禁用条件只看标题：这是"论坛"与"图片论坛"的分水岭 —— 写个标题就能发，
    // 图可以不配。正文也要求非空的话，一句"周三晚缺两人"就没法发了。
    if (!title) return setCTip("给话题起个标题");
    if (!cCat) return setCTip("先选一个板块");
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
        router.push(topicHref(r.topic.id));
      } else {
        void load();
      }
    } catch (e) {
      setCTip(e instanceof Error ? e.message : "发送失败，请稍后重试");
    } finally {
      setSending(false);
    }
  };

  const openMine = async () => {
    setMineOpen(true);
    setMineErr("");
    setMineBusy(true);
    try {
      setMine(await api.listMyForumTopics());
    } catch (e) {
      setMineErr(e instanceof Error ? e.message : "读取失败");
    } finally {
      setMineBusy(false);
    }
  };

  const openAdmin = async () => {
    setAdmOpen(true);
    setAdmErr("");
    setAdmBusy(true);
    setPicking(0);
    setReason("");
    setAdmDone({});
    try {
      setAdm(await api.getForumModQueue());
    } catch (e) {
      setAdmErr(e instanceof Error ? e.message : "读取失败");
    } finally {
      setAdmBusy(false);
    }
  };

  const doReview = async (t: ForumTopic, action: "approve" | "remove" | "restore") => {
    if (admBusy) return;
    if (action === "remove" && !reason) return;
    setAdmBusy(true);
    setAdmErr("");
    try {
      await api.reviewForumTopic(t.id, action, action === "remove" ? reason : "");
      const label = adm?.reasons.find((r) => r.key === reason)?.label ?? "";
      setAdmDone((cur) => ({
        ...cur,
        [t.id]:
          action === "approve"
            ? "已通过。作者看不到任何变化 —— 这正是通过该有的样子。"
            : `已下架 · ${label}。作者点开原链接、或在「我发的帖」里，都能看到这条原因和申诉入口。`,
      }));
      setPicking(0);
      setReason("");
      // 计数要跟着变，但列表留着让管理员看见刚处理的是哪条 —— 重新拉队列会把
      // 这行抽走，屏幕上唯一的变化就是"少了一行"，容易以为点错了
      try {
        const q2 = await api.getForumModQueue();
        setAdm((cur) => (cur ? { ...cur, stats: q2.stats } : q2));
      } catch {
        // 计数拉不到不影响判定已经成功
      }
    } catch (e) {
      setAdmErr(e instanceof Error ? e.message : "操作失败");
    } finally {
      setAdmBusy(false);
    }
  };

  const doAppeal = async (t: ForumTopic) => {
    if (appealing) return;
    setAppealing(t.id);
    try {
      await api.appealForumTopic(t.id);
      setMine((cur) =>
        cur
          ? {
              ...cur,
              posts: cur.posts.map((p) =>
                p.id === t.id ? { ...p, appeal_at: Math.floor(Date.now() / 1000) } : p
              ),
            }
          : cur
      );
    } catch (e) {
      setMineErr(e instanceof Error ? e.message : "申请失败");
    } finally {
      setAppealing(0);
    }
  };

  const postable = cats.filter((c) => c.postable);
  const catName = cat ? (cats.find((c) => c.key === cat)?.label ?? "") : "";
  /** 右栏只留"真的有人在聊"的：热榜排序在 0 回复时等于最新，和中间那栏重复 */
  const hotLive = hot.filter((t) => t.reply_n > 0);
  /** 顶部横幅用的那一条。关过一次就不再挡视线 */
  const notice = pinned[0] && String(pinned[0].id) !== closedNotice ? pinned[0] : null;
  /**
   * 公告条已经说了这一条，列表里就不再重复它。
   *
   * 手机上这一条的标题本来会在同一屏出现两次：顶部横幅一次，列表首行一次。
   * 关掉横幅后它要回到列表：否则用户关一次，那一期公告就再也找不到入口了。
   */
  const listPins = notice ? pinned.filter((t) => t.id !== notice.id) : pinned;
  /**
   * 横幅吸走的那条公告不在列表里。
   *
   * 于是左栏写「全部」3、列表只有 2 行 —— 对不上账时读的人第一反应是
   * "是不是还有几条没加载出来"。底部那句就是补这个账的。
   */
  const noticeAside = notice ? pinned.length - listPins.length : 0;

  const jumpToNotice = () => {
    const el = noticeRef.current;
    if (!el) return;
    const calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    el.scrollIntoView({ behavior: calm ? "auto" : "smooth", block: "center" });
    // 只滚不聚焦的话，键盘用户点了还在原地：焦点留在按钮上，Tab 继续从列表往下走
    el.querySelector<HTMLElement>("a,button")?.focus({ preventScroll: true });
  };

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

  /** 一条话题 */
  const renderPost = (t: ForumTopic, isPin = false) => {
    const av = avatarOf(t.author.name);
    const canRemove = isAdmin;
    return (
      <article className="tb-post" key={isPin ? `pin-${t.id}` : t.id}>
        <div className="tb-ph">
          <span className={`tb-av${av.cls}`} aria-hidden="true">
            {av.ch}
          </span>
          <div className="tb-who">
            <div className="tb-who-n">
              {t.author.name}
              {t.author.admin && <span className="tb-tag gold">管理员</span>}
              {isPin && <span className="tb-tag pin">置顶</span>}
            </div>
            <div className="tb-who-m">
              {/* 板块在这里而不是行尾：它回答"这条属于哪儿"，是作者信息的一部分。
                  点一下就去那个板块 —— 读的人常常是顺着一个板块往下看的 */}
              <button
                type="button"
                className="tb-bar-inline"
                onClick={() => setCat(t.cat)}
              >
                {t.cat_label}
              </button>
              <span>·</span>
              <span>{fmtWhen(t.at)}</span>
            </div>
          </div>
        </div>

        <h3 className="tb-title">
          <Link href={topicHref(t.id)}>{t.title}</Link>
        </h3>

        {t.excerpt && <p className="tb-text">{t.excerpt}</p>}

        <div className="tb-acts">
          <Link className="tb-act reply" href={topicHref(t.id)}>
            <CornIcon name="reply" className="h-3.5 w-3.5" />
            {/* 0 条不写「0 条回复」：那等于用一行字宣布"这里什么都没有" */}
            {t.reply_n > 0 ? `${t.reply_n} 条回复` : "回复"}
          </Link>
          {t.mine && (
            <button type="button" className="tb-act" onClick={() => void openMine()}>
              我发的
            </button>
          )}
          {canRemove && t.report_n > 0 && (
            <button type="button" className="tb-act" onClick={() => void openAdmin()}>
              被举报 {t.report_n}
            </button>
          )}
          {t.kb && (
            <Link
              className="tb-act src"
              href={`/library/${encodeURIComponent(t.kb.id)}`}
              title={`来自资料库：${t.kb.name}`}
            >
              <CornIcon name="book" className="h-3.5 w-3.5" />
              <span>{t.kb.name}</span>
            </Link>
          )}
        </div>
      </article>
    );
  };

  const admList = admTab === "pending" ? (adm?.pending ?? []) : (adm?.appeals ?? []);

  return (
    <div className="tb-root" ref={rootRef}>
      <header className="tb-top">
        <div className="tb-top-in">
          {/* 站名就是这一页的 h1。手机上是隐藏的（.tb-logo-t），
              a11y 上仍有标题存在 */}
          <Link href="/" className="tb-logo" aria-label="返回工作台">
            <span className="tb-logo-mark">
              <CornIcon name="task" className="h-4 w-4" />
            </span>
            <h1 className="tb-logo-t">知穗论坛</h1>
          </Link>

          <div className="tb-search">
            <CornIcon name="search" />
            <input
              type="search"
              value={q}
              placeholder="搜标题或正文"
              aria-label="搜索话题"
              onChange={(e) => setQ(e.target.value)}
            />
          </div>

          <div className="tb-top-acts">
            <button type="button" className="tb-new" onClick={openCompose}>
              <CornIcon name="plus" className="h-4 w-4" />
              发帖
            </button>
          </div>
        </div>
      </header>

      <div className="tb-wrap">
        {/* ---------- 左：板块 ---------- */}
        <aside className="tb-rail" aria-label="板块" ref={railRef}>
          <div className={"tb-rail-card" + (chipEnd ? " at-end" : "")}>
            <p className="tb-rail-h">大家在逛</p>
            <button
              type="button"
              className={"tb-bar" + (cat === "" ? " on" : "")}
              aria-current={cat === "" ? "true" : undefined}
              onClick={() => setCat("")}
            >
              <span className="tb-bar-ico" aria-hidden="true">
                全
              </span>
              <span className="tb-bar-n">全部</span>
              <span className={"tb-bar-c" + (stat.total ? "" : " zero")}>
                {fmtNum(stat.total)}
              </span>
            </button>
            {cats.map((c) => (
              <button
                key={c.key}
                type="button"
                className={"tb-bar" + (cat === c.key ? " on" : "")}
                aria-current={cat === c.key ? "true" : undefined}
                onClick={() => setCat(c.key)}
                title={c.hint}
              >
                <span className="tb-bar-ico" aria-hidden="true">
                  {CAT_MARK[c.key] || c.label.slice(0, 1)}
                </span>
                <span className="tb-bar-n">{c.label}</span>
                {/* 0 的计数压到最淡：六个板块里五个是 0 时，一列同样重的数字
                    会让"哪个板块真的有内容"要逐个去读 */}
                <span className={"tb-bar-c" + (c.n ? "" : " zero")}>{fmtNum(c.n)}</span>
              </button>
            ))}

            <p className="tb-rail-h" style={{ marginTop: 14 }}>
              我的
            </p>
            <button type="button" className="tb-bar" onClick={() => void openMine()}>
              <span className="tb-bar-ico" aria-hidden="true">
                帖
              </span>
              <span className="tb-bar-n">我发的帖</span>
              {mine && mine.removed_n > 0 && (
                <span className="tb-bar-c todo" title="其中被下架的条数">
                  {mine.removed_n}
                </span>
              )}
            </button>

            {/* 管理台只给管理员看。权限不是靠藏按钮（后端另有强制校验），
                但按钮不该出现在无权的人眼前 —— 普通同学看到"管理台"只会有两个反应：
                点进去被 403，或者以为这站有人在审他 */}
            {isAdmin && (
              <>
                <p className="tb-rail-h" style={{ marginTop: 14 }}>
                  管理
                </p>
                <button type="button" className="tb-bar" onClick={() => void openAdmin()}>
                  <span className="tb-bar-ico" aria-hidden="true">
                    审
                  </span>
                  <span className="tb-bar-n">管理台</span>
                  {adm && adm.stats.pending > 0 ? (
                    <span className="tb-bar-c todo">{adm.stats.pending}</span>
                  ) : (
                    <span className="tb-bar-c zero">0</span>
                  )}
                </button>
              </>
            )}
          </div>
        </aside>

        {/* ---------- 中：信息流 ---------- */}
        <main className="tb-feed">
          {/* 站务公告横幅：进论坛第一眼就是它。
              只显示最新一条 —— 两条公告叠在一起就成了公告区，反而没人读 */}
          {notice && (
            <div className="tb-notice" ref={noticeRef}>
              <span className="tb-notice-tag">
                <CornIcon name="bell" className="h-3 w-3" />
                公告
              </span>
              <div className="tb-notice-main">
                <Link className="tb-notice-t" href={topicHref(notice.id)}>
                  {notice.title}
                </Link>
                <p className="tb-notice-m">{fmtWhen(notice.at)}</p>
              </div>
              <button
                type="button"
                className="tb-notice-x"
                onClick={closeNotice}
                aria-label="关闭这条公告"
                title="关闭（下一条公告还会出现）"
              >
                <CornIcon name="close" className="h-3.5 w-3.5" />
              </button>
            </div>
          )}

          <div className="tb-sorts">
            {SORTS.map((s) => (
              <button
                key={s.key}
                type="button"
                className={"tb-sort" + (sort === s.key ? " on" : "")}
                aria-pressed={sort === s.key}
                onClick={() => setSort(s.key)}
              >
                {s.label}
              </button>
            ))}
            {/* 统计跟着排序 tab 走，不推到大老远右边：放到最右时它和「最热」
                之间隔着半个屏，窄屏上看着像飘在空里的孤字 */}
            <span className="tb-sort-stat">今日新增 {fmtNum(stat.today)} 条</span>
          </div>

          {loading && (
            <p className="tb-loading" role="status">
              正在加载话题
            </p>
          )}

          {!loading && err && (
            <div className="tb-blank">
              <p className="tb-blank-t">{err}</p>
              <p className="tb-blank-p">网络或服务暂时不通，列表没有变。</p>
              <button type="button" className="tb-ghost" onClick={() => void load()}>
                重试
              </button>
            </div>
          )}

          {/* 空态要能接着做事，不是一句"还没有人发帖"就完了。
              "搜不到"和"板块空着"是两种处境：前者要能退回全量，后者要能迈出第一步。
              公告条吸走唯一一条内容时不算空 —— 那种情况下页面上确实有东西可看 */}
          {!loading && !err && !notice && listPins.length + topics.length === 0 && (
            <div className="tb-blank">
              <p className="tb-blank-t">
                {dq
                  ? `没有找到包含「${dq}」的话题`
                  : cat
                    ? `「${catName}」还没有人发帖`
                    : "还没有人发帖"}
              </p>
              <p className="tb-blank-p">
                {dq
                  ? "换个说法，或者把关键词缩短到两三个字。"
                  : "第一个说话的人，往往能得到最认真的回答。"}
              </p>
              {dq ? (
                <button type="button" className="tb-ghost" onClick={() => setQ("")}>
                  清空搜索
                </button>
              ) : (
                <button type="button" className="tb-new" onClick={openCompose}>
                  <CornIcon name="plus" className="h-4 w-4" />
                  发第一个话题
                </button>
              )}
            </div>
          )}

          {listPins.length + topics.length > 0 && (
            <>
              {listPins.map((t) => renderPost(t, true))}
              {topics.map((t) => renderPost(t))}
              {/* 列表结束得有个句号。三条内容下面直接接一整屏空白，
                  读起来像"还没加载完"，而不是"就这些"。
                  横幅吸走的那条也在这儿交回给读的人 */}
              {!hasMore && (
                <p className="tb-end">
                  <span>到底了</span>
                  {noticeAside > 0 && (
                    <button type="button" className="tb-end-up" onClick={jumpToNotice}>
                      <CornIcon name="arrow" className="h-3 w-3" />
                      另有 {noticeAside} 条公告在顶部
                    </button>
                  )}
                </p>
              )}
            </>
          )}

          {hasMore && sort === "new" && (
            <button
              type="button"
              className="tb-more"
              disabled={moreBusy}
              onClick={() => void loadMore()}
            >
              {moreBusy ? "加载中…" : "加载更早的话题"}
            </button>
          )}
        </main>

        {/* ---------- 右：侧卡 ---------- */}
        <aside className="tb-side" aria-label="说明与热帖">
          <section className="tb-card">
            <h4 className="tb-card-t">
              <CornIcon name="bulb" className="h-4 w-4" />
              第一次来？
            </h4>
            <ul className="tb-rules">
              <li>
                <b>不用注册，也不用登录。</b>昵称自动起。
              </li>
              <li>
                写个标题就能发，<b>正文可以不写</b>。
              </li>
              <li>帖子长期留着；被判违规会下架，<b>原因写「我发的帖」里</b>。</li>
              <li>别发人像正脸、宿舍门牌、身份证。</li>
            </ul>
          </section>

          {/* 只有"有人在接话"的话题攒到 3 条以上，这一栏才单独成列。
              热榜在 0 回复时退化成最新、在只有 1 条时等于把列表第三行再抄一遍 ——
              一栏只有一条的"热帖榜"读起来不是榜，是重复 */}
          {hotLive.length >= 3 && (
            <section className="tb-card">
              <h4 className="tb-card-t">
                <CornIcon name="bolt" className="h-4 w-4" />
                被顶得最多的
              </h4>
              <ol className="tb-hot">
                {hotLive.map((t) => (
                  <li key={t.id}>
                    <Link href={topicHref(t.id)}>
                      <span className="tb-hot-t">{t.title}</span>
                    </Link>
                  </li>
                ))}
              </ol>
            </section>
          )}

          <section className="tb-card">
            <h4 className="tb-card-t">
              <CornIcon name="calendar" className="h-4 w-4" />
              今天
            </h4>
            <p className="tb-today">
              <b>{fmtNum(stat.today)}</b> 条新话题与回复
              <br />
              全站 <b>{fmtNum(stat.total)}</b> 个话题 · <b>{fmtNum(stat.replies)}</b> 条回复
            </p>
          </section>
        </aside>
      </div>

      {/* ---------- 发帖 ---------- */}
      {compOpen && (
        <div
          className="tb-mask on"
          role="dialog"
          aria-modal="true"
          aria-label="发一个新话题"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setCompOpen(false);
          }}
        >
          <div className="tb-comp">
            <div className="tb-comp-h">
              <h2>发一个新话题</h2>
              <button
                type="button"
                className="tb-x"
                onClick={() => setCompOpen(false)}
                aria-label="关闭"
              >
                <CornIcon name="close" className="h-4 w-4" />
              </button>
            </div>

            <div className="tb-compose">
              <div className="tb-field">
                <label className="tb-label" htmlFor="tb-title">
                  标题 <span>必填，最多 {TITLE_MAX} 字</span>
                </label>
                <input
                  id="tb-title"
                  ref={titleRef}
                  className="tb-input"
                  value={cTitle}
                  maxLength={TITLE_MAX}
                  placeholder="一句话说清你要问什么"
                  onChange={(e) => {
                    setCTitle(e.target.value);
                    if (cTip) setCTip("");
                  }}
                />
              </div>

              <div className="tb-field">
                <label className="tb-label" htmlFor="tb-body">
                  正文 <span>选填，最多 {BODY_MAX} 字</span>
                </label>
                <textarea
                  id="tb-body"
                  className="tb-input tb-area"
                  value={cBody}
                  maxLength={BODY_MAX}
                  placeholder="背景、你试过什么、卡在哪一步。写得越具体，越可能有人接话。"
                  onChange={(e) => {
                    setCBody(e.target.value);
                    if (cTip) setCTip("");
                  }}
                />
                <p className="tb-tip">
                  {cBody.length}/{BODY_MAX}
                </p>
              </div>

              <div className="tb-field">
                <p className="tb-label">板块</p>
                <div className="tb-pills">
                  {postable.map((c) => (
                    <button
                      key={c.key}
                      type="button"
                      className={"tb-pill" + (cCat === c.key ? " on" : "")}
                      aria-pressed={cCat === c.key}
                      onClick={() => {
                        setCCat(c.key);
                        if (cTip) setCTip("");
                      }}
                    >
                      {c.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="tb-field">
                <label className="tb-label" htmlFor="tb-kb">
                  关联资料库 <span>选填</span>
                </label>
                <select
                  id="tb-kb"
                  className="tb-select"
                  value={cKb}
                  onChange={(e) => setCKb(e.target.value)}
                >
                  <option value="">不关联</option>
                  {kbs.map((k) => (
                    <option key={k.kb_id} value={k.kb_id}>
                      {k.name}
                    </option>
                  ))}
                </select>
                <p className="tb-tip">
                  选上之后，这条话题也会出现在那个库的留言板里。
                </p>
              </div>

              {cTip && <p className="tb-comp-tip">{cTip}</p>}
            </div>

            <div className="tb-comp-f">
              <p className="tb-comp-note">
                发布后以「{auth.me?.user.nickname || auth.me?.user.username || "玉米NNNN"}」署名。
                发出去就是公开的，管理员事后看到违规会下架并写原因。
              </p>
              <button
                type="button"
                className="tb-pub"
                disabled={sending || !cTitle.trim()}
                onClick={() => void submit()}
              >
                {sending ? "发送中…" : cTitle.trim() ? "发布" : "先写标题"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ---------- 我发的帖 ---------- */}
      {mineOpen && (
        <div
          className="tb-mask on"
          role="dialog"
          aria-modal="true"
          aria-label="我发的帖"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setMineOpen(false);
          }}
        >
          <div className="tb-sheet">
            <div className="tb-sheet-h">
              <h2>我发的帖</h2>
              <p>这台设备发过的。清缓存或换设备就认不出来了 —— 没有账号，认的是浏览器。</p>
              <button
                type="button"
                className="tb-x"
                onClick={() => setMineOpen(false)}
                aria-label="关闭"
              >
                <CornIcon name="close" className="h-4 w-4" />
              </button>
            </div>

            <div className="tb-sheet-b">
              {mineBusy && <p className="tb-loading">正在读取</p>}
              {!mineBusy && mineErr && (
                <p className="tb-adm-empty">
                  {mineErr}{" "}
                  <button type="button" className="tb-ghost" onClick={() => void openMine()}>
                    重试
                  </button>
                </p>
              )}
              {!mineBusy && !mineErr && mine && mine.posts.length === 0 && (
                <p className="tb-adm-empty">这台设备还没发过帖。</p>
              )}
              {!mineBusy &&
                !mineErr &&
                mine?.posts.map((t) => (
                  <div className="tb-my" key={t.id}>
                    <div className="tb-my-h">
                      <h3>
                        {t.status === "removed" ? (
                          t.title
                        ) : (
                          <Link href={topicHref(t.id)}>{t.title}</Link>
                        )}
                      </h3>
                      <span className={"tb-state" + (t.status === "removed" ? " bad" : "")}>
                        {t.status === "removed" ? "已下架" : "已发布"}
                      </span>
                    </div>
                    <p className="tb-my-m">
                      {t.cat_label} · {fmtWhen(t.at)}
                      {t.reply_n > 0 ? ` · ${t.reply_n} 条回复` : ""}
                    </p>

                    {/* 下架说明必须是"打开就能看到"的，不能只在原链接里 ——
                        免登录没有消息中心，这里就是唯一的收件箱 */}
                    {t.status === "removed" && (
                      <div className="tb-gone">
                        <p>
                          违反社区规范，<b>{t.removed_reason || "未填写原因"}</b>
                          {t.removed_at > 0 ? ` · ${fmtWhen(t.removed_at)}` : ""}
                        </p>
                        <p className="tb-gone-hint">
                          原链接打开也是这个说明。确认是误判可以申请复核。
                        </p>
                        {t.appeal_at > 0 ? (
                          <p className="tb-gone-hint">
                            已于 {fmtWhen(t.appeal_at)} 申请复核，管理员会再看一遍。
                          </p>
                        ) : (
                          <button
                            type="button"
                            className="tb-appeal"
                            disabled={appealing === t.id}
                            onClick={() => void doAppeal(t)}
                          >
                            {appealing === t.id ? "提交中…" : "申请复核"}
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                ))}
            </div>

            <div className="tb-sheet-f">
              <p>
                <b>先发后审：</b>帖子发出去就是公开的，管理员事后看到违规才会下架。
                下架一定会写原因，就在上面那条红框里 —— 不写原因的下架，作者只会觉得东西被吞了。
              </p>
            </div>
          </div>
        </div>
      )}

      {/* ---------- 管理台 ---------- */}
      {admOpen && (
        <div
          className="tb-mask on"
          role="dialog"
          aria-modal="true"
          aria-label="管理台"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setAdmOpen(false);
          }}
        >
          <div className="tb-admin">
            <div className="tb-admin-h">
              <h2>管理台</h2>
              <p>帖子已经公开了才轮到你看。下架必须填原因 —— 不填，作者只会觉得东西被吞了。</p>
              <button
                type="button"
                className="tb-x"
                onClick={() => setAdmOpen(false)}
                aria-label="关闭"
              >
                <CornIcon name="close" className="h-4 w-4" />
              </button>
            </div>

            <div className="tb-adm-tabs" role="tablist">
              <button
                type="button"
                role="tab"
                aria-selected={admTab === "pending"}
                className={"tb-adm-tab" + (admTab === "pending" ? " on" : "")}
                onClick={() => setAdmTab("pending")}
              >
                待审 {adm ? <b>{adm.stats.pending}</b> : null}
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={admTab === "appeals"}
                className={"tb-adm-tab" + (admTab === "appeals" ? " on" : "")}
                onClick={() => setAdmTab("appeals")}
              >
                申诉 {adm ? <b>{adm.stats.appeals}</b> : null}
              </button>
            </div>

            <div className="tb-adm-list">
              {admBusy && !adm && <p className="tb-loading">正在读取</p>}
              {admErr && (
                <p className="tb-adm-empty">
                  {admErr}{" "}
                  <button type="button" className="tb-ghost" onClick={() => void openAdmin()}>
                    重试
                  </button>
                </p>
              )}
              {adm && admList.length === 0 && (
                <p className="tb-adm-empty">
                  {admTab === "pending" ? "没有待审的帖子。" : "没有待复核的申诉。"}
                </p>
              )}
              {adm &&
                admList.map((t) => {
                  const done = admDone[t.id];
                  const isPicking = picking === t.id;
                  return (
                    <div
                      className={
                        "tb-adm-row" + (isPicking ? " picking" : "") + (done ? " done" : "")
                      }
                      key={t.id}
                    >
                      <div className="tb-adm-top">
                        <div className="tb-adm-main">
                          <h3>
                            <Link href={topicHref(t.id)} target="_blank">
                              {t.title}
                            </Link>
                          </h3>
                          <p className="tb-adm-m">
                            {t.author.name} · {t.cat_label} · {fmtWhen(t.at)}
                            {t.reply_n > 0 ? ` · ${t.reply_n} 条回复` : ""}
                            {t.report_n > 0 ? ` · 被举报 ${t.report_n} 次` : ""}
                          </p>
                          {t.excerpt && <p className="tb-adm-q">{t.excerpt}</p>}
                        </div>
                        <div className="tb-adm-acts">
                          {t.status === "removed" ? (
                            <button
                              type="button"
                              className="tb-adm-ok"
                              disabled={admBusy}
                              onClick={() => void doReview(t, "restore")}
                            >
                              撤销下架
                            </button>
                          ) : (
                            <>
                              <button
                                type="button"
                                className="tb-adm-ok"
                                disabled={admBusy}
                                onClick={() => void doReview(t, "approve")}
                              >
                                通过
                              </button>
                              <button
                                type="button"
                                className="tb-adm-no"
                                disabled={admBusy}
                                onClick={() => {
                                  setPicking(isPicking ? 0 : t.id);
                                  setReason("");
                                }}
                              >
                                下架
                              </button>
                            </>
                          )}
                        </div>
                      </div>

                      {/* 原因必须点开才出现：一进来就摊开四个选项，会让人以为
                          每个帖子都得判一次，而绝大多数帖子（哪怕有点糙）并不该下架 */}
                      <div className="tb-reasons">
                        {adm.reasons.map((r) => (
                          <button
                            type="button"
                            key={r.key}
                            className={"tb-reason" + (reason === r.key ? " on" : "")}
                            onClick={() => setReason(r.key)}
                          >
                            {r.label}
                          </button>
                        ))}
                        <button
                          type="button"
                          className={"tb-adm-go" + (reason ? " ready" : "")}
                          disabled={!reason || admBusy}
                          onClick={() => void doReview(t, "remove")}
                        >
                          确认下架
                        </button>
                      </div>

                      {done && <p className="tb-adm-done">{done}</p>}
                    </div>
                  );
                })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
