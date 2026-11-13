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
 *   1. 别人的库只能读：可以挂进对话检索、可以点进去看清单和预览；
 *      **能不能下载由库主决定**（仅观看 / 可下载），改内容永远只有库主能做；
 *   2. 自己的库主动公开才会出现在这里，随时可以收回；
 *   3. 简介是唯一由库主写的展示信息 —— 不写就不编，卡片上直说"还没写简介"。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import MascotLogo from "@/components/chat/MascotLogo";
import CornIcon from "@/components/CornIcon";
import KbBoard from "@/components/library/KbBoard";
import AuthGate from "@/components/AuthGate";
import { ToastHost } from "@/components/console/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/hooks/useAuth";
import { useToast } from "@/lib/hooks/useToast";
import { fmtNum, fmtWhen } from "@/lib/time";
import type { AccessMode, FavoriteKbInfo, KbInfo, PublicKbInfo } from "@/lib/types";

/** 简介上限，与后端 VisibilityRequest.desc 的 max_length 保持一致 */
const DESC_MAX = 200;

/** 排序：最近分享 / 资料最多 / 最受欢迎（浏览 + 收藏） */
type Sort = "new" | "big" | "hot";

/**
 * 热度分：浏览 + 收藏 × 5。
 *
 * 为什么不是一个纯加法：点开看一眼（浏览）和特意收藏留下来，意图强度差一个量级。
 * 一个 100 人看过、0 人收藏的库，和一个 30 人看过、20 人收藏的库，
 * 后者才是"大家真的在用"；等权相加会把它排在后面。
 * 权重写死在注释里而不是配置项 —— 这是产品判断，不是可调参数。
 */
const heatOf = (k: PublicKbInfo) => (k.views ?? 0) + (k.favs ?? 0) * 5;

/** 卡片左侧那个方块里的字：取库名第一个字符，空名兜一个「库」 */
const markOf = (name: string) => (name || "").trim().slice(0, 1) || "库";

