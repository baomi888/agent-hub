"use client";

/**
 * 公开资料页（/library/{kb_id}）。
 *
 * 2026-11-13 起**同学分享的库也能进这一页**（由库主决定仅观看还是可下载），
 * 所以页面上不再写死"官方"：徽标、副标题、页脚按 `data.official` 分叉。
 *
 * 广场上点「查看资料」进到这里：一门课一页，列出这门课的全部官方资料，
 * 每份都能**在线看**或者**下载到本地**。
 *
 * 与 /library 的分工：
 *   - 广场负责"有哪些课"（卡片墙 + 同学分享的库）；
 *   - 这一页负责"这门课有什么"，所以它不需要搜索和排序，只需要清单清楚。
 *
 * 三条实现取舍：
 *   1. 不用弹层放整页内容 —— 资料多的课有十几份，弹层里再套一层滚动很难用；
 *      单开一页还能直接分享链接、手机返回键也符合预期。
 *   2. PDF 不抽文字：浏览器自带阅读器能翻页、缩放、选中文字，比后端抽出来的
 *      纯文本好得多，所以 PDF 走 `<iframe src=...&inline=1>`。
 *   3. Word/表格/文本才走后端抽正文（浏览器打不开 docx），抽不出文字时
 *      （扫描件）明确提示"只能下载"，而不是给一个空白预览。
 */
import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import MascotLogo from "@/components/chat/MascotLogo";
import CornIcon from "@/components/CornIcon";
import KbBoard from "@/components/library/KbBoard";
import AuthGate from "@/components/AuthGate";
import { ToastHost } from "@/components/console/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/hooks/useAuth";
import { useToast } from "@/lib/hooks/useToast";
import { fmtNum, fmtWhen } from "@/lib/time";
import type { OfficialKbDetail, OfficialKbFile } from "@/lib/types";

/** 卡片左侧方块里的字：取名字第一个字符 */
const markOf = (name: string) => (name || "").trim().slice(0, 1) || "课";

/** 文件名与所在文件夹拆开：清单按文件夹分组，行内只显示文件名 */
function splitName(name: string): { folder: string; base: string } {
  const parts = (name || "").split("/");
  const base = parts.pop() || name;
  return { folder: parts.join("/"), base };
}

