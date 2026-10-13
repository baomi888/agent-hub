"use client";

// 知识库域：库列表、名称映射、上传建库 / 联网建库进度 / 删库删文件 / 重建 / 搜索预览
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { KbInfo, SearchResult } from "@/lib/types";
import { describeParams, type ChunkParams } from "@/lib/chunkProfiles";
import { forgetKbChunk, loadKbChunks, saveKbChunks, type KbChunkMap } from "@/lib/kbChunkPrefs";
import type { ShowToast } from "./useToast";

export function useKnowledge(showToast: ShowToast, chunkSize: number, chunkOverlap: number) {
  const [kbs, setKbs] = useState<KbInfo[]>([]);
  // 首屏库列表还没回来：右栏渲染骨架
  const [loading, setLoading] = useState(true);
  // 每个库各自的处理方式（浏览器本地）。用 effect 读，避免 SSR 期访问 localStorage
  const [kbChunks, setKbChunks] = useState<KbChunkMap>({});

  useEffect(() => {
    setKbChunks(loadKbChunks());
  }, []);

  /** 记住某个库这次用的参数，供列表标签和「重新处理」复用 */
  const rememberChunk = useCallback((kbId: string, params: ChunkParams) => {
    setKbChunks((prev) => {
      const old = prev[kbId];
      if (old && old.size === params.size && old.overlap === params.overlap) return prev;
      const next = { ...prev, [kbId]: params };
      saveKbChunks(next);
      return next;
    });
  }, []);

  /** 这个库该用哪套参数：记过就用记录的，没记过（老库/首次）用全局兜底值 */
  const chunkParamsOf = useCallback(
    (kbId: string | null | undefined): ChunkParams =>
      (kbId ? kbChunks[kbId] : undefined) ?? { size: chunkSize, overlap: chunkOverlap },
    [kbChunks, chunkSize, chunkOverlap]
  );

  const refreshKbs = useCallback(async () => {
    try {
      const { kbs: list } = await api.listKbs();
      setKbs(list);
    } catch (e) {
      console.error(e);
      // 同文件里 deleteKb / rebuildKb / deleteKbFile 失败都会 toast，只有这里静默：
      // 删完库再刷新失败的话，右栏还挂着已经删掉的库，用户会以为没删掉、再点一次。
      showToast("error", "知识库列表刷新失败，请稍后重试");
    }
  }, [showToast]);

  // 初始化：拉知识库列表（与 sessions/defaults 的请求并发）
  useEffect(() => {
    let alive = true;
    (async () => {
      const k = await api.listKbs().catch(() => null);
      if (!alive) return;
      if (k) setKbs(k.kbs);
      setLoading(false);
    })();
    return () => {
      alive = false;
    };
  }, []);

  // kb_id → 中文显示名映射（侧栏/顶栏展示用）
  const kbNameMap = useMemo(() => {
    const m: Record<string, string> = {};
    for (const k of kbs) m[k.kb_id] = k.name || k.kb_id;
    return m;
  }, [kbs]);

  // 上传文档建库：先把中文名解析为安全 kb_id，再上传。
  // size/overlap 由面板按「处理方式」档位传入；没传则用全局兜底值。
  const uploadFiles = async (kbName: string, files: File[], size?: number, overlap?: number) => {
    const params: ChunkParams = {
      size: size ?? chunkSize,
      overlap: overlap ?? chunkOverlap,
    };
    // 本次是否新建了库：上传失败要把它删掉，否则列表里会多一个名字相同、里面空空的库
    let createdId: string | null = null;
    try {
      // 名字对上已有库则复用，避免手打差一点就新建同名库
      const norm = kbName.trim().toLowerCase();
      const existing = kbs.find((k) => (k.name || "").trim().toLowerCase() === norm);
      let kb_id: string;
      if (existing) {
        kb_id = existing.kb_id;
      } else {
        kb_id = (await api.createKb(kbName)).kb_id;
        createdId = kb_id;
      }
      const r = await api.uploadFiles(kb_id, files, params.size, params.overlap);
      // 建库/追加成功才记档位：失败回滚的库不该留下记录
      rememberChunk(kb_id, params);
      showToast("success", `已导入 ${r.chunks} 个片段到「${kbName}」`);
    } catch (e) {
      // 回滚空壳库（复用已有库时不删，那里面可能有别的文件）
      if (createdId) {
        await api.deleteKb(createdId).catch(() => {});
        createdId = null;
      }
      const msg = e instanceof Error ? e.message : "上传失败，请稍后重试";
      showToast("error", msg);
    } finally {
      refreshKbs();
    }
  };

  // 删除库内单个文件：只清它的片段，不动其他文件
  const deleteKbFile = async (kbId: string, name: string) => {
    try {
      const r = await api.deleteKbFile(kbId, name);
      showToast("success", r.removed > 0 ? `已删除「${name}」的 ${r.removed} 个片段` : `已移除「${name}」`);
      refreshKbs();
    } catch (e) {
      showToast("error", e instanceof Error ? e.message : "删除文件失败，请稍后重试");
    }
  };

  // 按这个库自己的处理方式重新切整库（要重新 embedding，所以面板上点了要确认）
  const rebuildKb = async (kbId: string) => {
    const { size, overlap } = chunkParamsOf(kbId);
    try {
      const r = await api.rebuildKb(kbId, size, overlap);
      showToast("success", `已按「${describeParams(size, overlap)}」重新整理，共 ${r.chunks} 个片段`);
      refreshKbs();
    } catch (e) {
      showToast("error", e instanceof Error ? e.message : "重新整理失败，请稍后重试");
    }
  };

  const searchPreview = async (keyword: string): Promise<SearchResult[]> => {
    const r = await api.searchPreview(keyword);
    return r.results;
  };

  const deleteKb = async (kbId: string) => {
    try {
      await api.deleteKb(kbId);
      // 库没了，本地记的档位也一并清掉（否则重建同名库会继承旧设置）
      forgetKbChunk(kbId);
      setKbChunks((prev) => {
        if (!(kbId in prev)) return prev;
        const next = { ...prev };
        delete next[kbId];
        return next;
      });
      showToast("info", "知识库已删除");
      refreshKbs();
    } catch (e) {
      showToast("error", "删除失败，请稍后重试");
    }
  };

  return {
    kbs,
    kbNameMap,
    loading,
    refreshKbs,
    uploadFiles,
    deleteKbFile,
    rebuildKb,
    searchPreview,
    deleteKb,
    /** 供面板回填每个库的处理方式标签 */
    kbChunks,
    rememberChunk,
    chunkParamsOf,
  };
}
