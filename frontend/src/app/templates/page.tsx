"use client";

// 模板广场：面向大学生的场景化智能体入口。
// 从 /api/templates 拉目录 → 卡片画廊 → 点「使用模板」后端建好知识库+会话 → 跳回工作台并选中。
// 视觉沿用苞米地笔记本令牌（bg-canvas / bg-card-bg / text-accent / border-line / shadow-*），不另起风格。
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import MascotLogo from "@/components/chat/MascotLogo";
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

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const { templates: list } = await api.templates();
        if (alive) setTemplates(list);
      } catch (e) {
        if (alive) {
          const msg = e instanceof Error ? e.message : String(e);
          // 未登录：后端 401 会被 api 层翻译成这串，提示先回工作台登录
          setError(msg.includes("登录") ? "请先登录后再浏览模板广场" : msg);
        }
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

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
      // 跳回工作台：?sid 让首页自动选中新建会话，?q 预填首个引导问题
      const q = r.starter_prompt
        ? `&q=${encodeURIComponent(r.starter_prompt)}`
        : "";
      router.push(`/?sid=${encodeURIComponent(r.session_id)}${q}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setUsingId(null);
    }
  };

  return (
    <div className="min-h-dvh bg-canvas">
      {/* 顶栏：品牌 + 返回工作台 */}
      <header className="sticky top-0 z-10 border-b border-line bg-canvas/85 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center gap-3 px-5 py-3.5">
          <MascotLogo className="h-8 w-8 shrink-0 object-contain" />
          <div className="min-w-0 flex-1">
            <h1 className="font-serif text-fs-xl font-bold leading-tight text-ink">
              模板广场
            </h1>
            <p className="brand-sub truncate">挑一个场景，30 秒拥有一个为你定制的智能体</p>
          </div>
          <Link
            href="/"
            className="icon-btn shrink-0 border border-line"
            aria-label="返回工作台"
          >
            ← 工作台
          </Link>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-5 pb-16 pt-6">
        {/* 理念横幅：把差异化（教搭建而非代做）摆在最前 */}
        <div className="mb-7 rounded-lg border border-accent bg-accent-soft px-4 py-3 text-fs-md text-ink">
          💡 这里每个模板都帮你快速上手，但<strong>答案要你自己来</strong>——
          我们做的是为学习而生的 AI 素养平台，不是替你写答案的工具。
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
            <section key={cat} className="mb-8">
              <div className="group-head">
                <span className={`eyebrow ${catColor(cat)}`}>{cat}</span>
                <span className="rule" />
              </div>
              <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
                {items.map((t) => (
                  <article
                    key={t.id}
                    className="group flex flex-col rounded-lg border border-line bg-card-bg p-5 shadow-1 transition duration-200 hover:border-accent hover:shadow-2"
                  >
                    <div className="flex items-start gap-3">
                      <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-md bg-fill-2 text-2xl">
                        {t.icon}
                      </div>
                      <div className="min-w-0">
                        <div className={`eyebrow ${catColor(t.category)}`}>
                          {t.category}
                        </div>
                        <h3 className="mt-0.5 font-serif text-fs-xl font-bold leading-tight text-ink">
                          {t.name}
                        </h3>
                      </div>
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

                    <button
                      onClick={() => onUse(t)}
                      disabled={usingId === t.id}
                      className="btn-new mt-4 w-full disabled:opacity-50"
                    >
                      {usingId === t.id ? "正在创建…" : "使用模板"}
                    </button>
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
