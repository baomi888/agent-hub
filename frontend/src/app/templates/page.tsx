"use client";

// 模板广场：面向大学生的场景化智能体入口。
// 从 /api/templates 拉目录 → 卡片画廊 → 点「使用模板」后端建好知识库+会话 → 跳回工作台并选中。
// 视觉沿用苞米地笔记本令牌（bg-canvas / bg-card-bg / text-accent / border-line / shadow-*），不另起风格。
//
// 2026-10-22 增补：兴趣标签。
// 模板广场是"该推荐什么"的天然落点 —— 用户来这儿本来就是要挑东西，
// 所以在同一个页面里回答两个问题：
//   ① 不知道从哪挑 → 先把方向选出来（30 条定稿词表，见 core/tag_taxonomy.py）
//   ② 选过之后 → 顶部出现「为你推荐」，顺序由标签权重决定，不是随机
// 推荐是**可降级**的附加值：拿不到标签就整块不渲染，绝不让推荐失败拖垮模板列表。
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import MascotLogo from "@/components/chat/MascotLogo";
import CornIcon from "@/components/CornIcon";
import TagPicker from "@/components/profile/TagPicker";
import { useAuth } from "@/lib/hooks/useAuth";
import AuthGate from "@/components/AuthGate";
import { api } from "@/lib/api";
import type { ProfileSnapshot, Template, TemplateRecommend } from "@/lib/types";

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

/** 卡片列数随数量自适应：1 个不拉满、2 个双列、3+ 三列，避免宽屏右侧大片留白 */
const gridClass = (n: number) =>
  n === 1
    ? "grid grid-cols-1 max-w-md"
    : n === 2
    ? "grid grid-cols-1 gap-4 sm:grid-cols-2 sm:gap-5"
    : "grid grid-cols-1 gap-4 sm:grid-cols-2 sm:gap-5 lg:grid-cols-3";

