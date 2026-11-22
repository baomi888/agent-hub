/**
 * 大学时间表 · 考证与升学就业（18 条）
 *
 * 15 项证书考试（`cert`）+ 3 项升学就业考试（`exam`）。
 *
 * 为什么把考研、国考、省考单列成 `exam` 而不是塞进 `cert`：
 * 它们不发证书，是「换一个去处」的考试，报名条件、准备周期、时间点都跟考证不同。
 * 混在一起会让人误以为考研也是「考个证」，也会让「已通过」这个词变得不准确。
 *
 * **全年可报**的三项（普通话、雅思、托福）用 `from: 1, to: 12` 表示，
 * 逻辑层按「全年」处理：日历格不逐日染色，只在时间轴底部单列一区。
 * 否则这三条会把整年的每一天都染上颜色，反而淹掉真正有硬时间点的赛事。
 *
 * 时间一律标「往年规律」，`verified` 里是已核实的 2026 年实际日期。
 */

import type { EventItem } from "../../types";

export const CERTS: readonly EventItem[] = [
  /* ==================== 语言类（7） ==================== */
  {
    id: "cet4",
    name: "大学英语四级考试（CET-4）",
    short: "四级",
    kind: "cert",
    category: "language",
    tags: ["英语", "一年两次"],
    cycle: "annual",
    grades: [1, 2],
    signup: [
      { from: 3, to: 3, note: "上半年报名" },
      { from: 9, to: 10, note: "下半年报名" },
    ],
    runs: [
      { from: 6, to: 6, note: "6 月中旬", exact: "2026-06-13" },
      { from: 12, to: 12, note: "12 月中旬", exact: "2026-12-12" },
    ],
    intro:
      "425 分是大多数学校毕业与保研的门槛线，也是很多岗位的硬筛选项。一年两次，越早考掉越好 —— 大三大四会被实习和论文挤掉准备时间。",
    fitFor: "全专业，大一大二最合适",
    require: "在校生，通过学校统一报名；六级需四级 ≥ 425",
    officialUrl: "https://cet.neea.edu.cn",
    verified: { year: 2026, text: "上半年 6 月 13 日 · 下半年 12 月 12 日" },
  },
  {
    id: "cet6",
    name: "大学英语六级考试（CET-6）",
    short: "六级",
    kind: "cert",
    category: "language",
    tags: ["英语", "一年两次"],
    cycle: "annual",
    grades: [2, 3],
    signup: [
      { from: 3, to: 3, note: "上半年报名" },
      { from: 9, to: 10, note: "下半年报名" },
    ],
    runs: [
      { from: 6, to: 6, note: "6 月中旬（下午场）", exact: "2026-06-13" },
      { from: 12, to: 12, note: "12 月中旬（下午场）", exact: "2026-12-12" },
    ],
    intro:
      "保研、外企与部分公务员岗位会看六级分数，500 分以上才算有竞争力。四级过了就接着考，别拖。",
    fitFor: "四级已过 425 的",
    require: "CET-4 ≥ 425 才能报",
    officialUrl: "https://cet.neea.edu.cn",
    verified: { year: 2026, text: "与四级同日，下午场" },
  },
  {
    id: "tem4",
    name: "英语专业四级考试（TEM-4）",
    short: "专四",
    kind: "cert",
    category: "language",
    tags: ["英语专业"],
    cycle: "annual",
    grades: [2],
    signup: [{ from: 12, to: 12, note: "上年 12 月由学校统一报名" }],
    runs: [{ from: 4, to: 4, note: "4 月中下旬" }],
    intro: "英语专业的第一个硬指标，很多学校把它和学位挂钩。只有一次正考机会，补考仅一次。",
    fitFor: "英语专业大二",
    require: "仅英语专业，由学校统一报名",
  },
  {
    id: "tem8",
    name: "英语专业八级考试（TEM-8）",
    short: "专八",
    kind: "cert",
    category: "language",
    tags: ["英语专业"],
    cycle: "annual",
    grades: [4],
    signup: [{ from: 12, to: 12, note: "上年 12 月由学校统一报名" }],
    runs: [{ from: 3, to: 3, note: "3 月中下旬" }],
    intro: "英语专业最高级别的证书，毕业前最后一次机会。教师编与翻译岗常把它当门槛。",
    fitFor: "英语专业大四",
    require: "仅英语专业，需先通过专四或学校认可",
  },
  {
    id: "ielts",
    name: "雅思（IELTS）",
    short: "雅思",
    kind: "cert",
    category: "language",
    tags: ["英语", "全年可报"],
    cycle: "annual",
    runs: [{ from: 1, to: 12, note: "全年多场，按考点安排" }],
    intro:
      "出国留学与部分中外合作项目的语言门槛，成绩两年有效。也有人在保研夏令营前考一次作为英语能力佐证。",
    fitFor: "有出国打算，或需要英语能力证明的",
    require: "无门槛，自行在官网选考位",
    officialUrl: "https://www.chinaielts.org",
  },
  {
    id: "toefl",
    name: "托福（TOEFL iBT）",
    short: "托福",
    kind: "cert",
    category: "language",
    tags: ["英语", "全年可报"],
    cycle: "annual",
    runs: [{ from: 1, to: 12, note: "全年多场，按考点安排" }],
    intro: "北美方向留学的语言考试，成绩两年有效。与雅思二选一即可，不必都考。",
    fitFor: "申请北美院校的",
    require: "无门槛，自行在官网选考位",
    officialUrl: "https://toefl.neea.cn",
  },
  {
    id: "putonghua",
    name: "普通话水平测试",
    short: "普通话",
    kind: "cert",
    category: "language",
    tags: ["教师资格", "全年可报"],
    cycle: "annual",
    runs: [{ from: 1, to: 12, note: "各校考点分批组织" }],
    intro:
      "教师资格认定的必备项，语文教师要求二级甲等（87 分）以上，其他学科二级乙等即可。校内考点通常比社会考点好约。",
    fitFor: "师范类、准备考教资的",
    require: "无门槛，通过本校测试站报名",
    officialUrl: "https://www.cltt.org",
  },

  /* ==================== 计算机类（2） ==================== */
  {
    id: "ncre2",
    name: "全国计算机等级考试二级（NCRE）",
    short: "计算机二级",
    kind: "cert",
    category: "csai",
    tags: ["一年两次", "Office/Python"],
    cycle: "annual",
    grades: [1, 2],
    signup: [
      { from: 12, to: 1, note: "上半年报名（上年 12 月至当年 1 月）" },
      { from: 6, to: 7, note: "下半年报名" },
    ],
    runs: [
      { from: 3, to: 3, note: "3 月底（2026 年为 28–30 日）", exact: "2026-03-28" },
      { from: 9, to: 9, note: "9 月中下旬（2026 年为 19–21 日）", exact: "2026-09-19" },
    ],
    intro:
      "多数人考 MS Office 或 Python。部分学校把它和学位、综测挂钩，公务员部分岗位也认。题目套路固定，刷真题通过率不低。",
    fitFor: "全专业，尤其非计算机专业",
    require: "无门槛，自行在官网报名",
    officialUrl: "https://ncre.neea.edu.cn",
    verified: { year: 2026, text: "3 月 28–30 日 · 9 月 19–21 日" },
  },
  {
    id: "ruankao",
    name: "计算机技术与软件专业技术资格（软考）",
    short: "软考",
    kind: "cert",
    category: "csai",
    tags: ["一年两次", "职称"],
    cycle: "annual",
    signup: [
      { from: 3, to: 3, note: "上半年报名" },
      { from: 8, to: 8, note: "下半年报名" },
    ],
    runs: [
      { from: 5, to: 5, note: "5 月下旬" },
      { from: 11, to: 11, note: "11 月上旬" },
    ],
    intro:
      "国家级职业资格，中级（软件设计师）与高级（系统架构师）含金量较高，部分城市可评职称、落户加分。考试偏理论，需背。",
    fitFor: "计算机专业，或想要职称凭证的",
    require: "无学历门槛，自行在官网报名",
    officialUrl: "https://www.ruankao.org.cn",
  },

  /* ==================== 教育类（2） ==================== */
  {
    id: "ntce-written",
    name: "中小学教师资格证 · 笔试",
    short: "教资笔试",
    kind: "cert",
    category: "language",
    tags: ["教师", "一年两次"],
    cycle: "annual",
    grades: [3, 4],
    signup: [
      { from: 1, to: 1, note: "上半年报名" },
      { from: 7, to: 7, note: "下半年报名" },
    ],
    runs: [
      { from: 3, to: 3, note: "3 月上旬", exact: "2026-03-07" },
      { from: 9, to: 9, note: "9 月中旬", exact: "2026-09-12" },
    ],
    intro:
      "教师资格的第一关，考综合素质 + 教育知识与能力（中学还要加学科知识）。笔试成绩两年有效，可以先考笔试再慢慢准备面试。",
    fitFor: "师范类必考；非师范想当老师的",
    require: "在校生大三可报，需学历符合学段要求",
    officialUrl: "https://ntce.neea.edu.cn",
    verified: { year: 2026, text: "上半年 3 月 7 日 · 下半年 9 月 12 日" },
  },
  {
    id: "ntce-interview",
    name: "中小学教师资格证 · 面试",
    short: "教资面试",
    kind: "cert",
    category: "language",
    tags: ["教师", "一年两次"],
    cycle: "annual",
    grades: [3, 4],
    signup: [
      { from: 4, to: 4, note: "上半年报名" },
      { from: 11, to: 11, note: "下半年报名" },
    ],
    runs: [
      { from: 5, to: 5, note: "5 月中旬", exact: "2026-05-16" },
      { from: 12, to: 12, note: "12 月上旬", exact: "2026-12-05" },
    ],
    intro:
      "结构化问答 + 试讲 + 答辩，20 分钟。笔试过了才能报，试讲要提前练 —— 很多人栽在「讲不完」。",
    fitFor: "教资笔试已通过的",
    require: "笔试各科合格且在有效期内",
    officialUrl: "https://ntce.neea.edu.cn",
    verified: { year: 2026, text: "上半年 5 月 16–17 日 · 下半年 12 月 5–6 日" },
  },

  /* ==================== 财会法类（4） ==================== */
  {
    id: "chuji-kuaiji",
    name: "初级会计职称",
    short: "初级会计",
    kind: "cert",
    category: "business",
    tags: ["财会", "入门"],
    cycle: "annual",
    signup: [{ from: 1, to: 1, note: "1 月报名" }],
    runs: [{ from: 5, to: 5, note: "5 月中旬（2026 年为 16–18 日）", exact: "2026-05-16" }],
    intro:
      "财会岗的入门证，考《初级会计实务》与《经济法基础》两科，一年一考、两科同时过才算。大一大二就能考。",
    fitFor: "会计、财务管理，或想转财会的",
    require: "高中及以上学历即可",
    officialUrl: "https://kzp.mof.gov.cn",
    verified: { year: 2026, text: "5 月 16–18 日" },
  },
  {
    id: "cpa",
    name: "注册会计师（CPA）专业阶段",
    short: "CPA",
    kind: "cert",
    category: "business",
    tags: ["财会", "高含金量"],
    cycle: "annual",
    grades: [4],
    signup: [{ from: 4, to: 4, note: "4 月报名" }],
    runs: [{ from: 8, to: 8, note: "8 月下旬（2026 年为 29–30 日）", exact: "2026-08-29" }],
    intro:
      "财会领域含金量最高的证书，六科五年内通过。在校生一般大四才能报，但大三就可以开始看《会计》—— 这是最花时间的一科。",
    fitFor: "会计、审计，目标四大或券商的",
    require: "需高等专科以上学历（应届可报）",
    officialUrl: "https://www.cicpa.org.cn",
    verified: { year: 2026, text: "8 月 29–30 日" },
  },
  {
    id: "fakao-objective",
    name: "法律职业资格考试 · 客观题",
    short: "法考客观",
    kind: "cert",
    category: "general",
    tags: ["法学", "强壁垒"],
    cycle: "annual",
    signup: [{ from: 6, to: 6, note: "6 月报名" }],
    runs: [{ from: 9, to: 9, note: "9 月中旬（2026 年为 12–13 日）", exact: "2026-09-12" }],
    intro:
      "法考第一关，两卷选择题。通过后才能报名主观题，客观题成绩两年有效。法学专业的核心门槛证。",
    fitFor: "法学专业，或符合报考条件的",
    require: "法学类本科，或非法本 + 法律硕士/三年法律工作",
    officialUrl: "https://www.moj.gov.cn",
    verified: { year: 2026, text: "9 月 12–13 日" },
  },
  {
    id: "fakao-subjective",
    name: "法律职业资格考试 · 主观题",
    short: "法考主观",
    kind: "cert",
    category: "general",
    tags: ["法学", "强壁垒"],
    cycle: "annual",
    signup: [{ from: 9, to: 9, note: "客观题成绩公布后报名" }],
    runs: [{ from: 10, to: 10, note: "10 月中旬（2026 年为 10 月 18 日）", exact: "2026-10-18" }],
    intro: "四小时案例分析 + 论述，考的是把法条用对。客观题过了才有资格，两关都过才拿证。",
    fitFor: "法考客观题已通过的",
    require: "客观题成绩合格且在有效期内",
    officialUrl: "https://www.moj.gov.cn",
    verified: { year: 2026, text: "10 月 18 日" },
  },

  /* ==================== 升学就业（3） ==================== */
  {
    id: "kaoyan",
    name: "全国硕士研究生招生考试（考研）",
    short: "考研初试",
    kind: "exam",
    category: "career",
    tags: ["升学", "大四主战场"],
    cycle: "annual",
    grades: [4],
    signup: [{ from: 10, to: 10, note: "10 月上中旬预报名与正式报名" }],
    runs: [{ from: 12, to: 12, note: "12 月下旬初试（通常 25–27 日）" }],
    intro:
      "大四的主战场，初试在 12 月下旬，复试在次年 3–4 月。真正决定结果的是 3–6 月的专业课复习，报名只是节点。",
    fitFor: "大四准备升学的；大三下就该定校定专业",
    require: "应届本科生（大四）或已毕业",
    officialUrl: "https://yz.chsi.com.cn",
  },
  {
    id: "guokao",
    name: "国家公务员考试（国考）",
    short: "国考",
    kind: "exam",
    category: "career",
    tags: ["就业", "应届优势"],
    cycle: "annual",
    grades: [4],
    signup: [{ from: 10, to: 10, note: "10 月中下旬报名" }],
    runs: [{ from: 11, to: 11, note: "11 月底笔试" }],
    intro:
      "应届生可报的岗位最多，是「一次上岸」机会最大的一年。笔试考行测 + 申论，面试在次年 2–3 月。",
    fitFor: "大四应届生（应届身份最值钱）",
    require: "大专以上学历，具体看职位表",
    officialUrl: "https://bm.scs.gov.cn",
  },
  {
    id: "gd-exam",
    name: "广东省公务员考试（省考）",
    short: "广东省考",
    kind: "exam",
    category: "career",
    tags: ["就业", "广东"],
    cycle: "annual",
    grades: [4],
    signup: [{ from: 1, to: 3, note: "1–3 月报名，逐年浮动" }],
    runs: [{ from: 3, to: 4, note: "3–4 月笔试" }],
    intro:
      "广东单独命题，笔试通常比联考早。想在广东就业的，国考与省考可以都报，时间不冲突。",
    fitFor: "想在广东就业的应届生",
    require: "大专以上学历，具体看职位表",
  },
];