/** 人类可读体积。0 表示原件没留存，显示成「—」而不是「0 B」 */
function fmtSize(bytes: number): string {
  if (!bytes || bytes < 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0)} MB`;
}

/** 能直接在浏览器里内嵌的类型（与后端 _INLINE_EXT 对齐） */
const INLINE_EXT = new Set(["pdf", "png", "jpg", "jpeg", "webp", "gif"]);

/**
 * 窄屏（≤1024，手机 + 平板）判定。
 *
 * 为什么必须按设备分两套：`<iframe src="x.pdf">` 在 iOS Safari / 微信内置浏览器里
 * 只会渲染第一页，手指上下滑不动、也没有全屏入口 —— 这是 WebKit 对 iframe 内 PDF
 * 的限制，改 CSS 改不出来（把 iframe 撑到 100dvh 也一样，滑的仍是外层而不是 PDF）。
 *
 * 所以窄屏改成把 PDF 直链交给系统阅读器（自带滚动、双指缩放、全屏、页码）；
 * 桌面保留 iframe，因为内嵌确实更顺手，没必要为了统一而退步。
 *
 * 用 matchMedia 而不是 CSS：这里要改的是「渲染什么元素」，不是「长什么样」。
 * 服务端没有 window，getServerSnapshot 先按桌面渲染，客户端 hydrate 后立刻纠正，
 * 不会出现两份 HTML 对不上的告警。
 */
const NARROW_QUERY = "(max-width: 1024px)";

function subscribeNarrow(onChange: () => void) {
  const mq = window.matchMedia(NARROW_QUERY);
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}

function useIsNarrow(): boolean {
  return useSyncExternalStore(
    subscribeNarrow,
    () => window.matchMedia(NARROW_QUERY).matches,
    () => false,
  );
}

/** 这份资料有没有「原版文件」可交给浏览器打开（PDF 原件，或 Word 导出的同名 PDF） */
const hasNative = (f: OfficialKbFile) => INLINE_EXT.has(f.ext) || !!f.preview_file;

export default function CourseLibraryPage() {
  const auth = useAuth();
  const router = useRouter();
  const params = useParams<{ kbId: string }>();
  const kbId = typeof params?.kbId === "string" ? params.kbId : "";
  const { toasts, showToast } = useToast();

  const [data, setData] = useState<OfficialKbDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [opening, setOpening] = useState(false);
  /** 正在预览的那份资料；null = 没开预览 */
  const [viewing, setViewing] = useState<OfficialKbFile | null>(null);
  /** 资料名搜索。一个库里几十份资料时，靠滚着找太慢 */
  const [fileQuery, setFileQuery] = useState("");
  /** 收藏 / 复制这两件事都要等接口，各自一个忙态防连点 */
  const [favBusy, setFavBusy] = useState(false);
  const [forking, setForking] = useState(false);
  /** 手机/平板：原版 PDF 不走弹层，直接交给系统阅读器 */
  const narrow = useIsNarrow();

  const load = useCallback(async () => {
    if (!kbId) return;
    setLoading(true);
    setErr(null);
    try {
      setData(await api.getOfficialKb(kbId));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "加载失败，请稍后重试");
    } finally {
      setLoading(false);
    }
  }, [kbId]);

  useEffect(() => {
    if (!auth.me) return;
    // 与 /library 同样放宏任务里：直接在 effect 体里 setLoading 会被
    // react-hooks/set-state-in-effect 判成级联渲染
    const t = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(t);
  }, [auth.me, load]);

  /** 按文件夹分组，保持后端返回的原始顺序（导入时排好的） */
  const groups = useMemo(() => {
    const q = fileQuery.trim().toLowerCase();
    // 搜索匹配「资料名」整体（含文件夹前缀）：用户记得的多半是"第三章"，而不是完整路径
    const files = (data?.files ?? []).filter((f) => !q || f.name.toLowerCase().includes(q));
    const out: { folder: string; files: OfficialKbFile[] }[] = [];
    for (const f of files) {
      const { folder } = splitName(f.name);
      const last = out[out.length - 1];
      if (last && last.folder === folder) last.files.push(f);
      else out.push({ folder, files: [f] });
    }
    return out;
  }, [data, fileQuery]);

  const openInChat = async () => {
    if (!data || opening) return;
    setOpening(true);
    try {
      const conv = await api.createSession(`关于「${data.name}」的提问`, data.kb_id);
      router.push(`/?sid=${encodeURIComponent(conv.id)}`);
    } catch (e) {
      showToast("error", e instanceof Error ? e.message : "打开对话失败，请稍后重试");
      setOpening(false);
    }
  };

  /** 收藏 / 取消收藏。先改本地状态：星标点下去要立刻亮，等一个来回会让人再点一次 */
  const toggleFav = async () => {
    if (!data || favBusy) return;
    const on = !data.favorited;
    setFavBusy(true);
    setData((d) =>
      d ? { ...d, favorited: on, favs: Math.max(0, (d.favs ?? 0) + (on ? 1 : -1)) } : d,
    );
    try {
      const r = on ? await api.addFavorite(data.kb_id) : await api.removeFavorite(data.kb_id);
      setData((d) => (d ? { ...d, favorited: r.favorited, favs: r.favs } : d));
      showToast(
        "success",
        on ? "已收藏，在资料库面板的「我的收藏库」里能找到它" : "已取消收藏",
      );
    } catch (e) {
      setData((d) =>
        d ? { ...d, favorited: !on, favs: data.favs } : d,
      );
      showToast("error", e instanceof Error ? e.message : "操作失败，请稍后重试");
    } finally {
      setFavBusy(false);
    }
  };

  /**
   * 复制成我的库。
   *
   * 复制要重新 embedding，几十份资料要跑一会儿，所以按钮置为"复制中…"并禁点 ——
   * 连点会产生两次复制请求，第二次虽然同名复用不会多出库，但会白跑一遍向量化。
   * 完成后不跳转：用户当前正在看清单，把他弹走会丢掉正在看的位置；
   * 给个成功提示 + 一个"去我的库"的入口就够了。
   */
  const forkIt = async () => {
    if (!data || forking) return;
    setForking(true);
    try {
      const r = await api.forkPublicKb(data.kb_id);
      showToast(
        "success",
        r.files < r.from_files
          ? `已复制 ${r.files} 份资料到「${r.name}」（源库 ${r.from_files} 份里有部分原件缺失）`
          : `已复制 ${r.files} 份资料到「${r.name}」，现在可以往里加自己的资料了`,
      );
    } catch (e) {
      showToast("error", e instanceof Error ? e.message : "复制失败，请稍后重试");
    } finally {
      setForking(false);
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
            <h1>{data?.name || "资料库"}</h1>
            <p>
              {data?.official
                ? "官方公开资料库 · 在线预览或下载"
                : "同学分享的资料库 · " +
                  (data?.access === "download" ? "可在线预览、可下载" : "仅可在线预览")}
            </p>
          </div>
          <Link href="/library" className="lib-back" aria-label="返回公开资料库">
            <CornIcon name="back" className="h-4 w-4" />
            <span>公开资料库</span>
          </Link>
        </div>
      </header>

      <main className="lib-main">
        {loading && (
          <>
            <div className="lib-course-hero lib-skel" />
            <div className="lib-files" aria-busy="true">
              {[0, 1, 2, 3, 4].map((i) => (
                <div key={i} className="lib-file lib-skel" />
              ))}
            </div>
          </>
        )}

        {!loading && err && (
          <div className="lib-empty">
            <CornIcon name="book" className="lib-empty-icon" />
            <p className="lib-empty-t">{err}</p>
            <p className="lib-empty-d">可能是这个库还没公开、被收回了，或者链接不对。</p>
            <div className="lib-empty-acts">
              <button type="button" className="lib-ghost" onClick={() => void load()}>
                重试
              </button>
              <Link href="/library" className="lib-share">
                回公开资料库
              </Link>
            </div>
          </div>
        )}

        {!loading && !err && data && (
          <>
            <section className="lib-course-hero">
              <div className="lib-course-hero-h">
                <span className="lib-course-mark lib-course-mark-lg" aria-hidden="true">
                  {markOf(data.name)}
                </span>
                <div className="lib-course-hero-text">
                  <span className="lib-badge-off">
                    {data.official ? "官方资料" : `由 ${data.owner_name || "同学"} 分享`}
                  </span>
                  <h2>{data.name}</h2>
                  <p>{data.desc?.trim() || "这门课的资料还在整理，先把已有的用起来。"}</p>
                </div>
              </div>
              <div className="lib-course-hero-f">
                <span className="lib-course-stat">
                  <b>{fmtNum(data.files.length)}</b> 份资料
                </span>
                <span className="lib-dot" />
                <span className="lib-course-stat">
                  <b>{fmtNum(data.chunks)}</b> 段已建库
                </span>
                <span className="lib-dot" />
                {/* 浏览数（独立访客）：本次打开已计入，所以新库第一次看就是 1。
                    收藏数不在这儿写一遍 —— 右边星标按钮上的数字就是它，
                    同一行出现「2 人收藏 ★ 2」读起来像两个不同的数。 */}
                <span className="lib-course-stat" title="看过这个库的人数">
                  <b>{fmtNum(data.views ?? 0)}</b> 人看过
                </span>
                {/* 上传/分享日期：用户要求"每个库都写上上传日期"。
                    官方库是导入上线那天，同学库是第一次公开那天。 */}
                {!!data.uploads_at && (
                  <>
                    <span className="lib-dot" />
                    <span className="lib-course-stat">{fmtWhen(data.uploads_at)}上传</span>
                  </>
                )}
                {/* 右下两个动作成组靠右。
                    自己的库：收藏和复制都没有意义（已经在自己名下），不给星标。 */}
                <span className="lib-hero-acts">
                  {!data.mine && (
                    <button
                      type="button"
                      className={"lib-fav-btn lib-hero-fav" + (data.favorited ? " on" : "")}
                      aria-pressed={!!data.favorited}
                      aria-label={data.favorited ? "取消收藏" : "收藏这个库"}
                      title={data.favorited ? "取消收藏" : "收藏，之后在「我的收藏库」里找得到"}
                      disabled={favBusy}
                      onClick={() => void toggleFav()}
                    >
                      <CornIcon name="star" className="h-4 w-4" />
                      {(data.favs ?? 0) > 0 && (
                        <span className="lib-fav-n">{fmtNum(data.favs ?? 0)}</span>
                      )}
                    </button>
                  )}
                  <button
                    type="button"
                    className="lib-share lib-course-cta"
                    disabled={opening || data.chunks === 0}
                    onClick={() => void openInChat()}
                  >
                    {opening ? "打开中…" : "挂到我的对话"}
                    {!opening && <CornIcon name="arrow" className="h-3.5 w-3.5" />}
                  </button>
                </span>
              </div>
              {/* 复制成我的库：用户原话"除了用官方知识库，还可以往官方知识库加自己的资料，
                  形成自己的知识库"。实现是复制一份到自己名下 —— 往官方库写会改到所有人。
                  所以文案必须说清"复制"而不是"加入"，否则用户会以为是共享编辑。 */}
              {!data.mine && (
                <div className="lib-fork">
                  <div className="lib-fork-text">
                    <b>想在这个库的基础上加自己的资料？</b>
                    <span>
                      复制一份到你名下（{fmtNum(data.files.length)} 份资料一起带过去），
                      之后你往里加的东西只有你看得见，原库不受影响。
                    </span>
                  </div>
                  <button
                    type="button"
                    className="lib-ghost lib-fork-btn"
                    disabled={forking || data.files.length === 0}
                    onClick={() => void forkIt()}
                  >
                    {forking ? "复制中…" : "复制成我的库"}
                  </button>
                </div>
              )}
            </section>

            {/* 资料名搜索：只在这个库资料够多时才出现（少于 7 份滚一下就到了，
                多一个搜索框反而让首屏更挤） */}
            {(data.files.length > 6 || fileQuery.trim() !== "") && (
              <label className="lib-search lib-file-search">
                <CornIcon name="search" className="h-4 w-4" />
                <input
                  value={fileQuery}
                  onChange={(e) => setFileQuery(e.target.value)}
                  placeholder="搜资料名，如「第三章」或「真题」"
                  aria-label="搜索这个库里的资料"
                />
                {fileQuery && (
                  <button
                    type="button"
                    onClick={() => setFileQuery("")}
                    aria-label="清空资料搜索"
                  >
                    <CornIcon name="close" className="h-3.5 w-3.5" />
                  </button>
                )}
              </label>
            )}

            {groups.length === 0 && (
              <div className="lib-empty">
                <CornIcon name="book" className="lib-empty-icon" />
                <p className="lib-empty-t">
                  {fileQuery.trim() ? `没搜到「${fileQuery.trim()}」` : "这个库还没有资料"}
                </p>
              </div>
            )}

            {groups.map((g) => (
              <section className="lib-folder" key={g.folder || "_root"}>
                {g.folder && (
                  <div className="lib-sec-head">
                    <span className="eyebrow">{g.folder}</span>
                    <span className="rule" />
                    <span className="lib-count">{g.files.length} 份</span>
                  </div>
                )}
                <ul className="lib-files">
                  {g.files.map((f) => {
                    const { base } = splitName(f.name);
                    return (
                      <li className="lib-file" key={f.name}>
                        <span className="lib-file-ext">{f.ext ? f.ext.toUpperCase() : "文件"}</span>
                        <span className="lib-file-main">
                          <b title={f.name}>{base}</b>
                          <span className="lib-file-meta">
                            {fmtSize(f.size)}
                            {f.chunks > 0 ? ` · ${fmtNum(f.chunks)} 段` : " · 未做文字检索"}
                          </span>
                        </span>
                        <span className="lib-file-acts">
                          {f.preview &&
                            (narrow && hasNative(f) ? (
                              // 窄屏：直链 + target=_blank，交给系统 PDF 阅读器。
                              // 用 <a> 而不是 window.open：iOS/微信会拦异步 window.open，
                              // 而原生链接点击不受弹窗拦截策略影响。
                              <a
                                className="lib-ghost"
                                href={api.officialKbFileUrl(data.kb_id, f.preview_file || f.name, true)}
                                target="_blank"
                                rel="noopener noreferrer"
                              >
                                预览
                              </a>
                            ) : (
                              <button type="button" className="lib-ghost" onClick={() => setViewing(f)}>
                                预览
                              </button>
                            ))}
                          {f.download ? (
                            <a
                              className="lib-open lib-file-dl"
                              href={api.officialKbFileUrl(data.kb_id, f.name)}
                            >
                              下载
                            </a>
                          ) : data.access === "preview" ? (
                            // 库主设了仅观看：这里必须说清是**规则**不让下，
                            // 而不是原件丢了 —— 两者的处理方法完全不一样
                            <span className="lib-file-na" title="库主设置为仅观看">
                              仅观看
                            </span>
                          ) : (
                            <span className="lib-file-na">原件未留存</span>
                          )}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              </section>
            ))}

            <p className="lib-foot">
              {data.official
                ? "这些资料由站点整理，供校内学习使用。转载或再发布请注明来源与许可（如 OpenStax 的 CC BY-NC-SA 4.0）。"
                : "这些资料由同学自愿分享，仅供校内学习使用。要转载请先征得分享者同意。"}
            </p>

            {/* 留言板放在资料清单之后：这一页的主角是资料，讨论是附在后面的
                —— 放在最上面会让人以为进的是论坛而不是课程资料页。 */}
            <KbBoard kbId={data.kb_id} />
          </>
        )}
      </main>

      {viewing && data && (
        <FileViewer
          // 按文件名重挂：换一份资料时预览状态（正文/错误/加载中）必须归零，
          // 否则会先闪一下上一份的正文
          key={viewing.name}
          kbId={data.kb_id}
          file={viewing}
          narrow={narrow}
          onClose={() => setViewing(null)}
        />
      )}

      <ToastHost toasts={toasts} />
    </div>
  );
}

/** 预览弹层：PDF/图片直接内嵌，其余走后端抽正文。
 *
 *  独立组件是为了让"打开-关闭"只重挂弹层，不牵动整页的列表与滚动位置。
 */
function FileViewer({
  kbId,
  file,
  narrow,
  onClose,
}: {
  kbId: string;
  file: OfficialKbFile;
  narrow: boolean;
  onClose: () => void;
}) {
  const isInline = INLINE_EXT.has(file.ext);
  /**
   * 预览模式：
   *   pdf  —— 浏览器阅读器，看的是"原版"（Word 用导入时导出的 PDF，PDF 原件直接读）
   *   text —— 后端抽出来的正文，能选中复制，但公式只剩〔公式〕占位
   * 有原版 PDF 时默认走 pdf，因为那才是学生要的"和原来一样"。
   */
  const previewFile = file.preview_file || "";
  const [mode, setMode] = useState<"pdf" | "text">(previewFile || isInline ? "pdf" : "text");
  const [text, setText] = useState<string | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [msg, setMsg] = useState("");
  const showPdf = mode === "pdf" && (isInline || !!previewFile);

  // Esc 关闭：看资料时手一直在键盘上，找不到关闭按钮会很烦
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    if (showPdf) return;
    let alive = true;
    api
      .getOfficialKbText(kbId, file.name)
      .then((r) => {
        if (!alive) return;
        setText(r.content);
        setTruncated(r.truncated);
        setMsg("");
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setText(null);
        setMsg(e instanceof Error ? e.message : "这份资料没法在线看");
      });
    return () => {
      alive = false;
    };
  }, [kbId, file.name, showPdf]);

  const src = api.officialKbFileUrl(kbId, previewFile || file.name, true);
  const { base } = splitName(file.name);

  return (
    <div className="lib-veil" onClick={onClose} role="presentation">
      <div
        className="lib-viewer"
        role="dialog"
        aria-modal="true"
        aria-label={`预览 ${base}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="lib-viewer-h">
          <b title={file.name}>{base}</b>
          <span className="lib-viewer-acts">
            {previewFile && (
              <button
                type="button"
                className="lib-ghost"
                onClick={() => setMode((m) => (m === "pdf" ? "text" : "pdf"))}
                title={mode === "pdf" ? "看抽出来的纯文字（可复制）" : "回到原版排版"}
              >
                {mode === "pdf" ? "文字版" : "原版"}
              </button>
            )}
            <a className="lib-ghost" href={api.officialKbFileUrl(kbId, file.name)}>
              下载
            </a>
            <button type="button" className="lib-sheet-x" onClick={onClose} aria-label="关闭预览">
              <CornIcon name="close" className="h-4 w-4" />
            </button>
          </span>
        </div>

        <div className="lib-viewer-b">
          {/* 窄屏不给 iframe：iOS/微信里它只画第一页、滑不动、也没有全屏入口。
              这里只留一个明确的出口，把 PDF 交给系统阅读器（滚动/缩放/全屏都原生支持）。 */}
          {showPdf && narrow && (file.ext === "pdf" || previewFile) && (
            <div className="lib-viewer-tip">
              <p>手机上看原版 PDF 需要交给系统阅读器，才能上下滚动、双指缩放和全屏。</p>
              <a className="lib-share" href={src} target="_blank" rel="noopener noreferrer">
                全屏打开
              </a>
            </div>
          )}

          {showPdf && !narrow && (file.ext === "pdf" || previewFile) && (
            <iframe className="lib-viewer-frame" src={src} title={base} />
          )}

          {showPdf && file.ext !== "pdf" && !previewFile && (
            // eslint-disable-next-line @next/next/no-img-element -- 认证直链，不走 next/image
            <img className="lib-viewer-img" src={src} alt={base} />
          )}

          {!showPdf && !msg && text === null && <p className="lib-viewer-tip">正在读取…</p>}

          {!showPdf && msg && (
            <div className="lib-viewer-tip">
              <p>{msg}</p>
              <a className="lib-share" href={api.officialKbFileUrl(kbId, file.name)}>
                下载到本地看
              </a>
            </div>
          )}

          {!showPdf && text !== null && (
            <>
              <pre className="lib-viewer-text">{text}</pre>
              {truncated && (
                <p className="lib-viewer-tip">
                  内容较长，只显示了前面一部分。完整内容请下载原件。
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
