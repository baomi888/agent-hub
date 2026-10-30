"use client";

// 知识库（/knowledge）—— 独立页面。
//
// 为什么单独一页：这个入口原先指向 `/?kb=1`，和「对话」是同一个路由，
// 只是把首页右栏那个抽屉拉开（用户原话："点击对话和点击知识库都是一样的跳转"）。
// 资料管理本身是件独立的事：建库、传文件、看文件名和段数、删错的文件、重新整理。
// 塞在对话页 280px 的侧栏里，这些信息一件都放不下。
//
// 版式参考 ima 的知识库：左侧库列表，右侧当前库的文件卡片网格。
// 数据直接走后端已有的 /api/kb 一组接口（与首页资料库面板同一套），
// 不新增后端端点 —— 这里的"新"只是**摆放方式**新，能力与首页面板一致。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import PageShell from "@/components/console/PageShell";
import CornIcon from "@/components/CornIcon";
import { SkeletonLines, ToastHost } from "@/components/console/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/hooks/useAuth";
import { useToast } from "@/lib/hooks/useToast";
import { fmtNum, fmtWhen } from "@/lib/time";
import { CHUNK_PROFILES, nearestProfileId, profileParams, type ChunkProfileId } from "@/lib/chunkProfiles";
import { loadKbChunks, saveKbChunks, type KbChunkMap } from "@/lib/kbChunkPrefs";
import type { Defaults, KbFile, KbInfo } from "@/lib/types";

/** 文件类型标：只区分"读得出来的三类 + 其它"，不追求完整 MIME */
function kindOf(name: string): "pdf" | "doc" | "text" | "other" {
  const ext = (name.split(".").pop() ?? "").toLowerCase();
  if (ext === "pdf") return "pdf";
  if (ext === "doc" || ext === "docx") return "doc";
  if (ext === "md" || ext === "markdown" || ext === "txt") return "text";
  return "other";
}

function extOf(name: string): string {
  const ext = (name.split(".").pop() ?? "").toLowerCase();
  return ext && ext !== name.toLowerCase() ? ext.slice(0, 4) : "FILE";
}

const errMsg = (e: unknown, fallback: string) => (e instanceof Error ? e.message : fallback);

