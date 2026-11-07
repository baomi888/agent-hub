/* 设置提醒教程里的示意图（只有安卓这一套是画的）。
 *
 * iPhone 用用户真机截图裁的实拍图（public/remind/ios-*.webp），
 * 安卓这边没有拿到真机素材，按系统**真实控件位置和尺寸**画示意图顶上。
 * 数据结构里 shot / draw 是并列的，将来有截图只改 lib/reminderGuide.ts，
 * 不动这个文件。
 *
 * 约定（和 install/figs.tsx 一致）：
 *   - 系统界面一律浅色，不跟随站点暗色主题：用户拿着浅色手机看暗色示意图会对不上。
 *   - 只有金色批注用品牌色 #C8892A，其余用系统自己的颜色。
 *   - 实线 2.5px：实拍图上的金框是实线，这里用虚线会让"示意图/实拍"变成两套语汇。
 */

import type { JSX } from "react";
import type { RemindFigKey } from "@/lib/reminderGuide";

const C = {
  white: "#FFFFFF",
  ink: "#202124",
  gray: "#5F6368",
  hair: "#DADCE0",
  fill: "#F1F3F4",
  blue: "#1A73E8",
  gold: "#C8892A",
  dim: "rgba(0,0,0,0.45)",
};

const svgProps = {
  width: "100%",
  height: "100%",
  preserveAspectRatio: "xMidYMid meet",
  style: { display: "block", fontFamily: "var(--font-sans)" } as const,
};

function Ring({ x, y, w, h, r = 8 }: { x: number; y: number; w: number; h: number; r?: number }) {
  return (
    <rect
      x={x}
      y={y}
      width={w}
      height={h}
      rx={r}
      fill="none"
      stroke={C.gold}
      strokeWidth={2.5}
      strokeLinejoin="round"
    />
  );
}

/** 步骤一：日历 App 右上角「⋮」展开，找「订阅日历」那一项 */
function AndroidSubscribe(): JSX.Element {
  const items = [
    { label: "搜索", y: 96 },
    { label: "日程", y: 138 },
    { label: "订阅日历", y: 180, on: true },
    { label: "设置", y: 222 },
  ];
  return (
    <svg viewBox="0 0 360 300" {...svgProps} role="img" aria-label="安卓日历右上角菜单展开，「订阅日历」被金色方框圈出（示意图）">
      <rect x="0" y="0" width="360" height="300" fill={C.white} />

      {/* 顶栏 */}
      <text x="20" y="40" fontSize="15" fontWeight="700" fill={C.ink}>
        十一月
      </text>
      <text x="20" y="58" fontSize="11" fill={C.gray}>
        今天 · 11 月 7 日
      </text>
      {/* 右上角 ⋮ */}
      <circle cx="330" cy="38" r="14" fill={C.fill} />
      <circle cx="330" cy="31" r="1.8" fill={C.ink} />
      <circle cx="330" cy="38" r="1.8" fill={C.ink} />
      <circle cx="330" cy="45" r="1.8" fill={C.ink} />
      <path d="M0 72 L360 72" stroke={C.hair} strokeWidth="1" />

      {/* 展开的菜单卡片 */}
      <rect x="196" y="78" width="148" height="176" rx="10" fill={C.white} stroke={C.hair} />
      {items.map((it) => (
        <g key={it.label}>
          <rect
            x="200"
            y={it.y - 17}
            width="140"
            height="34"
            rx="6"
            fill={it.on ? "rgba(200,137,42,0.10)" : "transparent"}
          />
          <text x="214" y={it.y + 4} fontSize="12.5" fill={it.on ? C.ink : C.gray}>
            {it.label}
          </text>
        </g>
      ))}
      <Ring x={196} y={159} w={148} h={40} r={8} />

      {/* 说明：各品牌叫法不同 */}
      <text x="20" y="228" fontSize="11" fill={C.gray}>
        小米 / OPPO 叫「订阅日历」
      </text>
      <text x="20" y="246" fontSize="11" fill={C.gray}>
        华为叫「订阅日历」，三星在「添加日历」里
      </text>
      <text x="20" y="272" fontSize="10.5" fill={C.dim}>
        示意图：按钮位置以真机为准，名字对得上就行
      </text>
    </svg>
  );
}

/** 步骤二：粘贴链接的对话框，底部「添加」 */
function AndroidUrlDialog(): JSX.Element {
  return (
    <svg viewBox="0 0 360 240" {...svgProps} role="img" aria-label="订阅日历对话框，URL 输入框已填好链接，底部「添加」按钮被金色方框圈出（示意图）">
      <rect x="0" y="0" width="360" height="240" fill="rgba(0,0,0,0.06)" />

      <rect x="24" y="28" width="312" height="184" rx="14" fill={C.white} />
      <text x="44" y="60" fontSize="14" fontWeight="700" fill={C.ink}>
        订阅日历
      </text>

      <text x="44" y="88" fontSize="11" fill={C.gray}>
        订阅 URL
      </text>
      <rect x="44" y="96" width="272" height="36" rx="8" fill={C.fill} />
      <text x="56" y="119" fontSize="10.5" fill={C.ink}>
        https://myzhisui.cn/api/calendar/…
      </text>

      <text x="44" y="152" fontSize="10.5" fill={C.dim}>
        在设置里点「复制订阅链接」，粘到这里
      </text>

      {/* 底部按钮 */}
      <text x="120" y="192" fontSize="12.5" fill={C.gray} textAnchor="middle">
        取消
      </text>
      <text x="248" y="192" fontSize="12.5" fontWeight="700" fill={C.blue} textAnchor="middle">
        添加
      </text>
      <Ring x={216} y={172} w={64} h={30} r={8} />

      <text x="180" y="232" fontSize="10.5" fill={C.dim} textAnchor="middle">
        示意图：各家日历的对话框长得不一样，认「添加 / 订阅」两个字
      </text>
    </svg>
  );
}

export const REMIND_FIGS: Record<RemindFigKey, () => JSX.Element> = {
  "android-subscribe": AndroidSubscribe,
  "android-url-dialog": AndroidUrlDialog,
};
