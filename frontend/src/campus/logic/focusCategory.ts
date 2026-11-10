/**
 * 校园工具集 · 番茄钟大类归类（纯函数）
 *
 * 为什么需要这一层：饼图如果按任务名逐条画，一个学期下来会碎成几十片
 * （「高数作业」「高数复习」「高数第三章」各算一片），看不出时间到底花在哪。
 * 所以统计按**大类**聚合，任务名只用来猜大类。
 *
 * 规则就两条，简单到可以背下来：
 *  1. 用户手动选了大类 → 一律听用户的，不再猜；
 *  2. 没手动选 → 从上到下扫关键词，第一个命中的大类胜出，都没命中就是「未分类」。
 *
 * 关键词表刻意保持短：命中率靠的是「常见叫法」，不是穷举。
 * 猜错了不要紧，用户可以手动改；猜得太激进反而会让人反复纠正。
 */

/** 兜底大类。不要改成空串，饼图需要一个有名字的分区 */
export const DEFAULT_CATEGORY = "未分类";

/** 一条归类规则：大类名 + 触发它的关键词 */
export interface CategoryRule {
  category: string;
  keywords: readonly string[];
}

/**
 * 关键词表。**数组顺序即优先级**——靠前的大类先被匹配。
 *
 * 排在前面的几个是校园里最高频的科目。新增规则时注意别放泛词：
 * 例如数学类不要放「分析」，否则「分析化学」会被判成数学。
 */
export const CATEGORY_RULES: readonly CategoryRule[] = [
  {
    category: "数学",
    keywords: [
      "数学",
      "高数",
      "高等数学",
      "线代",
      "线性代数",
      "概率",
      "数理统计",
      "微积分",
      "数分",
      "数学分析",
      "离散",
      "运筹",
    ],
  },
  {
    category: "英语",
    keywords: ["英语", "四级", "六级", "单词", "听力", "阅读", "作文", "翻译", "口语", "雅思", "托福"],
  },
  {
    category: "政治",
    keywords: ["政治", "马原", "毛概", "思修", "近代史", "马克思", "党史", "时政", "形势与政策"],
  },
  {
    category: "物理",
    keywords: ["物理", "大物", "力学", "电磁", "热学", "光学"],
  },
  {
    category: "化学",
    keywords: ["化学", "有机", "无机", "生化"],
  },
  {
    category: "生物",
    keywords: ["生物", "遗传", "细胞", "生态"],
  },
  {
    category: "计算机",
    keywords: [
      "编程",
      "代码",
      "算法",
      "数据结构",
      "计网",
      "计算机网络",
      "操作系统",
      "数据库",
      "编译",
      "python",
      "java",
      "c++",
      "js",
      "前端",
      "后端",
      "机器学习",
      "深度学习",
    ],
  },
  {
    category: "语文",
    keywords: ["语文", "文学", "现代文", "古诗文", "古文"],
  },
  {
    category: "论文",
    keywords: ["论文", "毕设", "开题", "文献", "综述", "答辩", "毕业设计"],
  },
  {
    category: "实验",
    keywords: ["实验", "实习", "实验报告"],
  },
];

/** 所有可选大类（含兜底），按规则顺序，供下拉框使用 */
export function listCategories(): string[] {
  return [...CATEGORY_RULES.map((rule) => rule.category), DEFAULT_CATEGORY];
}

/** 清洗用户输入的大类名：去空白，空串归为未分类 */
export function normalizeCategory(input: string | null | undefined): string {
  const text = (input ?? "").trim();
  return text === "" ? DEFAULT_CATEGORY : text;
}

/**
 * 按任务名猜大类。只做子串匹配，不做分词 —— 中文任务名短，够用。
 *
 * @param name 任务名，如「高数第三章习题」
 * @returns 命中的大类；没命中返回 `未分类`
 */
export function guessCategory(name: string | null | undefined): string {
  const text = (name ?? "").toLowerCase();
  if (text.trim() === "") return DEFAULT_CATEGORY;

  for (const rule of CATEGORY_RULES) {
    for (const keyword of rule.keywords) {
      if (text.includes(keyword.toLowerCase())) return rule.category;
    }
  }

  return DEFAULT_CATEGORY;
}

/**
 * 最终大类：手动值优先，没填才去猜。
 *
 * 注意「手动填了未分类」和「没填」是两回事：前者是个明确选择，不该被猜成数学。
 * 所以这里判断的是「有没有填」，而不是「填的值是不是未分类」。
 */
export function resolveCategory(name: string | null | undefined, manual?: string | null): string {
  const picked = (manual ?? "").trim();
  if (picked !== "") return picked;
  return guessCategory(name);
}
