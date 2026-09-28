// 计划卡片：识别「计划类」请求 → 构造格式化提示词 → 解析回答为结构化计划渲染
// 从 ChatArea.tsx 拆出（2026-09 重构）。isPlanRequest/PLAN_TOPICS 是前端关键词硬编码，
// 长期方案是后端给结构化 plan 字段（见 docs/前端重构说明.md P2）
import { Fragment, useState } from "react";
import { useCopy } from "@/lib/useCopy";

export const PLAN_KEYWORDS = [
  "计划", "策划", "方案", "安排", "规划", "路线图", "日程", "排期",
  "攻略", "行程", "roadmap", "plan",
];

export function isPlanRequest(q?: string): boolean {
  if (!q) return false;
  const lq = q.toLowerCase();
  return PLAN_KEYWORDS.some((k) => lq.includes(k));
}

export interface PlanTopic {
  name: string;
  hint: string;
}

export const PLAN_TOPICS: { keys: string[]; topic: PlanTopic }[] = [
  {
    keys: ["营销", "发布会", "推广", "上市", "促销", "品牌", "市场", "宣发", "campaign", "活动策划"],
    topic: { name: "营销活动", hint: "时间轴建议按「第1周 筹备期 / 第2周 预热期 / 第3周 爆发期」划分。" },
  },
  {
    keys: ["学习", "备考", "复习", "考研", "考公", "雅思", "托福", "读书", "课程", "培训", "考试", "study"],
    topic: { name: "学习计划", hint: "时间轴建议按「第1周 ~ 第N周」划分，阶段按科目或模块编号。" },
  },
  {
    keys: ["旅行", "旅游", "出游", "自驾", "攻略", "度假", "出差", "trip", "travel"],
    topic: { name: "旅行行程", hint: "时间轴建议按「DAY 1 / DAY 2」或「第1天 / 第2天」划分。" },
  },
  {
    keys: ["健身", "训练", "减脂", "增肌", "跑步", "马拉松", "瑜伽", "运动", "fitness"],
    topic: { name: "健身训练", hint: "时间轴建议按「第1周 ~ 第N周」划分，阶段按训练部位编号。" },
  },
  {
    keys: ["婚礼", "订婚", "求婚", "派对", "聚会", "生日", "wedding"],
    topic: { name: "婚礼派对", hint: "时间轴建议按「筹备期 / 当天 / 收尾」或「第N个月」划分。" },
  },
  {
    keys: ["搬家", "装修", "改造", "布置", "收纳", "move"],
    topic: { name: "搬家装修", hint: "时间轴建议按「第N周」或「打包 / 运输 / 布置」阶段划分。" },
  },
  {
    keys: ["内容", "写作", "文案", "脚本", "公众号", "小红书", "直播", "自媒体", "专栏", "书籍", "论文", "content", "video"],
    topic: { name: "内容创作", hint: "时间轴建议按「第N周」或「选题 / 撰写 / 发布 / 复盘」阶段划分。" },
  },
  {
    keys: ["产品", "开发", "研发", "上线", "迭代", "立项", "需求", "版本", "发布"],
    topic: { name: "产品研发", hint: "时间轴建议按「第N周」或「需求 / 设计 / 开发 / 测试 / 上线」阶段划分。" },
  },
  {
    keys: ["创业", "开店", "经营", "融资", "商业", "公司", "团队", "startup"],
    topic: { name: "创业经营", hint: "时间轴建议按「第N月」或「筹备 / 启动 / 运营」阶段划分。" },
  },
  {
    keys: ["预算", "财务", "理财", "省钱", "采购", "成本", "资金", "budget"],
    topic: { name: "财务预算", hint: "时间轴建议按「第N周」或「盘点 / 规划 / 执行 / 复盘」阶段划分。" },
  },
];

export function detectPlanTopic(q?: string): PlanTopic | null {
  if (!q) return null;
  const lq = q.toLowerCase();
  for (const t of PLAN_TOPICS) {
    if (t.keys.some((k) => lq.includes(k))) return t.topic;
  }
  return null;
}

export function buildPlanPrompt(raw: string, topic: PlanTopic | null): string {
  const hint = topic?.hint ?? "时间轴建议按「第1周 / 第N天 / DAY N」划分，阶段用编号列出。";
  return (
    raw +
    "\n\n【格式要求】请直接输出一份结构化计划，不要解释。格式如下：\n" +
    "第一行：计划标题\n" +
    "时间轴：每行以「第1周 xxx」「第N天 xxx」或「DAY 1 xxx」开头，简短\n" +
    "阶段清单：每行用「1. xxx」编号，标题一行、说明一行\n" +
    hint
  );
}