export default function LibraryPage() {
  const auth = useAuth();
  const router = useRouter();
  const { toasts, showToast } = useToast();

  const [pub, setPub] = useState<PublicKbInfo[]>([]);
  const [mine, setMine] = useState<KbInfo[]>([]);
  const [favs, setFavs] = useState<FavoriteKbInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<Sort>("new");
  /** 正在为哪个库建会话（卡片按钮转圈用） */
  const [opening, setOpening] = useState<string | null>(null);
  /** 正在等收藏接口返回的库（防连点，也让星标立刻有反馈） */
  const [faving, setFaving] = useState<string | null>(null);
  /**
   * 同名筛选取的库名。不同用户会传来同名的库（"考研数学"能有五六份），
   * 内容不同不能合并，所以给一个"只看这个名字"的开关，配合热度排序挑最值的那个。
   */
  const [sameName, setSameName] = useState<string | null>(null);

  const [shareOpen, setShareOpen] = useState(false);
  /** 正在看哪个库的留言板；null = 没开 */
  const [boardKb, setBoardKb] = useState<PublicKbInfo | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      // 三个接口互不依赖，并行拉：广场列表 + 我的库（"我分享的"分组） + 我的收藏
      const [p, m, f] = await Promise.all([
        api.listPublicKbs(),
        api.listKbs(),
        api.listFavoriteKbs().catch(() => ({ kbs: [] as FavoriteKbInfo[] })),
      ]);
      setPub(p.kbs);
      setMine(m.kbs);
      setFavs(f.kbs);
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

  /** 命中搜索词：库名 / 简介 / 作者 —— 官方和同学库用同一套判定 */
  const hit = useCallback(
    (k: PublicKbInfo) => {
      const q = query.trim().toLowerCase();
      if (!q) return true;
      return (
        k.name.toLowerCase().includes(q) ||
        (k.desc || "").toLowerCase().includes(q) ||
        (k.owner_name || "").toLowerCase().includes(q)
      );
    },
    [query],
  );

  /** 官方课程库：站点自建、带 official 标记。它们出现在最前面，单独一种卡片。 */
  const official = useMemo(() => pub.filter((k) => k.official && hit(k)), [pub, hit]);
  /** 同学分享的库：能看清单、能预览；能不能下载看库主的设置 */
  const shared = useMemo(() => pub.filter((k) => !k.official), [pub]);

  const filtered = useMemo(() => {
    // 同名筛选：只看这一个名字下的多个库，横向比较哪份更值
    const list = shared.filter((k) => hit(k) && (!sameName || k.name.trim() === sameName));
    // 排序都带第二关键字：刚分享的几个库如果段数一样，顺序不该每次刷新都变
    return [...list].sort((a, b) =>
      sort === "new"
        ? b.at - a.at || b.chunks - a.chunks
        : sort === "hot"
          ? // 浏览 + 收藏加权：收藏是"特意留下"，比路过式浏览重得多
            heatOf(b) - heatOf(a) || b.at - a.at
          : b.chunks - a.chunks || b.at - a.at,
    );
  }, [shared, hit, sort, sameName]);

  /**
   * 收藏 / 取消收藏。
   *
   * 先改本地状态再发请求：星标点下去要立刻亮，等一个来回会让人以为没点上、
   * 再点一次又变成取消。失败时回到原状并 toast。
   */
  const toggleFav = async (k: PublicKbInfo) => {
    if (faving) return;
    const on = !k.favorited;
    setFaving(k.kb_id);
    setPub((list) =>
      list.map((x) =>
        x.kb_id === k.kb_id
          ? { ...x, favorited: on, favs: Math.max(0, (x.favs ?? 0) + (on ? 1 : -1)) }
          : x,
      ),
    );
    try {
      const r = on ? await api.addFavorite(k.kb_id) : await api.removeFavorite(k.kb_id);
      // 以后端的计数为准（别人的收藏会同时变，本地加减只是过渡态）
      setPub((list) =>
        list.map((x) => (x.kb_id === k.kb_id ? { ...x, favorited: r.favorited, favs: r.favs } : x)),
      );
      const f = await api.listFavoriteKbs();
      setFavs(f.kbs);
      showToast("success", on ? `已收藏「${k.name}」，在资料库的「我的收藏库」里能找到` : `已取消收藏「${k.name}」`);
    } catch (e) {
      setPub((list) =>
        list.map((x) => (x.kb_id === k.kb_id ? { ...x, favorited: !on, favs: k.favs } : x)),
      );
      showToast("error", e instanceof Error ? e.message : "收藏失败，请稍后重试");
    } finally {
      setFaving(null);
    }
  };

  /** 从广场挂一个库：建好会话时就把 kb_id 绑上，进去直接能问 */
  const openInChat = async (k: { kb_id: string; name: string }) => {
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

  /** 从「我的收藏库」里取消收藏。列表本身就少一条，所以这里直接把它抹掉 */
  const unfav = async (k: FavoriteKbInfo) => {
    try {
      await api.removeFavorite(k.kb_id);
      setFavs((list) => list.filter((x) => x.kb_id !== k.kb_id));
      setPub((list) => list.map((x) => (x.kb_id === k.kb_id ? { ...x, favorited: false } : x)));
      showToast("info", `已取消收藏「${k.name}」`);
    } catch (e) {
      showToast("error", e instanceof Error ? e.message : "取消失败，请稍后重试");
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
            <p>官方课程资料与同学分享的资料库，点开就能看</p>
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
            <h2>官方课程资料，和同学分享的资料库</h2>
            <p>
              <strong>官方库</strong>是学校课程的配套资料，可以点进去看文件、下载原件；
              <strong>同学分享的库</strong>也能点进去看清单和预览，能不能下载由分享的同学决定。
              两者都能挂进你的对话做检索。
            </p>
          </div>
          <button type="button" className="lib-share" onClick={() => setShareOpen(true)}>
            <CornIcon name="upload" className="h-4 w-4" />
            分享我的知识库
          </button>
        </section>

        {official.length > 0 && (
          <section className="lib-sec lib-official">
            <div className="lib-sec-head">
              <span className="eyebrow">官方公开知识库</span>
              <span className="rule" />
              <span className="lib-count">{official.length} 门课程</span>
            </div>
            <p className="lib-sec-lead">
              每门课一个库，收录该课程的官方资料。点进去可以逐份在线看，也可以下载到本地。
            </p>
            <ul className="lib-courses">
              {official.map((k) => (
                <li key={k.kb_id} className="lib-course-wrap">
                  <Link href={`/library/${encodeURIComponent(k.kb_id)}`} className="lib-course">
                    <span className="lib-course-h">
                      <span className="lib-course-mark" aria-hidden="true">
                        {markOf(k.name)}
                      </span>
                      <span className="lib-badge-off">官方</span>
                    </span>
                    <b className="lib-course-name" title={k.name}>
                      {k.name}
                    </b>
                    <span className="lib-course-d">{k.desc?.trim() || "这门课的资料还在整理"}</span>
                    <span className="lib-course-f">
                      <span>{fmtNum(k.files)} 份资料</span>
                      {/* 上传日期：官方库是导入上线那天。用户要求"每个库都写上" */}
                      <span className="lib-course-when">
                        {k.at ? fmtWhen(k.at) : ""}
                        {(k.views ?? 0) > 0 ? ` · ${fmtNum(k.views ?? 0)} 人看过` : ""}
                      </span>
                      <span className="lib-course-go">
                        查看资料
                        <CornIcon name="arrow" className="h-3.5 w-3.5" />
                      </span>
                    </span>
                  </Link>
                  {/* 星标必须是 Link 的**兄弟**而不是子节点：<a> 里放 <button> 是非法嵌套，
                      移动端浏览器会各自按自己的规则修正 DOM，点收藏会连带跳转。 */}
                  <button
                    type="button"
                    className={"lib-fav-btn" + (k.favorited ? " on" : "")}
                    aria-pressed={!!k.favorited}
                    aria-label={`${k.favorited ? "取消收藏" : "收藏"}：${k.name}`}
                    title={k.favorited ? "取消收藏" : "收藏，之后在「我的收藏库」里找得到"}
                    disabled={faving === k.kb_id}
                    onClick={() => void toggleFav(k)}
                  >
                    <CornIcon name="star" className="h-4 w-4" />
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}

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

        {/* 我的收藏库：用户自己挑的那几个。
            用户原话是"收藏后可以在主界面的资料库看到一个新的地方就是我的收藏库"，
            广场这里也放一份 —— 他刚在下面点了星标，就该在这里立刻看到结果。 */}
        {favs.length > 0 && (
          <section className="lib-sec lib-fav-sec">
            <div className="lib-sec-head">
              <span className="eyebrow">我的收藏库</span>
              <span className="rule" />
              <span className="lib-count">{favs.length} 个</span>
            </div>
            <p className="lib-sec-lead">
              收藏的库会出现在你工作台的资料库面板里，随时能挂到对话上检索。
              每条都标了来源和上传日期 —— 官方的、同学分享的，一眼分得清。
            </p>
            <ul className="lib-favs">
              {favs.map((k) => (
                <li key={k.kb_id} className="lib-fav-row">
                  <span className="lib-mark lib-mark-sm" aria-hidden="true">
                    {markOf(k.name)}
                  </span>
                  <div className="lib-fav-main">
                    <b title={k.name}>{k.name}</b>
                    <span className="lib-fav-src">
                      {/* 用户要求：官方写"来源官方知识库"，个人的写"来源某某 的库" */}
                      <span className={"lib-src-tag" + (k.official ? " off" : "")}>
                        来源{k.source}
                      </span>
                      {k.at > 0 && <span>{fmtWhen(k.at)}上传</span>}
                      <span>{fmtNum(k.views)} 人看过</span>
                      <span>{fmtNum(k.files)} 份资料</span>
                      {k.access === "preview" && <span className="lib-access">仅观看</span>}
                    </span>
                  </div>
                  <span className="lib-card-acts">
                    <Link
                      href={`/library/${encodeURIComponent(k.kb_id)}`}
                      className="lib-ghost lib-view-link"
                    >
                      查看资料
                    </Link>
                    <button
                      type="button"
                      className="lib-open"
                      disabled={opening !== null}
                      onClick={() => void openInChat(k)}
                    >
                      {opening === k.kb_id ? "打开中…" : "挂到我的对话"}
                    </button>
                    <button
                      type="button"
                      className="lib-fav-btn on"
                      aria-pressed
                      aria-label={`取消收藏：${k.name}`}
                      title="取消收藏"
                      onClick={() => void unfav(k)}
                    >
                      <CornIcon name="star" className="h-4 w-4" />
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          </section>
        )}

        <section className="lib-sec">
          <div className="lib-sec-head">
            <span className="eyebrow">同学分享的</span>
            <span className="rule" />
          </div>
          <p className="lib-sec-lead">
            挑一个挂进自己的对话，它就会拿里面的内容回答你；也能点进去看资料清单。
            标着「仅观看」的库可以预览，但下载按钮是关着的 —— 那是分享的同学自己选的。
          </p>
        </section>

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
            <button type="button" aria-pressed={sort === "hot"} onClick={() => setSort("hot")}>
              最受欢迎
            </button>
          </div>
          <span className="lib-count">{filtered.length} 个库</span>
        </div>

        {/* 同名筛选生效时的清空条：不加这条，用户会以为"库怎么只剩两个了" */}
        {sameName && (
          <div className="lib-filter-bar">
            <span>
              只看叫「{sameName}」的库（{(pub.find((k) => k.name.trim() === sameName)?.same_name ?? 1)} 个），
              按浏览和收藏排在前面
            </span>
            <button type="button" className="lib-ghost" onClick={() => setSameName(null)}>
              看全部
            </button>
          </div>
        )}

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

        {!loading && !err && filtered.length === 0 && official.length === 0 && (
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
                  <button
                    type="button"
                    className={"lib-fav-btn" + (k.favorited ? " on" : "")}
                    aria-pressed={!!k.favorited}
                    aria-label={`${k.favorited ? "取消收藏" : "收藏"}：${k.name}`}
                    title={k.favorited ? "取消收藏" : "收藏，之后在「我的收藏库」里找得到"}
                    disabled={faving === k.kb_id}
                    onClick={() => void toggleFav(k)}
                  >
                    <CornIcon name="star" className="h-4 w-4" />
                  </button>
                </div>
                {/* 同名库：不同同学传来同名的库，内容不同不能替用户合并，
                    但要说清"这个名字下还有 N 个"，并给一个横向比较的入口。 */}
                {(k.same_name ?? 1) > 1 && (
                  <button
                    type="button"
                    className="lib-same"
                    onClick={() => {
                      setSameName(k.name.trim());
                      // 挑同名库时默认按热度排：浏览和收藏多的排前面，用户要的就是这个
                      setSort("hot");
                    }}
                  >
                    <CornIcon name="notes" className="h-3 w-3" />
                    同名共 {k.same_name} 个，按热度比一比
                  </button>
                )}
                <p className={"lib-card-d" + (k.desc?.trim() ? "" : " is-none")}>
                  {k.desc?.trim() || "这个库还没写简介"}
                </p>
                <div className="lib-card-m">
                  <span>{fmtNum(k.files)} 份资料</span>
                  <span className="lib-dot" />
                  <span>{fmtNum(k.chunks)} 段</span>
                  <span className="lib-dot" />
                  {/* 浏览数：独立访客。0 也显示 —— "还没人看过"本身就是信息，
                      而且它是"最多人看"排序的依据，藏起来排序就成了黑箱 */}
                  <span title="看过这个库的人数">{fmtNum(k.views ?? 0)} 人看过</span>
                  <span title="收藏人数">{fmtNum(k.favs ?? 0)} 人收藏</span>
                  <span className="lib-owner" title="分享者">
                    <CornIcon name="user" className="h-3 w-3" />
                    {k.owner_name || "某位同学"}
                  </span>
                  {/* 只有"仅观看"才需要说明：可下载是常态，不用额外加一个标 */}
                  {k.access === "preview" && (
                    <span className="lib-access" title="库主只允许在线观看，不提供下载">
                      仅观看
                    </span>
                  )}
                </div>
                <div className="lib-card-f">
                  <span className="lib-when">{k.at ? fmtWhen(k.at) + "分享" : "较早分享"}</span>
                  <span className="lib-card-acts">
                    {/* 查看资料：2026-11-13 起同学分享的库也有详情页（清单 + 预览），
                        能不能下载由库主设的 access 决定，这里不做判断。 */}
                    <Link
                      href={`/library/${encodeURIComponent(k.kb_id)}`}
                      className="lib-ghost lib-view-link"
                    >
                      查看资料
                    </Link>
                    {/* 留言入口：数字 > 0 才显示条数，0 条时只说「留言」，
                        免得一排「留言 0」把卡片下半区塞满。 */}
                    <button
                      type="button"
                      className="lib-ghost lib-board-open"
                      onClick={() => setBoardKb(k)}
                    >
                      留言{k.posts ? ` ${k.posts}` : ""}
                    </button>
                    <button
                      type="button"
                      className="lib-open"
                      disabled={opening !== null}
                      onClick={() => void openInChat(k)}
                    >
                      {opening === k.kb_id ? "打开中…" : "挂到我的对话"}
                      {opening !== k.kb_id && <CornIcon name="arrow" className="h-3.5 w-3.5" />}
                    </button>
                  </span>
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

      {boardKb && (
        <div className="lib-veil" onClick={() => setBoardKb(null)} role="presentation">
          <div
            className="lib-sheet lib-board-sheet"
            role="dialog"
            aria-modal="true"
            aria-label={`${boardKb.name} 的留言板`}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="lib-sheet-h">
              <h2 title={boardKb.name}>{boardKb.name}</h2>
              <button
                type="button"
                className="lib-sheet-x"
                onClick={() => setBoardKb(null)}
                aria-label="关闭留言板"
              >
                <CornIcon name="close" className="h-4 w-4" />
              </button>
            </div>
            {/* 同学分享的库没有详情页（文件清单不给看），留言就挂在这个弹层里。
                官方库的留言板在 /library/{kbId} 页脚，用的是同一个组件。 */}
            <KbBoard
              kbId={boardKb.kb_id}
              compact
              onCount={(n) =>
                setPub((list) =>
                  list.map((x) => (x.kb_id === boardKb.kb_id ? { ...x, posts: n } : x))
                )
              }
            />
          </div>
        </div>
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
  /** 别人能做什么。默认"仅观看"：新增能力要用户自己点开，不该替他放开下载 */
  const [access, setAccess] = useState<AccessMode>("preview");
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
      await api.setKbPublic(k.kb_id, true, desc.trim(), access);
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
              选一个库，写一句它是干什么的。公开后全站登录用户都能在对话里检索它、
              点进去看资料清单，<strong>改内容仍然只有你能做</strong>，随时可以收回。
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

            <div className="lib-sheet-label">别人能做什么</div>
            <div className="lib-access-pick" role="radiogroup" aria-label="访问方式">
              <button
                type="button"
                role="radio"
                aria-checked={access === "preview"}
                className={"lib-access-opt" + (access === "preview" ? " on" : "")}
                onClick={() => setAccess("preview")}
              >
                <b>仅观看</b>
                <span>能看清单、能在线预览，不提供下载按钮</span>
              </button>
              <button
                type="button"
                role="radio"
                aria-checked={access === "download"}
                className={"lib-access-opt" + (access === "download" ? " on" : "")}
                onClick={() => setAccess("download")}
              >
                <b>可以下载</b>
                <span>在上面基础上，还能把原件存到本地</span>
              </button>
            </div>

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
