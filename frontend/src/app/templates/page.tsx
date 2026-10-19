"use client";

// 模板广场：面向大学生的场景化智能体入口。
// 从 /api/templates 拉目录 → 卡片画廊 → 点「使用模板」后端建好知识库+会话 → 跳回工作台并选中。
// 视觉沿用苞米地笔记本令牌（bg-canvas / bg-card-bg / text-accent / border-line / shadow-*），不另起风格。
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import MascotLogo from "@/components/chat/MascotLogo";
import CornIcon from "@/components/CornIcon";
import { useAuth } from "@/lib/hooks/useAuth";
import AuthGate from "@/components/AuthGate";
import { api } from "@/lib/api";
import type { Template } from "@/lib/types";

// 分类 → 强调色（仅用于眉题与图标描边，不用于大面积填充）
const CAT_COLOR: Record<string, string> = {
  升学规划: "text-accent",
  学术科研: "text-info",
  学习备考: "text-success",
  求职发展: "text-warning",
  语言提升: "text-info",
  校园生活: "text-accent",
};

const catColor = (c: string) => CAT_COLOR[c] ?? "text-accent";

export default function TemplatesPage() {
  const router = useRouter();
  const [templates, setTemplates] = useState<Template[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [usingId, setUsingId] = useState<string | null>(null);
  const auth = useAuth();

  useEffect(() => {
    // 未登录不发请求：先等 useAuth 确认身份，登录后再拉目录，避免 401 白跑一轮。
    if (!auth.me) {
      setLoading(false);
      return;
    }
    let alive = true;
    (async () => {
      try {
        const { templates: list } = await api.templates();
        if (alive) setTemplates(list);
      } catch (e) {
        if (alive) {
          const msg = e instanceof Error ? e.message : String(e);
          setError(msg.includes("登录") ? "请先登录后再浏览模板广场" : msg);
        }
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [auth.me]);

  // 按分类分组，保持目录里出现的顺序
  const grouped = useMemo(() => {
    const order: string[] = [];
    const map = new Map<string, Template[]>();
    for (const t of templates) {
      if (!map.has(t.category)) {
        map.set(t.category, []);
        order.push(t.category);
      }
      map.get(t.category)!.push(t);
    }
    return order.map((cat) => ({ cat, items: map.get(cat)! }));
  }, [templates]);

  const onUse = async (t: Template) => {
    setUsingId(t.id);
    try {
      const r = await api.applyTemplate(t.id);
      // 跳回工作台：?sid 让首页自动选中新建会话，?q 预填首个引导问题，
      // ?mode 把模板推荐的对话模式带过去（白名单之外的值后端会回落 auto）
      const q = r.starter_prompt
        ? `&q=${encodeURIComponent(r.starter_prompt)}`
        : "";
      const m = r.recommended_mode
        ? `&mode=${encodeURIComponent(r.recommended_mode)}`
        : "";
      router.push(`/?sid=${encodeURIComponent(r.session_id)}${q}${m}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setUsingId(null);
    }
  };

  /**
   * 把模板变成一条「每日委托」。
   *
   * 模板与委托本来是同一件事的两个时间尺度：模板给一个**一次性**的起点
   * （建库 + 开场白），委托则是把这件事**持续**做下去（定时自动执行）。
   * 与其让 8 个模板停在"用一次就完"，不如让它们能直接变成长期任务。
   *
   * 预填内容走 sessionStorage 而不是 URL query：指令动辄几十字，塞进 query
   * 既难看又有超长风险；而且跳过去之后模板对象就取不到了。
   * 这里**只预填、不调 /api/delegate** —— 那个端点还在 feat/ai-module-20261013
   * 分支上，本分支 404。用户到委托页填完点保存才发请求，届时接口没上线的话，
   * 那个页面本来就会显示「接口待上线」。
   */
  const onDelegate = (t: Template) => {
    try {
      sessionStorage.setItem(
        "baomi:delegate-preset",
        JSON.stringify({
          from: t.name,
          // 目录里没有 delegate_instruction 时回退 tagline：
          // 宁可预填得粗一点，也不要给用户一张空白表单
          instruction: t.delegate_instruction || t.tagline || "",
          ts: Date.now(),
        })
      );
    } catch {
      /* 隐私模式可能禁掉 sessionStorage —— 那只是不预填，不该拦住跳转 */
    }
    router.push("/delegate?new=1");
  };

  // 登录门：与四屏一致，未登录整页挡住（后端 /api/templates 也按用户隔离，这里只是提前挡，
  // 免得登录前先渲染空列表再跳走）。auth.loading 期间不打断，等确认完再决定渲染什么。
  if (auth.loading) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-canvas">
        <p className="text-fs-sm text-faint">正在确认登录状态...</p>
      </div>
    );
  }
  if (!auth.me) {
    return <AuthGate allowSignup={auth.allowSignup} onLogin={auth.login} onRegister={auth.register} />;
  }

  return (
    <div className="h-dvh overflow-y-auto overscroll-contain bg-canvas">
      {/* 顶栏：品牌 + 返回工作台（窄屏收起副标题） */}
      <header className="sticky top-0 z-10 border-b border-line bg-canvas/85 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center gap-3 px-4 py-3.5 sm:px-5">
          <MascotLogo className="h-8 w-8 shrink-0 object-contain" />
          <div className="min-w-0 flex-1">
            <h1 className="truncate font-serif text-fs-xl font-bold leading-tight text-ink">
              模板广场
            </h1>
            <p className="brand-sub hidden truncate sm:block">
              挑一个场景，30 秒拥有一个为你定制的智能体
            </p>
          </div>
          <Link
            href="/"
            className="icon-btn shrink-0 border border-line"
            aria-label="返回工作台"
          >
            <CornIcon name="back" className="h-4 w-4" />
            工作台
          </Link>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 pb-10 pt-5 sm:px-5 sm:pb-16 sm:pt-6">
        {/* 理念横幅：把差异化（教搭建而非代做）摆在最前 */}
        <div className="mb-6 flex items-center gap-2.5 rounded-lg border border-accent bg-accent-soft px-4 py-3 text-fs-sm text-ink sm:mb-7 sm:text-fs-md">
          <CornIcon name="bulb" className="h-5 w-5 shrink-0" title="提示" />
          <p>
            这里每个模板都帮你快速上手，但<strong>答案要你自己来</strong>——
            我们做的是为学习而生的 AI 素养平台，不是替你写答案的工具。
            用一次就好的点「使用模板」；<strong>想让它每天自动做的，点「设为每日委托」</strong>。
          </p>
        </div>

        {loading && (
          <p className="py-16 text-center text-fs-md text-muted">正在加载模板…</p>
        )}
        {!loading && error && (
          <div className="py-16 text-center">
            <p className="text-fs-md text-red-ink">{error}</p>
            <Link href="/" className="btn-import mt-3 inline-block px-4 py-1.5">
              返回工作台
            </Link>
          </div>
        )}

        {!loading &&
          !error &&
          grouped.map(({ cat, items }) => (
            <section key={cat} className="mb-8 last:mb-0">
              <div className="group-head">
                <span className={`eyebrow ${catColor(cat)}`}>{cat}</span>
                <span className="rule" />
                <span className="shrink-0 text-fs-xs text-muted">
                  {items.length} 个
                </span>
              </div>
              {/* 列数随模板数量自适应：1 个不拉满、2 个双列、3+ 三列，避免宽屏右侧大片留白 */}
              <div
                className={
                  items.length === 1
                    ? "grid grid-cols-1 max-w-md"
                    : items.length === 2
                    ? "grid grid-cols-1 gap-4 sm:grid-cols-2 sm:gap-5"
                    : "grid grid-cols-1 gap-4 sm:grid-cols-2 sm:gap-5 lg:grid-cols-3"
                }
              >
                {items.map((t) => (
                  <article
                    key={t.id}
                    className="group flex flex-col rounded-lg border border-line bg-card-bg p-5 shadow-1 transition duration-200 hover:border-accent hover:shadow-2"
                  >
                    <div className="flex items-start gap-3">
                      <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-md bg-fill-2 text-ink">
                        <CornIcon name={t.icon} className="h-7 w-7" title={t.name} />
                      </div>
                      <h3 className="mt-1 min-w-0 font-serif text-fs-xl font-bold leading-tight text-ink">
                        {t.name}
                      </h3>
                    </div>

                    <p className="mt-3 text-fs-md font-medium text-ink">{t.tagline}</p>
                    <p className="mt-1.5 text-fs-md leading-relaxed text-faint">
                      {t.description}
                    </p>

                    <div className="mt-3 flex flex-wrap gap-1.5">
                      {t.tags.map((tag) => (
                        <span
                          key={tag}
                          className="rounded-full border border-line bg-fill-3 px-2.5 py-0.5 text-fs-xs text-muted"
                        >
                          {tag}
                        </span>
                      ))}
                    </div>

                    {t.philosophy && (
                      <p className="mt-3 border-t border-line pt-3 text-fs-xs leading-relaxed text-muted">
                        {t.philosophy}
                      </p>
                    )}

                    {/* flex-1 + justify-end：同一行卡片不论文字长短，按钮都贴底对齐。
                        「设为每日委托」是次要动作（描边），主操作仍是「使用模板」（实心）——
                        后者立刻能聊，前者是"以后每天帮我做这件事"。 */}
                    <div className="mt-4 flex flex-1 flex-col justify-end gap-2">
                      <button
                        onClick={() => onUse(t)}
                        disabled={usingId === t.id}
                        className="btn-new w-full disabled:opacity-50"
                      >
                        {usingId === t.id ? "正在创建…" : "使用模板"}
                      </button>
                      <button
                        type="button"
                        onClick={() => onDelegate(t)}
                        className="btn-import w-full px-4 py-2 text-fs-md"
                        title="把这件事交给知穗定时去做（会带着这个模板的说明跳到委托页）"
                      >
                        设为每日委托
                      </button>
                    </div>
                  </article>
                ))}
              </div>
            </section>
          ))}

        {!loading && !error && templates.length === 0 && (
          <p className="py-16 text-center text-fs-md text-muted">
            暂时还没有模板，敬请期待。
          </p>
        )}
      </main>
    </div>
  );
}