export function stripPlanHint(text: string): string {
  const i = text.indexOf("\n\n【格式要求】");
  if (i >= 0) return text.slice(0, i);
  return text;
}

interface PlanPhase {
  title: string;
  desc?: string;
}
interface PlanData {
  title: string;
  timeline: string[];
  phases: PlanPhase[];
}

function cleanLine(l: string): string {
  return l
    .replace(/^#{1,6}\s*/, "")
    .replace(/\*\*/g, "")
    .replace(/^[-*•·]\s*/, "")
    .replace(/^>\s*/, "")
    .replace(/^【[^】]*】\s*/, "")
    .trim();
}

export function parsePlan(content: string): PlanData | null {
  const rawLines = content.split("\n");
  const raws: string[] = [];
  const lines: string[] = [];
  for (const raw of rawLines) {
    const line = cleanLine(raw);
    if (line) {
      raws.push(raw.trim());
      lines.push(line);
    }
  }
  if (lines.length < 2) return null;
  const title = lines[0].length <= 60 ? lines[0] : lines[0].slice(0, 60);
  const timeline: string[] = [];
  const phases: PlanPhase[] = [];
  let cur: PlanPhase | null = null;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const raw = raws[i];
    if (
      line.length <= 30 &&
      /(第?[一二三四五六七八九十0-9]{1,3}周|DAY\s*\d{1,2}|第[一二三四五六七八九十0-9]{1,3}天|第[一二三四五六七八九十0-9]{1,3}晚|[0-9]{1,2}月|[0-9]{1,2}日|第[一二三四五六七八九十0-9]{1,3}课时|[0-9]{4}[-年]|[0-9]{1,2}[-/~至—][0-9]{1,2}|筹备期|预热期|执行期|收尾|第[一二三四五六七八九十0-9]{1,3}阶段)/i.test(line)
    ) {
      timeline.push(line);
      continue;
    }
    const isPhase =
      /^(0?[0-9]{1,2}[、.．:：]|[一二三四五六七八九十]{1,3}[、.．:：]|第[一二三四五六七八九十0-9]{1,3}[阶段部分]|步骤\s*[0-9一二三四五六七八九十]{1,3}|[-*•·]\s)/.test(raw);
    if (isPhase) {
      if (cur) phases.push(cur);
      cur = {
        title: line
          .replace(/^0?[0-9]{1,2}[、.．:：]/, "")
          .replace(/^[一二三四五六七八九十]{1,3}[、.．:：]/, "")
          .replace(/^第[一二三四五六七八九十0-9]{1,3}[阶段部分]\s*/, "")
          .replace(/^步骤\s*[0-9一二三四五六七八九十]{1,3}[:：]?\s*/, "")
          .replace(/^[-*•·]\s*/, "")
          .trim(),
      };
    } else if (cur) {
      cur.desc = cur.desc ? cur.desc + " " + line : line;
    }
  }
  if (cur) phases.push(cur);
  if (phases.length === 0 && timeline.length < 2) return null;
  if (!title) return null;
  return { title, timeline, phases };
}

function PlanCard({
  data,
  content,
  topic,
  onCopy,
}: {
  data: PlanData;
  content: string;
  topic: PlanTopic | null;
  onCopy: () => void;
}) {
  const { copied, copy } = useCopy();

  const doCopy = async () => {
    await copy(content);
    onCopy();
  };

  return (
    <div className="plan-card fade-up">
      <div className="plan-eyebrow">PLAN · {topic?.name ?? "结构化计划"}</div>
      <div className="plan-title">{data.title}</div>
      {data.timeline.length > 0 && (
        <div className="plan-timeline">
          {data.timeline.map((t, i) => (
            <Fragment key={i}>
              {i > 0 && <span className="plan-tl-line" />}
              <span className="plan-tl-node">
                <i />
                {t}
              </span>
            </Fragment>
          ))}
        </div>
      )}
      {data.phases.length > 0 && (
        <div className="plan-phases">
          {data.phases.map((p, i) => (
            <div key={i} className="plan-phase">
              <span className="plan-idx">{String(i + 1).padStart(2, "0")}</span>
              <div>
                <div className="plan-phase-title">{p.title}</div>
                {p.desc && <div className="plan-phase-desc">{p.desc}</div>}
              </div>
            </div>
          ))}
        </div>
      )}
      <div className="plan-actions">
        <button className="plan-export" onClick={doCopy}>
          {copied ? "已复制" : "导出计划"} · 复制全文
        </button>
        <span className="plan-hint">可继续追问：把某阶段拆细 / 调整时间</span>
      </div>
    </div>
  );
}

export default PlanCard;
