"use client";

// 兴趣标签选择器（首次引导 + 设置页共用）。
//
// 设计上刻意**不硬编码任何标签名**：七大类、每条标签、说明、哪些能选，
// 全部由 GET /api/profile/tags 下发（权威在 core/tag_taxonomy.py）。
// 加一条标签只改后端一个文件，前端不用动 —— 否则词表一改就是两处不同步。
//
// 两个容易做错的点：
//   1. 已选但**不在可选项**里的标签（历史数据、或者"时间管理"这类刻意不进首屏的）
//      不能因为没渲染就丢掉。所以渲染时按 `可选项 ∪ 已选` 取并集。
//   2. 保存是**覆盖式**：这次没选的下次就没了，所以本地 state 必须完整镜像
//      服务端已选集合，不能"只发增量"。
import { useCallback, useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import { api } from "@/lib/api";
import CornIcon from "@/components/CornIcon";
import type { ProfileSnapshot, TagTaxonomy } from "@/lib/types";

/** 与后端 core/session.MAX_PICKS 一致。后端会再校验一次，这里只做即时反馈。 */
export const MAX_PICKS = 6;

const chipStyle = (on: boolean): CSSProperties => ({
  appearance: "none",
  cursor: "pointer",
  borderRadius: 999,
  border: `1px solid ${on ? "var(--accent)" : "var(--line)"}`,
  background: on ? "var(--accent-soft)" : "var(--card-bg)",
  color: on ? "var(--accent-ink)" : "var(--text-2)",
  fontWeight: on ? 600 : 400,
  padding: "5px 12px",
  fontSize: "var(--fs-sm)",
  lineHeight: 1.5,
  transition: "background-color .14s ease, border-color .14s ease, color .14s ease",
});

export default function TagPicker({
  initialPicks,
  onSaved,
  onCancel,
  dense = false,
  submitLabel = "保存我的方向",
}: {
  /** 已选手选标签；不传则视为空（首次引导） */
  initialPicks?: string[];
  onSaved: (snap: ProfileSnapshot) => void;
  onCancel?: () => void;
  dense?: boolean;
  submitLabel?: string;
}) {
  const [tax, setTax] = useState<TagTaxonomy | null>(null);
  const [picked, setPicked] = useState<string[]>(initialPicks ?? []);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .tagTaxonomy()
      .then((t) => {
        if (alive) setTax(t);
      })
      .catch((e: unknown) => {
        if (alive) setErr(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  const pickable = useMemo(() => new Set(tax?.pickable ?? []), [tax]);

  const toggle = useCallback(
    (name: string) => {
      setErr(null);
      setPicked((prev) => {
        if (prev.includes(name)) return prev.filter((n) => n !== name);
        if (prev.length >= MAX_PICKS) {
          // 不做静默忽略：用户点了没反应是最难排查的一种"空状态"
          setErr(`最多选 ${MAX_PICKS} 个，先取消一个再选`);
          return prev;
        }
        return [...prev, name];
      });
    },
    []
  );

  const save = useCallback(async () => {
    if (!picked.length) {
      setErr("至少选 1 个，我们才知道该推荐什么");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const snap = await api.saveTags(picked);
      onSaved(snap);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [picked, onSaved]);

  if (loading) {
    return (
      <p className="hint" style={{ margin: 0 }}>
        正在读取标签表…
      </p>
    );
  }
  if (!tax) {
    return (
      <p className="hint" style={{ margin: 0, color: "var(--red-ink)" }}>
        标签表加载失败{err ? `：${err}` : ""}
      </p>
    );
  }

  return (
    <div>
      {tax.groups.map((g) => {
        // 可选项 ∪ 已选：历史选过的非首屏标签（如"时间管理"）不能被藏起来，
        // 藏起来就等于下次保存时被悄悄删掉。
        const items = g.tags.filter((t) => pickable.has(t.name) || picked.includes(t.name));
        if (!items.length) return null;
        return (
          <div key={g.category} style={{ marginTop: dense ? 12 : 16 }}>
            <div
              className="row"
              style={{ gap: 8, alignItems: "baseline", marginBottom: 8 }}
            >
              <span className="sec-label" style={{ margin: 0 }}>
                {g.category}
              </span>
              <span className="meta" style={{ color: "var(--text-3)" }}>
                {/* 写"可选"而不是"N 个"：校园生活只剩 1 条可选项（时间管理/心态调节
                    刻意不进首屏），只写"1 个"会让人以为标签表只剩这么点。 */}
                可选 {items.length} 个
              </span>
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {items.map((t) => {
                const on = picked.includes(t.name);
                return (
                  <button
                    key={t.name}
                    type="button"
                    aria-pressed={on}
                    onClick={() => toggle(t.name)}
                    style={chipStyle(on)}
                    title={
                      t.desc + (t.parent ? `（${t.parent} 的细分方向）` : "") + ` · ${t.type}`
                    }
                  >
                    {on ? "✓ " : ""}
                    {t.name}
                  </button>
                );
              })}
            </div>
          </div>
        );
      })}

      <div
        className="row"
        style={{
          marginTop: 16,
          gap: 10,
          alignItems: "center",
          flexWrap: "wrap",
          borderTop: "1px solid var(--line)",
          paddingTop: 12,
        }}
      >
        <button
          type="button"
          className="btn btn-primary btn-sm"
          onClick={() => void save()}
          disabled={busy || picked.length === 0}
        >
          <CornIcon name="check" />
          {busy ? "保存中…" : submitLabel}
        </button>
        {onCancel ? (
          <button type="button" className="btn btn-ghost btn-sm" onClick={onCancel} disabled={busy}>
            取消
          </button>
        ) : null}
        <span className="meta" style={{ marginLeft: "auto", color: "var(--text-3)" }}>
          已选 {picked.length}/{MAX_PICKS}
        </span>
      </div>

      {err ? (
        <p className="hint" style={{ color: "var(--red-ink)", marginTop: 8, marginBottom: 0 }}>
          {err}
        </p>
      ) : (
        <p className="hint" style={{ marginTop: 8, marginBottom: 0 }}>
          这些标签决定模板推荐与每日委托的默认方向；随时可以在设置里改。
        </p>
      )}
    </div>
  );
}
