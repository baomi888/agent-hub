"use client";

// 知识库域：库列表、名称映射、上传建库 / 联网建库进度 / 删库删文件 / 重建 / 搜索预览
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { KbInfo, SearchResult } from "@/lib/types";
import type { ShowToast } from "./useToast";

export function useKnowledge(showToast: ShowToast, chunkSize: number, chunkOverlap: number) {
  const [kbs, setKbs] = useState<KbInfo[]>([]);
  // 首屏库列表还没回来：右栏渲染骨架
  const [loading, setLoading] = useState(true);

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

  // 上传文档建库：先把中文名解析为安全 kb_id，再上传（切片参数用当前设置）
  const uploadFiles = async (kbName: string, files: File[]) => {
    try {
      const { kb_id } = await api.createKb(kbName);
      const r = await api.uploadFiles(kb_id, files, chunkSize, chunkOverlap);
      showToast("success", `已导入 ${r.chunks} 个片段到「${kbName}」`);
      refreshKbs();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "上传失败，请稍后重试";
      showToast("error", msg);
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

  // 按当前切片参数重建整库（会对全部文件重新 embedding，所以要点一下确认）
  const rebuildKb = async (kbId: string) => {
    try {
      const r = await api.rebuildKb(kbId, chunkSize, chunkOverlap);
      showToast("success", `已按 ${chunkSize}/${chunkOverlap} 重建，共 ${r.chunks} 个片段`);
      refreshKbs();
    } catch (e) {
      showToast("error", e instanceof Error ? e.message : "重建失败，请稍后重试");
    }
  };

  const searchPreview = async (keyword: string): Promise<SearchResult[]> => {
    const r = await api.searchPreview(keyword);
    return r.results;
  };

  const deleteKb = async (kbId: string) => {
    try {
      await api.deleteKb(kbId);
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
  };
}
