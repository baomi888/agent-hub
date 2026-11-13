"use client";

/**
 * 官方课程资料页（/library/{kb_id}）。
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
import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import MascotLogo from "@/components/chat/MascotLogo";
import CornIcon from "@/components/CornIcon";
import AuthGate from "@/components/AuthGate";
import { ToastHost } from "@/components/console/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/hooks/useAuth";
import { useToast } from "@/lib/hooks/useToast";
import { fmtNum } from "@/lib/time";
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
    const out: { folder: string; files: OfficialKbFile[] }[] = [];
    for (const f of data?.files ?? []) {
      const { folder } = splitName(f.name);
      const last = out[out.length - 1];
      if (last && last.folder === folder) last.files.push(f);
      else out.push({ folder, files: [f] });
    }
    return out;
  }, [data]);

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
            <h1>{data?.name || "课程资料"}</h1>
            <p>官方公开知识库 · 在线预览或下载</p>
          </div>
          <Link href="/library" className="lib-back" aria-label="返回公开知识库">
            <CornIcon name="back" className="h-4 w-4" />
            <span>公开知识库</span>
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
            <p className="lib-empty-d">可能是这门课的资料还没上线，或者链接不对。</p>
            <div className="lib-empty-acts">
              <button type="button" className="lib-ghost" onClick={() => void load()}>
                重试
              </button>
              <Link href="/library" className="lib-share">
                回公开知识库
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
                  <span className="lib-badge-off">官方资料</span>
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
                <button
                  type="button"
                  className="lib-share lib-course-cta"
                  disabled={opening || data.chunks === 0}
                  onClick={() => void openInChat()}
                >
                  {opening ? "打开中…" : "挂到我的对话"}
                  {!opening && <CornIcon name="arrow" className="h-3.5 w-3.5" />}
                </button>
              </div>
            </section>

            {groups.length === 0 && (
              <div className="lib-empty">
                <CornIcon name="book" className="lib-empty-icon" />
                <p className="lib-empty-t">这门课还没有资料</p>
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
                          {f.preview && (
                            <button type="button" className="lib-ghost" onClick={() => setViewing(f)}>
                              预览
                            </button>
                          )}
                          {f.download ? (
                            <a
                              className="lib-open lib-file-dl"
                              href={api.officialKbFileUrl(data.kb_id, f.name)}
                            >
                              下载
                            </a>
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
              这些资料由站点整理，供校内学习使用。转载或再发布请注明来源与许可（如 OpenStax 的
              CC BY-NC-SA 4.0）。
            </p>
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
  onClose,
}: {
  kbId: string;
  file: OfficialKbFile;
  onClose: () => void;
}) {
  const isInline = INLINE_EXT.has(file.ext);
  const [text, setText] = useState<string | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [state, setState] = useState<"idle" | "loading" | "ready" | "error">(
    isInline ? "ready" : "loading"
  );
  const [msg, setMsg] = useState("");

  // Esc 关闭：看资料时手一直在键盘上，找不到关闭按钮会很烦
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    if (isInline) return;
    let alive = true;
    // 不在 effect 里同步 setState("loading")：初始值已经是 loading，
    // 换一份资料时靠父层的 key 重挂组件拿回初始态（见下面 FileViewer 的 key）
    api
      .getOfficialKbText(kbId, file.name)
      .then((r) => {
        if (!alive) return;
        setText(r.content);
        setTruncated(r.truncated);
        setState("ready");
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setMsg(e instanceof Error ? e.message : "这份资料没法在线看");
        setState("error");
      });
    return () => {
      alive = false;
    };
  }, [kbId, file.name, isInline]);

  const src = api.officialKbFileUrl(kbId, file.name, true);
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
            <a className="lib-ghost" href={api.officialKbFileUrl(kbId, file.name)}>
              下载
            </a>
            <button type="button" className="lib-sheet-x" onClick={onClose} aria-label="关闭预览">
              <CornIcon name="close" className="h-4 w-4" />
            </button>
          </span>
        </div>

        <div className="lib-viewer-b">
          {state === "loading" && <p className="lib-viewer-tip">正在读取…</p>}

          {state === "error" && (
            <div className="lib-viewer-tip">
              <p>{msg}</p>
              <a className="lib-share" href={api.officialKbFileUrl(kbId, file.name)}>
                下载到本地看
              </a>
            </div>
          )}

          {state === "ready" && isInline && file.ext === "pdf" && (
            <iframe className="lib-viewer-frame" src={src} title={base} />
          )}

          {state === "ready" && isInline && file.ext !== "pdf" && (
            // eslint-disable-next-line @next/next/no-img-element -- 认证直链，不走 next/image
            <img className="lib-viewer-img" src={src} alt={base} />
          )}

          {state === "ready" && !isInline && (
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
