// 每个资料库各自记住自己的处理方式。
//
// 教材库和访谈实录本来就该用不同的切法，一套全局参数套所有库并不符合直觉。
// 后端只认请求里的 chunk_size / chunk_overlap，不记录"这个库当初怎么切的"，
// 所以这里在浏览器本地存一份映射：kb_id → 上次用的参数。
// 用途有两个：① 库列表上显示「均衡」这类标签；② 点重新处理时沿用同一套参数。

import type { ChunkParams } from "./chunkProfiles";

const KEY = "baomi.kbChunk.v1";

/** kb_id → 该库上次用的切片参数 */
export type KbChunkMap = Record<string, ChunkParams>;

function isParams(v: unknown): v is ChunkParams {
  if (!v || typeof v !== "object") return false;
  const p = v as Partial<ChunkParams>;
  return (
    typeof p.size === "number" &&
    Number.isFinite(p.size) &&
    typeof p.overlap === "number" &&
    Number.isFinite(p.overlap)
  );
}

export function loadKbChunks(): KbChunkMap {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return {};
    const out: KbChunkMap = {};
    for (const [kbId, v] of Object.entries(parsed as Record<string, unknown>)) {
      if (isParams(v)) out[kbId] = { size: v.size, overlap: v.overlap };
    }
    return out;
  } catch {
    // 解析失败当作没存过：宁可回落到默认档，也不要让面板炸掉
    return {};
  }
}

export function saveKbChunks(map: KbChunkMap) {
  try {
    localStorage.setItem(KEY, JSON.stringify(map));
  } catch {
    /* 隐私模式下写入失败可忽略 */
  }
}

/** 删库时顺手清掉它的记录，避免换个人建同名库时继承旧档位 */
export function forgetKbChunk(kbId: string) {
  const map = loadKbChunks();
  if (!(kbId in map)) return;
  delete map[kbId];
  saveKbChunks(map);
}