export default function TemplatesPage() {
  const router = useRouter();
  const [templates, setTemplates] = useState<Template[]>([]);
  const [recommended, setRecommended] = useState<TemplateRecommend[]>([]);
  const [profile, setProfile] = useState<ProfileSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [usingId, setUsingId] = useState<string | null>(null);
  // 首次引导：无标签时把选择器直接铺在页面上；用户主动收起后本次会话不再打扰
  const [pickerOpen, setPickerOpen] = useState(false);
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
        // 画像与目录并行拉：画像失败不该影响模板列表（推荐可降级）
        const [r, me] = await Promise.all([
          api.templates(),
          api.myProfile().catch(() => null),
        ]);
        if (!alive) return;
        setTemplates(r.templates);
        setRecommended(r.recommended ?? []);
        setProfile(me);
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

  // 推荐 id → 模板对象；顺带保留"为什么推荐"（命中的标签）
  const recItems = useMemo(() => {
    const byId = new Map(templates.map((t) => [t.id, t]));
    return recommended
      .map((r) => ({ t: byId.get(r.id), matched: r.matched }))
      .filter((x): x is { t: Template; matched: string[] } => Boolean(x.t));
  }, [recommended, templates]);

  const onUse = useCallback(
    async (t: Template) => {
      setUsingId(t.id);
      try {
        const r = await api.applyTemplate(t.id);
        // 跳回工作台：?sid 让首页自动选中新建会话，?q 预填首个引导问题。
        // 2026-10-31 起不再拼 ?mode= —— 四档模式条已下架，首页读到这个参数
        // 也不会做任何事，带上只是让 URL 看起来还有功能。
        const q = r.starter_prompt ? `&q=${encodeURIComponent(r.starter_prompt)}` : "";
        router.push(`/?sid=${encodeURIComponent(r.session_id)}${q}`);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        setUsingId(null);
      }
    },
    [router]
  );

  /**
   * 把模板变成一条「每日委托」。
   *
   * 模板与委托本来是同一件事的两个时间尺度：模板给一个**一次性**的起点
   * （建库 + 开场白），委托则是把这件事**持续**做下去（定时自动执行）。
   * 与其让 8 个模板停在"用一次就完"，不如让它们能直接变成长期任务。
   *
   * 预填内容走 sessionStorage 而不是 URL query：指令动辄几十字，塞进 query
   * 既难看又有超长风险；而且跳过去之后模板对象就取不到了。
   * 这里**只预填、不调 /api/delegate** —— 用户到委托页填完点保存才发请求，
   * 届时接口没上线的话，那个页面本来就会显示「接口待上线」。
   */
  const onDelegate = useCallback(
    (t: Template) => {
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
    },
    [router]
  );

  // 选完标签：本地立即更新（不用重拉整个列表），并让「为你推荐」自己长出来
  const onTagsSaved = useCallback(
    async (snap: ProfileSnapshot) => {
      setProfile(snap);
      setPickerOpen(false);
      try {
        const r = await api.templates();
        setRecommended(r.recommended ?? []);
      } catch {
        /* 推荐刷新失败就保持原样：标签已经存进去了，下次进来自然就有 */
      }
    },
    []
  );

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

  // 有没有标签，决定顶部是"引导选方向"还是"直接看推荐"
  const hasTags = Boolean(profile?.has_tags);
  const showPicker = profile !== null && (!hasTags || pickerOpen);

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

        {/* ---------- 首次引导 / 修改方向 ---------- */}
        {showPicker && (
          <section className="mb-7 rounded-lg border border-line bg-card-bg p-4 shadow-1 sm:p-5">
            <div className="mb-1 flex items-center gap-2">
              <CornIcon name="target" className="h-5 w-5 shrink-0" title="兴趣标签" />
              <h2 className="font-serif text-fs-xl font-bold text-ink">
                {hasTags ? "修改我的方向" : "先告诉我们你的方向"}
              </h2>
            </div>
            <p className="mb-1 text-fs-sm leading-relaxed text-faint">
              {hasTags
                ? "改动会立刻影响「为你推荐」的排序。"
                : "选 1~3 个最贴近你的标签（最多 6 个）。选完以后，下面的模板会按你的方向排 —— 以后不用每次重新自我介绍。"}
            </p>
            <TagPicker
              initialPicks={profile?.picks ?? []}
              onSaved={(snap) => void onTagsSaved(snap)}
              onCancel={hasTags ? () => setPickerOpen(false) : undefined}
              dense
            />
          </section>
        )}

        {/* ---------- 为你推荐 ---------- */}
        {!showPicker && recItems.length > 0 && (
          <section className="mb-8">
            <div className="group-head">
              <span className="eyebrow text-accent-ink">为你推荐</span>
              <span className="rule" />
              <button
                type="button"
                onClick={() => setPickerOpen(true)}
                className="shrink-0 text-fs-xs text-muted underline decoration-dotted underline-offset-2 hover:text-ink"
              >
                调整方向
              </button>
            </div>
            <div className={gridClass(recItems.length)}>
              {recItems.map(({ t, matched }) => (
                <TemplateCard
                  key={`rec-${t.id}`}
                  t={t}
                  using={usingId === t.id}
                  onUse={onUse}
                  onDelegate={onDelegate}
                  reason={matched}
                />
              ))}
            </div>
          </section>
        )}

        {loading && <p className="py-16 text-center text-fs-md text-muted">正在加载模板…</p>}
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
          // 保持原来的按分类分组视图：推荐是顶部多出来的一块，不是替代品。
          // 分类是"这些东西分别是什么"，推荐是"你该先看哪个"——两个问题不冲突。
          grouped.map(({ cat, items }) => (
            <section key={cat} className="mb-8 last:mb-0">
              <div className="group-head">
                <span className={`eyebrow ${catColor(cat)}`}>{cat}</span>
                <span className="rule" />
                <span className="shrink-0 text-fs-xs text-muted">{items.length} 个</span>
              </div>
              <div className={gridClass(items.length)}>
                {items.map((t) => (
                  <TemplateCard
                    key={t.id}
                    t={t}
                    using={usingId === t.id}
                    onUse={onUse}
                    onDelegate={onDelegate}
                  />
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

/**
 * 单个模板卡。
 *
 * 抽成组件是因为它现在要在**两处**渲染（「为你推荐」和完整列表）——
 * 复制一遍 60 行 JSX 的代价是以后改样式要改两处，漏一处就是两个卡片长得不一样。
 *
 * `reason` 只在推荐位传：显示"因为你在准备英语四六级"，
 * 让用户能判断推荐靠不靠谱；点不相关的东西时才知道该去改方向。
 */
function TemplateCard({
  t,
  using,
  onUse,
  onDelegate,
  reason,
}: {
  t: Template;
  using: boolean;
  onUse: (t: Template) => void;
  onDelegate: (t: Template) => void;
  reason?: string[];
}) {
  return (
    <article className="group flex flex-col rounded-lg border border-line bg-card-bg p-5 shadow-1 transition duration-200 hover:border-accent hover:shadow-2">
      <div className="flex items-start gap-3">
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-md bg-fill-2 text-ink">
          <CornIcon name={t.icon} className="h-7 w-7" title={t.name} />
        </div>
        <h3 className="mt-1 min-w-0 font-serif text-fs-xl font-bold leading-tight text-ink">
          {t.name}
        </h3>
      </div>

      <p className="mt-3 text-fs-md font-medium text-ink">{t.tagline}</p>
      <p className="mt-1.5 text-fs-md leading-relaxed text-faint">{t.description}</p>

      {reason && reason.length > 0 && (
        <p className="mt-3 flex flex-wrap items-center gap-1.5 text-fs-xs text-accent-ink">
          <CornIcon name="star" className="h-3.5 w-3.5 shrink-0" title="推荐理由" />
          因为你在意{reason.slice(0, 3).join("、")}
        </p>
      )}

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
        <button onClick={() => onUse(t)} disabled={using} className="btn-new w-full disabled:opacity-50">
          {using ? "正在创建…" : "使用模板"}
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
  );
}