export default function KnowledgePage() {
  const auth = useAuth();
  const authedId = auth.me?.user.id ?? null;
  const { toasts, showToast } = useToast();

  const [kbs, setKbs] = useState<KbInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [defaults, setDefaults] = useState<Defaults | null>(null);
  /** 正在进行的操作：upload / rebuild / import / create / delete */
  const [busy, setBusy] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  /** 删库的二次确认（库 id）；删文件用下面那个 */
  const [confirmDelKb, setConfirmDelKb] = useState<string | null>(null);
  const [confirmDelFile, setConfirmDelFile] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  /** 每个库上次用的切法（kb_id → 参数）。后端不记这件事，存在本地，
      和首页资料库面板共用同一份 baomi.kbChunk.v1 —— 两个入口改的是同一件事，
      不能各存一套。 */
  const [chunkMap, setChunkMap] = useState<KbChunkMap>({});
  const [urlOpen, setUrlOpen] = useState(false);
  const [url, setUrl] = useState("");
  const fileInput = useRef<HTMLInputElement | null>(null);
  const detailRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const t = window.setTimeout(() => setChunkMap(loadKbChunks()), 0);
    return () => window.clearTimeout(t);
  }, []);

  const active = useMemo(() => kbs.find((k) => k.kb_id === activeId) ?? null, [kbs, activeId]);

  /** 当前库的切法：这张表记过就用它，没记过才回落到全局默认值 */
  const fallbackParams = useMemo(
    () => ({ size: defaults?.chunk_size ?? 500, overlap: defaults?.chunk_overlap ?? 50 }),
    [defaults]
  );
  const activeParams = (active && chunkMap[active.kb_id]) || fallbackParams;
  const size = activeParams.size;
  const overlap = activeParams.overlap;
  const modeId = nearestProfileId(size, overlap);

  const refresh = useCallback(
    async (select?: string) => {
      try {
        const { kbs: list } = await api.listKbs();
        setKbs(list);
        // 选中项：优先用刚建的，其次保留原选中，最后退回第一个
        setActiveId((prev) => {
          if (select && list.some((k) => k.kb_id === select)) return select;
          if (prev && list.some((k) => k.kb_id === prev)) return prev;
          return list[0]?.kb_id ?? null;
        });
      } catch (e) {
        showToast("error", errMsg(e, "知识库列表读不到，请稍后重试"));
      } finally {
        setLoading(false);
      }
    },
    [showToast],
  );

  // 未登录不发请求：登录前必然是 401，且登录成功后不会自动重来
  useEffect(() => {
    if (!authedId) return;
    void refresh();
    // 切块默认值：后端是唯一权威，前端不硬编码 500/50
    api
      .defaults()
      .then(setDefaults)
      .catch(() => {
        /* 读不到就用兜底值，不阻断上传 */
      });
  }, [authedId, refresh]);

  /** 窄屏是上下堆叠：点了左边的库，得把右边的详情滚进来，否则像"点了没反应" */
  const focusDetail = () => {
    if (!window.matchMedia("(max-width: 900px)").matches) return;
    // 等 React 把新选中态画出来再滚
    window.setTimeout(() => {
      detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 0);
  };

  const pickKb = (id: string) => {
    setActiveId(id);
    setConfirmDelKb(null);
    setConfirmDelFile(null);
    focusDetail();
  };

  const create = async () => {
    const name = newName.trim();
    if (!name || busy) return;
    setBusy("create");
    try {
      const r = await api.createKb(name);
      setNewName("");
      setCreating(false);
      await refresh(r.kb_id);
      showToast("success", `建好了「${r.name}」，往里放点资料吧`);
    } catch (e) {
      showToast("error", errMsg(e, "建库失败，请稍后重试"));
    } finally {
      setBusy(null);
    }
  };

  const upload = async (files: FileList | File[] | null) => {
    if (!active || !files) return;
    const arr = Array.from(files);
    if (arr.length === 0) return;
    setBusy("upload");
    try {
      const r = await api.uploadFiles(active.kb_id, arr, size, overlap);
      await refresh(active.kb_id);
      showToast("success", `收进 ${fmtNum(r.chunks)} 段`);
    } catch (e) {
      showToast("error", errMsg(e, "上传失败，请稍后重试"));
    } finally {
      setBusy(null);
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  const removeFile = async (name: string) => {
    if (!active) return;
    setBusy("delete");
    try {
      const r = await api.deleteKbFile(active.kb_id, name);
      setConfirmDelFile(null);
      await refresh(active.kb_id);
      showToast("info", r.removed > 0 ? `已移除「${name}」` : `已移除「${name}」`);
    } catch (e) {
      showToast("error", errMsg(e, "删除失败，请稍后重试"));
    } finally {
      setBusy(null);
    }
  };

  const removeKb = async (id: string) => {
    setBusy("delete");
    try {
      await api.deleteKb(id);
      setConfirmDelKb(null);
      // 删掉的是当前选中的库时，refresh 会自动退回第一个
      await refresh();
      showToast("info", "知识库已删除");
    } catch (e) {
      showToast("error", errMsg(e, "删除失败，请稍后重试"));
    } finally {
      setBusy(null);
    }
  };

  const rebuild = async () => {
    if (!active) return;
    setBusy("rebuild");
    try {
      const r = await api.rebuildKb(active.kb_id, size, overlap);
      await refresh(active.kb_id);
      showToast("success", `重新整理好了，共 ${fmtNum(r.chunks)} 段`);
    } catch (e) {
      showToast("error", errMsg(e, "重新整理失败，请稍后重试"));
    } finally {
      setBusy(null);
    }
  };

  /** 换切法只记下来，不自动重切：重切要重算向量，几秒到几十秒，
   *  用户只是想先选好再动手时不该被卡住。文件卡片下面那行提示会说清这件事。 */
  const pickMode = (id: ChunkProfileId) => {
    if (!active) return;
    const p = profileParams(id);
    const next: KbChunkMap = { ...chunkMap, [active.kb_id]: p };
    setChunkMap(next);
    saveKbChunks(next);
  };

  const importUrl = async () => {
    if (!active || !url.trim()) return;
    setBusy("import");
    try {
      const r = await api.importUrl(active.kb_id, url.trim(), size, overlap);
      setUrl("");
      setUrlOpen(false);
      await refresh(active.kb_id);
      showToast("success", `已收进「${r.title}」，${fmtNum(r.chunks)} 段`);
    } catch (e) {
      showToast("error", errMsg(e, "这个网址抓不下来，换个页面或稍后再试"));
    } finally {
      setBusy(null);
    }
  };

  const files: KbFile[] = active?.files ?? [];
  const exts = defaults?.supported_extensions ?? [".pdf", ".md", ".txt"];

  return (
    <PageShell
      eyebrow="知识库"
      title="我的资料库"
      sub="课件、笔记、论文都可以放进来。对话时选中它，回答就会先从这里找依据。"
      actions={
        <>
          {/* 页头只放"对当前这个库的主操作"。原来这里还有一个「+ 新建」，
              和左栏标题栏那个「新建」是同一件事 —— 同屏两个同级按钮，用户
              第一反应是"这俩有什么区别"，所以库管理统一收进左栏。 */}
          <button
            type="button"
            className="btn btn-primary"
            disabled={!active || busy !== null}
            onClick={() => fileInput.current?.click()}
          >
            <CornIcon name="upload" />
            {busy === "upload" ? "收集中…" : "上传文件"}
          </button>
        </>
      }
    >
      <input
        ref={fileInput}
        type="file"
        multiple
        accept={exts.join(",")}
        style={{ display: "none" }}
        onChange={(e) => void upload(e.target.files)}
      />

      {loading ? (
        <div className="card card-pad">
          <SkeletonLines widths={["40%", "100%", "86%"]} />
        </div>
      ) : kbs.length === 0 ? (
        <div className="card kbp-empty">
          <CornIcon name="book" className="h-10 w-10" />
          <b>还没有资料库</b>
          <p>
            建一个库，把这一门课的课件、笔记、往年题放进去 ——
            之后问的时候，回答会先从这里找依据，再补自己的话。
          </p>
          {/* 空态下的新建**必须在这里就地长出输入框**：左侧栏这时根本没渲染
              （kbs.length === 0 走的是这个分支），把 creating 只挂在左栏上，
              用户点「建第一个资料库」会看到毫无反应。 */}
          {creating ? (
            <div className="kbp-newrow" style={{ width: "100%", maxWidth: 380 }}>
              <input
                autoFocus
                value={newName}
                placeholder="给它起个名字，比如「数据结构」"
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void create();
                  if (e.key === "Escape") {
                    setCreating(false);
                    setNewName("");
                  }
                }}
              />
              <button
                type="button"
                className="btn btn-primary btn-sm"
                disabled={!newName.trim() || busy === "create"}
                onClick={() => void create()}
              >
                建
              </button>
            </div>
          ) : (
            <button type="button" className="btn btn-primary btn-sm" onClick={() => setCreating(true)}>
              建第一个资料库
            </button>
          )}
        </div>
      ) : (
        <div className="kbp">
          {/* ---------- 左：库列表 ---------- */}
          <aside className="card kbp-side">
            <div className="kbp-side-head">
              <span className="t">全部资料库</span>
              <button type="button" className="kbp-new" onClick={() => setCreating((v) => !v)}>
                <CornIcon name="plus" />
                新建
              </button>
            </div>

            {creating && (
              <div className="kbp-newrow">
                <input
                  autoFocus
                  value={newName}
                  placeholder="给它起个名字，比如「数据结构」"
                  onChange={(e) => setNewName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void create();
                    if (e.key === "Escape") {
                      setCreating(false);
                      setNewName("");
                    }
                  }}
                />
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  disabled={!newName.trim() || busy === "create"}
                  onClick={() => void create()}
                >
                  建
                </button>
              </div>
            )}

            {kbs.map((k) => (
              <button
                key={k.kb_id}
                type="button"
                className="kbp-item"
                aria-current={k.kb_id === activeId ? "true" : undefined}
                onClick={() => pickKb(k.kb_id)}
              >
                <CornIcon name="book" className="ico" />
                <span className="nm">
                  <b title={k.name}>{k.name}</b>
                  <span>
                    {k.files.length} 份资料 · {fmtNum(k.chunks)} 段
                  </span>
                </span>
              </button>
            ))}
          </aside>

          {/* ---------- 右：当前库 ---------- */}
          <section className="card kbp-main" ref={detailRef}>
            {active ? (
              <>
                <div className="kbp-main-head">
                  <div style={{ minWidth: 0 }}>
                    <h3 className="kbp-title">{active.name}</h3>
                    <p className="kbp-sub">
                      {files.length} 份资料 · 切成 {fmtNum(active.chunks)} 段
                    </p>
                  </div>
                  <div className="kbp-acts">
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      disabled={busy !== null || files.length === 0}
                      title="按当前读法把整个库重新切一遍（要重新算向量，会慢一点）"
                      onClick={() => void rebuild()}
                    >
                      <CornIcon name="refresh" />
                      {busy === "rebuild" ? "整理中…" : "重新整理"}
                    </button>
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm kbp-del"
                      onClick={() => setConfirmDelKb(active.kb_id)}
                    >
                      <CornIcon name="trash" />
                      删除
                    </button>
                  </div>
                </div>

                {confirmDelKb === active.kb_id && (
                  <div className="kbp-confirm">
                    <span>删掉「{active.name}」连同里面的 {files.length} 份资料？这一步不可撤销。</span>
                    <button
                      type="button"
                      className="btn btn-danger btn-sm"
                      disabled={busy === "delete"}
                      onClick={() => void removeKb(active.kb_id)}
                    >
                      确认删除
                    </button>
                    <button type="button" className="btn btn-ghost btn-sm" onClick={() => setConfirmDelKb(null)}>
                      先留着
                    </button>
                  </div>
                )}

                {/* 上传区：可点可拖。限制写在下面一行，并给出出路 */}
                {/* 这份资料怎么切。原先这个选项只在首页右栏的小面板里有，
                    用户从「知识库」进来就找不到了 —— 而设置页恰好写着"在资料库里给每个库单独选"。
                    三档用的是同一套 lib/chunkProfiles，改的是同一个本地映射。 */}
                <div className="kbp-read">
                  <span className="kbp-read-l">怎么切</span>
                  <div className="kbp-read-seg" role="group" aria-label="这份资料怎么切">
                    {CHUNK_PROFILES.map((p) => (
                      <button
                        key={p.id}
                        type="button"
                        aria-pressed={modeId === p.id}
                        title={`${p.examples} — ${p.benefit}`}
                        onClick={() => pickMode(p.id)}
                      >
                        {p.label}
                      </button>
                    ))}
                  </div>
                  <span className="kbp-read-n">每段约 {size} 字</span>
                  {files.length > 0 && (
                    <span className="kbp-read-tip">换了记得点「重新整理」</span>
                  )}
                </div>

                <label
                  className={`kbp-drop${dragging ? " drag" : ""}`}
                  onDragOver={(e) => {
                    e.preventDefault();
                    setDragging(true);
                  }}
                  onDragLeave={() => setDragging(false)}
                  onDrop={(e) => {
                    e.preventDefault();
                    setDragging(false);
                    void upload(e.dataTransfer.files);
                  }}
                >
                  <CornIcon name="upload" />
                  <span>
                    {busy === "upload" ? "正在收进来…" : "上传文件，或把文件拖到这里"}
                    <small>
                      支持 {exts.join(" / ")}；Word、PPT 和图片里的字读不出来 ——
                      先把它们另存为 PDF 再放进来。
                    </small>
                  </span>
                  <input
                    type="file"
                    multiple
                    accept={exts.join(",")}
                    style={{ display: "none" }}
                    disabled={busy !== null}
                    onChange={(e) => void upload(e.target.files)}
                  />
                </label>

                {/* 网址入库：走的是同一个库的 import-url，不影响本地上传 */}
                <div style={{ marginTop: 10 }}>
                  {urlOpen ? (
                    <div className="kbp-newrow" style={{ padding: 0 }}>
                      <input
                        autoFocus
                        value={url}
                        placeholder="粘贴一个网页地址，比如某篇博客或文档页"
                        onChange={(e) => setUrl(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") void importUrl();
                          if (e.key === "Escape") setUrlOpen(false);
                        }}
                      />
                      <button
                        type="button"
                        className="btn btn-primary btn-sm"
                        disabled={!url.trim() || busy === "import"}
                        onClick={() => void importUrl()}
                      >
                        {busy === "import" ? "抓取中…" : "收进来"}
                      </button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      className="kbp-url"
                      onClick={() => setUrlOpen(true)}
                    >
                      <CornIcon name="link" />
                      或者从一个网址收进来
                    </button>
                  )}
                </div>

                {files.length === 0 ? (
                  <p className="kbp-hint">
                    这个库还是空的 —— 放一份课件或笔记进来，里面第一段话就能被问到。
                  </p>
                ) : (
                  <div className="kbp-files">
                    {files.map((f) => (
                      <div className="kbp-file" key={f.name}>
                        <span className="kbp-ext" data-kind={kindOf(f.name)}>
                          {extOf(f.name)}
                        </span>
                        <div className="fn" title={f.name}>
                          {f.name}
                        </div>
                        <div className="fm">
                          {f.chunks != null ? `${fmtNum(f.chunks)} 段` : "已入库"}
                          {f.added_at ? ` · ${fmtWhen(f.added_at)}` : ""}
                          {f.origin === "web" ? " · 来自网页" : ""}
                        </div>
                        {confirmDelFile === f.name ? (
                          <button
                            type="button"
                            className="btn btn-danger btn-sm"
                            style={{ marginTop: 8 }}
                            disabled={busy === "delete"}
                            onClick={() => void removeFile(f.name)}
                          >
                            确认删掉
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="x"
                            aria-label={`删除 ${f.name}`}
                            title={`删除 ${f.name}`}
                            onClick={() => setConfirmDelFile(f.name)}
                          >
                            <CornIcon name="close" />
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </>
            ) : (
              <div className="kbp-empty">
                <b>左边挑一个库</b>
                <p>选中之后，这里会列出它装了什么、每份资料被切成了几段。</p>
              </div>
            )}
          </section>
        </div>
      )}

      <ToastHost toasts={toasts} />
    </PageShell>
  );
}
