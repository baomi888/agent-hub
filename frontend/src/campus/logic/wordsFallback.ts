/**
 * 校园工具集 · 内置兜底词库
 *
 * 只在 data/words/*.json 拉取失败时使用（断网、部署漏传静态资源）。
 * 内容与旧版 tools.html 的 FALLBACK_WORDS 完全一致，从旧文件脚本提取，未手抄。
 * 词条元组顺序：[单词, 释义, 例句, 译文]。
 */

import type { WordItem } from "../types";

export const FALLBACK_WORDS: readonly WordItem[] = [
  ["abandon","v. 放弃，抛弃","They had to abandon the plan because of the sudden storm.","由于突如其来的暴风雨，他们不得不放弃这个计划。"],
  ["abundant","adj. 丰富的，大量的","The region has abundant natural resources, which support the development of local agriculture.","该地区拥有丰富的自然资源，这支撑了当地农业的发展。"],
  ["accessible","adj. 可接近的；易懂的","The new library is easily accessible to all students, even those without a car.","新图书馆对所有学生都很方便到达，即使是没有车的学生也是如此。"],
  ["accumulate","v. 积累，积聚","Over the years, he accumulated a large collection of old books.","多年来，他积累了大量的旧书。"],
  ["acknowledge","v. 承认；致谢","She acknowledged her mistake and promised to correct it immediately.","她承认了自己的错误，并承诺立即改正。"],
  ["adequate","adj. 足够的，适当的","The training was not adequate for the difficult tasks ahead.","对于前方艰巨的任务来说，这次培训并不充分。"],
  ["advocate","v. 提倡；n. 拥护者","Many doctors advocate a healthy diet and regular exercise for people of all ages.","许多医生提倡各年龄段的人都要健康饮食和规律锻炼。"],
  ["alter","v. 改变，修改","We had to alter our plans when the weather suddenly turned bad.","天气突然变坏时，我们不得不改变计划。"],
  ["ambiguous","adj. 模棱两可的","The manager's ambiguous answer left everyone unsure about what to do next.","经理含糊其辞的回答让大家都不确定接下来该做什么。"],
  ["anticipate","v. 预期，期望","We anticipate that the new policy will reduce costs for most families.","我们预计新政策将为大多数家庭降低成本。"],
  ["appropriate","adj. 合适的","Please choose an appropriate time to discuss this issue with your professor.","请选择一个合适的时间与你的教授讨论这个问题。"],
  ["assess","v. 评估，估价","Teachers assess students' progress through regular quizzes and final exams.","教师通过定期的测验和期末考试来评估学生的进步。"],
  ["coherent","adj. 连贯的，条理清楚的","Her essay was so coherent that readers easily followed every step of her argument.","她的文章如此连贯，读者很容易跟上她论证的每一步。"],
  ["compel","v. 强迫，迫使","The heavy rain compelled us to cancel the outdoor meeting and stay inside.","大雨迫使我们取消了户外会议，待在室内。"],
  ["comprehensive","adj. 综合的，全面的","The report offers a comprehensive analysis of the problems facing local schools.","这份报告对当地学校面临的问题进行了全面的分析。"],
  ["consecutive","adj. 连续的，不间断的","The team won three consecutive games last season.","该队上赛季连续赢了三场比赛。"],
  ["contemporary","adj. 当代的；同时代的","Many contemporary artists use digital tools to express their ideas about modern life.","许多当代艺术家使用数字工具来表达他们对现代生活的想法。"],
  ["deliberate","adj. 故意的；v. 仔细考虑","She spoke in a slow, deliberate way so everyone could follow her argument.","她说话缓慢而从容，以便每个人都能跟上她的论证。"],
  ["deteriorate","v. 恶化，变坏","His health deteriorated rapidly after he stopped taking the medicine.","停药之后，他的健康状况迅速恶化。"],
  ["diminish","v. 减少，削弱","The noise from the street gradually diminished as the night went on.","随着夜色渐深，街上的噪音逐渐减弱了。"],
  ["distinguish","v. 区分，辨别","It is often hard to distinguish between the twins because they look so similar.","人们常常很难分辨这对双胞胎，因为他们长得太像了。"],
  ["elaborate","adj. 精心制作的；v. 详尽阐述","She asked the waiter to elaborate on how the dish was prepared.","她请服务员详细说明这道菜是怎么做的。"],
  ["emphasize","v. 强调，着重","The professor emphasized the importance of reviewing notes before the final exam.","教授强调了在期末考试前复习笔记的重要性。"],
  ["fragile","adj. 脆弱的，易碎的","The old wooden bridge is so fragile that only one person can cross it at a time.","这座旧木桥非常脆弱，一次只能让一个人通过。"],
  ["gradual","adj. 逐渐的，逐步的","The patient made a gradual recovery after several weeks of rest and treatment.","经过几周的休息和治疗，病人逐渐康复了。"],
  ["impose","v. 强加；征收","The government decided to impose stricter rules on companies that pollute the environment.","政府决定对污染环境的公司实施更严格的规定。"],
  ["inevitable","adj. 不可避免的","With so many delays, it was inevitable that the project would finish late.","由于出现了这么多延误，项目推迟完成是不可避免的。"],
  ["indicate","v. 表明，指示","The sudden drop in sales indicates that customers are losing interest in this product.","销量的突然下降表明顾客正在对这款产品失去兴趣。"],
  ["maintain","v. 维持；坚持认为","It is important to maintain a healthy balance between work and rest.","保持工作与休息之间的健康平衡很重要。"],
  ["necessitate","v. 使成为必要","The heavy rain will necessitate changing the outdoor event to next week.","大雨将使得户外活动不得不改到下周举行。"],
  ["perceive","v. 察觉；认为","Many students perceive the exam as a threat rather than a challenge.","许多学生把考试视为威胁而不是挑战。"],
  ["phenomenon","n. 现象（复数 phenomena）","Scientists are still trying to explain this strange natural phenomenon.","科学家们仍在努力解释这一奇怪的自然现象。"],
  ["prominent","adj. 显著的；杰出的","She played a prominent role in the campaign to protect the old town.","她在保护古镇的那场运动中发挥了重要作用。"],
  ["reinforce","v. 加强，巩固","The new evidence only served to reinforce our belief that the plan would work.","新的证据只是进一步增强了我们对这个计划会奏效的信心。"],
  ["reluctant","adj. 不情愿的","She was reluctant to admit that her study plan had failed.","她不愿承认自己的学习计划已经失败了。"],
  ["restrain","v. 抑制，克制","Governments must restrain spending to control inflation.","政府必须抑制支出以控制通货膨胀。"],
  ["sustain","v. 维持；承受","She could not sustain her attention on the lecture for more than ten minutes.","她无法把注意力集中在讲座上超过十分钟。"],
  ["tremendous","adj. 巨大的，极大的","The new policy brought tremendous benefits to students and teachers alike.","这项新政策给学生和教师都带来了巨大的好处。"],
  ["vulnerable","adj. 易受伤害的，脆弱的","Older people living alone are often more vulnerable to illness during cold winters.","在寒冷的冬天，独居的老年人往往更容易患病。"],
];
