// 历史消息兜底清洗（2026-11-23）
//
// 为什么前端还要再做一次：后端 `_StreamDraft` 现在落库前就剥掉了资料块，
// 所以**新**回答进库时已经干净。但本轮修复之前写进库的老消息仍然带着
// 「【资料开始·用户上传】…score=0.721…【资料结束·用户上传】」整段 ——
// 用户翻历史时还会看到。用户选的口径是「只剥离资料块，其余历史一律不动」，
// 意思是不去刷线上库；那就在渲染这一层顺手剥掉，等价效果、零迁移风险。
//
// 与后端 core/data_boundary.strip_blocks 的规则保持一致（同样的容错）。
// ⚠️ 这里只用于**展示**，绝不回写数据库 —— 「不动历史」是承诺。

const OPEN_CH = "[【\\[〔〖]";
const CLOSE_CH = "[】\\]〕〗]";
// 分隔符容错：·/・/．/空格/冒号等，模型复述时不逐字节照抄
const SEP = "[·・．.\\-_—–/／|｜:：,，、~～•\\s]*";
const SRC_NAME = "(知识库|联网|用户上传)";

const BLOCK_RE = new RegExp(
  `${OPEN_CH}资料开始${SEP}${SRC_NAME}${CLOSE_CH}[\\s\\S]*?${OPEN_CH}资料结束${SEP}\\1${CLOSE_CH}`,
  "g"
);
// 只有开标签没闭标签（模型被截断）：一路吃到文末
const OPEN_ONLY_RE = new RegExp(`${OPEN_CH}资料开始${SEP}${SRC_NAME}${CLOSE_CH}[\\s\\S]*$`);

/** 把历史正文里的资料块摘掉，只用于显示。无标签时原样返回同一个字符串。 */
export function stripLegacyBlocks(text: string): string {
  if (!text || !text.includes("资料开始")) return text;
  let out = text.replace(BLOCK_RE, "\n");
  out = out.replace(OPEN_ONLY_RE, "");
  // 剥完会留下连续空行和行尾空白
  out = out.replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
  return out;
}