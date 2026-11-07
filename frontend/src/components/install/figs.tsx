/* 安装教程的示意图（只剩安卓这一套）。
 *
 * 为什么只有安卓是画的：iOS 和电脑都用用户提供的真机截图 / 录屏裁成了实拍图
 * （裁剪与金框脚本留在本机 .workbuddy/pp/make_shots.py，不随仓库分发；金框是烘进图片的）。安卓没有拿到真机素材，
 * 先用按系统**真实控件位置和尺寸**画的示意图顶上，等有截图再换——数据结构里
 * shot / draw 两种是并列的，换的时候只改 lib/installGuide.ts，不动这个文件。
 *
 * 约定：
 *   - 系统界面一律保持浅色（真机默认就是这样），不跟随站点暗色主题；
 *     否则用户拿着浅色手机看暗色示意图，反而对不上。
 *   - 只有金色批注用品牌色 #C8892A，其余用各系统自己的颜色。
 *   - 手机图统一 viewBox 宽 360，由 CSS 等比缩放。
 */

import type { JSX } from "react";
import type { FigKey } from "@/lib/installGuide";

const C = {
  white: "#FFFFFF",
  // 安卓 / Chrome 系统色
  gInk: "#202124",
  gGray: "#5F6368",
  gHair: "#DADCE0",
  gFill: "#F1F3F4",
  gBlue: "#1A73E8",
  // 品牌
  gold: "#C8892A",
  paper: "#EDE4C8",
  corn: "#F5B840",
  cornDark: "#3D2F1A",
  leaf: "#7C9A54",
  dim: "rgba(0,0,0,0.42)",
};

const svgProps = {
  width: "100%",
  height: "100%",
  preserveAspectRatio: "xMidYMid meet",
  style: { display: "block", fontFamily: "var(--font-sans)" } as const,
};

/** 金色批注环：标出"这一步要点这里" —— 全站唯一允许出现在系统界面上的颜色。
 *  ★ 实线、2px：真机截图上的金框是用 PIL 画的白底双描边实线（make_shots.py），
 *  这里如果画虚线，同一页里就会出现"虚线=示意图 / 实线=实拍"两套语汇，
 *  读者会以为虚线框是别的东西（评审第一条就点了这个）。 */
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

/** 玉米小人：站点图标 / 桌面图标都用它，尺寸跟着 s 走 */
function Corn({ cx, cy, s }: { cx: number; cy: number; s: number }) {
  const h = s;
  const w = s * 0.62;
  return (
    <g>
      {/* 两片叶子 */}
      <path
        d={`M${cx - w * 0.5} ${cy + h * 0.1} C${cx - w * 1.1} ${cy + h * 0.15} ${cx - w * 1.25} ${cy + h * 0.5} ${cx - w * 1.1} ${cy + h * 0.62} C${cx - w * 0.75} ${cy + h * 0.55} ${cx - w * 0.55} ${cy + h * 0.32} ${cx - w * 0.5} ${cy + h * 0.1} Z`}
        fill={C.leaf}
        stroke={C.cornDark}
        strokeWidth={Math.max(1, s * 0.05)}
      />
      <path
        d={`M${cx + w * 0.5} ${cy + h * 0.1} C${cx + w * 1.1} ${cy + h * 0.15} ${cx + w * 1.25} ${cy + h * 0.5} ${cx + w * 1.1} ${cy + h * 0.62} C${cx + w * 0.75} ${cy + h * 0.55} ${cx + w * 0.55} ${cy + h * 0.32} ${cx + w * 0.5} ${cy + h * 0.1} Z`}
        fill="#8FAF62"
        stroke={C.cornDark}
        strokeWidth={Math.max(1, s * 0.05)}
      />
      {/* 玉米棒 */}
      <rect
        x={cx - w / 2}
        y={cy - h / 2}
        width={w}
        height={h * 0.92}
        rx={w / 2}
        fill={C.corn}
        stroke={C.cornDark}
        strokeWidth={Math.max(1, s * 0.055)}
      />
      {/* 两三道横纹就够了，多了在 16px 下糊成一团 */}
      <path
        d={`M${cx - w * 0.3} ${cy - h * 0.29} h${w * 0.6} M${cx - w * 0.32} ${cy - h * 0.15} h${w * 0.64} M${cx - w * 0.3} ${cy - h * 0.01} h${w * 0.6}`}
        stroke="rgba(61,47,26,0.35)"
        strokeWidth={Math.max(0.8, s * 0.035)}
        strokeLinecap="round"
        fill="none"
      />
    </g>
  );
}

/** 桌面 / 主屏上的方角圆图标（带米黄底） */
function AppIcon({ x, y, s }: { x: number; y: number; s: number }) {
  return (
    <g>
      <rect x={x} y={y} width={s} height={s} rx={s * 0.24} fill={C.paper} stroke="rgba(61,47,26,0.18)" />
      <Corn cx={x + s / 2} cy={y + s / 2} s={s * 0.62} />
    </g>
  );
}


/* ========================== 安卓 ========================== */

function GStatus() {
  return (
    <g>
      <text x={16} y={18} fontSize={11.5} fontWeight={600} fill={C.gInk}>
        9:41
      </text>
      <g fill={C.gInk} opacity={0.75}>
        <rect x={296} y={10} width={2.5} height={5} rx={1} />
        <rect x={300.5} y={7.5} width={2.5} height={7.5} rx={1} />
        <rect x={305} y={5} width={2.5} height={10} rx={1} />
      </g>
      <path d="M314 14a9 9 0 0 1 11 0" stroke={C.gInk} strokeWidth={1.6} fill="none" strokeLinecap="round" opacity={0.75} />
      <rect x={330} y={9} width={16} height={9} rx={2.5} fill={C.gInk} opacity={0.75} />
      <rect x={347} y={11.5} width={1.6} height={4} rx={0.8} fill={C.gInk} opacity={0.5} />
    </g>
  );
}

function AndroidMenu() {
  return (
    <svg viewBox="0 0 360 300" {...svgProps}>
      <rect width="360" height="300" fill={C.white} />
      <rect y="26" width="360" height="274" fill={C.paper} />
      <Corn cx={180} cy={92} s={36} />
      <text x={180} y={132} textAnchor="middle" fontSize={14} fontWeight={600} fill={C.cornDark}>
        知穗 Agent
      </text>
      <rect x="28" y="150" width="304" height="26" rx="9" fill="rgba(255,253,246,0.7)" />
      <rect x="28" y="184" width="304" height="26" rx="9" fill="rgba(255,253,246,0.5)" />

      <GStatus />
      {/* Chrome 顶栏：网址胶囊 + ⋮ */}
      <rect x="8" y="32" width="300" height="34" rx="17" fill={C.gFill} />
      <g stroke={C.gGray} strokeWidth={1.5} fill="none" strokeLinecap="round">
        <path d="M24 53v-6a2.5 2.5 0 0 1 5 0v6" />
        <rect x={23} y={47} width={11} height={8} rx={1.5} fill={C.gFill} />
      </g>
      <text x={44} y={54} fontSize={12} fill={C.gInk}>
        myzhisui.cn
      </text>
      <g fill={C.gGray}>
        <circle cx={328} cy={44} r={2} />
        <circle cx={328} cy={50} r={2} />
        <circle cx={328} cy={56} r={2} />
      </g>

      {/* 下拉菜单 */}
      <g>
        <rect x="204" y="74" width="148" height="190" rx="8" fill={C.white} stroke="rgba(0,0,0,0.08)" />
        {[
          "新建标签页",
          "新建无痕标签页",
          "书签",
          "下载",
          "安装应用",
          "设置",
        ].map((t, i) => {
          const y = 74 + i * 30;
          const on = t === "安装应用";
          return (
            <g key={t}>
              {on && <rect x="206" y={y + 3} width="144" height="24" rx="6" fill="#E8F0FE" />}
              {on && (
                <g stroke={C.gBlue} strokeWidth={1.6} fill="none" strokeLinecap="round">
                  <path d={`M216 ${y + 15}v5a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2v-5`} />
                  <path d={`M222 ${y + 18}V${y + 6}`} />
                  <path d={`M219 ${y + 9}l3-3l3 3`} />
                </g>
              )}
              <text
                x={on ? 234 : 216}
                y={y + 20}
                fontSize={11.5}
                fontWeight={on ? 600 : 400}
                fill={on ? C.gBlue : C.gInk}
              >
                {t}
              </text>
            </g>
          );
        })}
      </g>
      <Ring x={208} y={197} w={140} h={26} r={7} />
    </svg>
  );
}

function AndroidInstall() {
  return (
    <svg viewBox="0 0 360 200" {...svgProps}>
      <rect width="360" height="200" fill={C.paper} />
      <rect width="360" height="200" fill={C.dim} />
      <rect x="0" y="92" width="360" height="108" rx="16" fill={C.white} />
      <rect x="168" y="100" width="24" height="4" rx="2" fill={C.gHair} />
      <AppIcon x={20} y={112} s={44} />
      <text x="76" y="132" fontSize={14} fontWeight={600} fill={C.gInk}>
        安装应用
      </text>
      <text x="76" y="150" fontSize={11} fill={C.gGray}>
        myzhisui.cn
      </text>
      <text x="238" y="181" textAnchor="middle" fontSize={12.5} fill={C.gGray}>
        取消
      </text>
      <rect x="272" y="160" width="68" height="32" rx="16" fill={C.gBlue} />
      <text x="306" y="181" textAnchor="middle" fontSize={12.5} fontWeight={600} fill={C.white}>
        安装
      </text>
      <Ring x={268} y={156} w={76} h={40} r={20} />
    </svg>
  );
}

function AndroidHome() {
  type HomeApp = { x: number; label: string; kind: string; row?: number };
  // 只画"一眼认得出"的简单外形：小尺寸下细节全糊，写意的图标反而更像系统图标
  const apps: HomeApp[] = [
    { x: 40, label: "电话", kind: "phone" },
    { x: 124, label: "信息", kind: "msg" },
    { x: 208, label: "相机", kind: "cam" },
    { x: 292, label: "时钟", kind: "clock" },
    { x: 40, label: "浏览器", kind: "globe", row: 2 },
    { x: 124, label: "相册", kind: "photo", row: 2 },
    { x: 208, label: "音乐", kind: "music", row: 2 },
    { x: 292, label: "知穗Agent", kind: "corn", row: 2 },
  ];
  return (
    <svg viewBox="0 0 360 260" {...svgProps}>
      <defs>
        <linearGradient id="igWall" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#3B3226" />
          <stop offset="100%" stopColor="#6B5942" />
        </linearGradient>
      </defs>
      <rect width="360" height="260" fill="url(#igWall)" />
      <text x="28" y="66" fontSize={34} fontWeight={300} fill="rgba(255,253,246,0.94)">
        9:41
      </text>
      <text x="30" y="86" fontSize={10.5} fill="rgba(255,253,246,0.62)">
        11月7日 星期六
      </text>

      {apps.map((a) => {
        const y = a.row === 2 ? 186 : 110;
        const isApp = a.kind === "corn";
        return (
          <g key={a.label}>
            {isApp ? (
              <AppIcon x={a.x} y={y} s={44} />
            ) : (
              <g>
                <rect
                  x={a.x}
                  y={y}
                  width={44}
                  height={44}
                  rx={12}
                  fill="rgba(255,253,246,0.16)"
                  stroke="rgba(255,253,246,0.22)"
                />
                <g stroke="rgba(255,253,246,0.82)" strokeWidth={1.7} fill="none" strokeLinecap="round" strokeLinejoin="round">
                  {a.kind === "phone" && (
                    <>
                      <rect x={a.x + 16} y={y + 12} width={12} height={20} rx={3.5} />
                      <path d={`M${a.x + 20.5} ${y + 15.5}h3`} />
                    </>
                  )}
                  {a.kind === "msg" && (
                    <path
                      d={`M${a.x + 13} ${y + 15}h18a3 3 0 0 1 3 3v9a3 3 0 0 1-3 3h-10l-6 5v-5h-2a3 3 0 0 1-3-3v-9a3 3 0 0 1 3-3z`}
                    />
                  )}
                  {a.kind === "cam" && (
                    <>
                      <rect x={a.x + 11} y={y + 16} width={22} height={15} rx={3.5} />
                      <path d={`M${a.x + 18} ${y + 16}l2-3h4l2 3`} />
                      <circle cx={a.x + 22} cy={y + 23.5} r={4.2} />
                    </>
                  )}
                  {a.kind === "clock" && (
                    <>
                      <circle cx={a.x + 22} cy={y + 22} r={9.5} />
                      <path d={`M${a.x + 22} ${y + 16.5}V${y + 22}l4 2.5`} />
                    </>
                  )}
                  {a.kind === "globe" && (
                    <>
                      <circle cx={a.x + 22} cy={y + 22} r={9.5} />
                      <path d={`M${a.x + 12.5} ${y + 22}h19`} />
                      <path d={`M${a.x + 22} ${y + 12.5}c5.5 6 5.5 13 0 19c-5.5-6-5.5-13 0-19z`} />
                    </>
                  )}
                  {a.kind === "photo" && (
                    <>
                      <rect x={a.x + 12} y={y + 15} width={20} height={16} rx={3} />
                      <path d={`M${a.x + 13} ${y + 28}l5-5 3 3 3.5-4.5 4.5 6.5`} />
                      <circle cx={a.x + 18} cy={y + 19.5} r={1.4} fill="rgba(255,253,246,0.82)" />
                    </>
                  )}
                  {a.kind === "music" && (
                    <>
                      <path d={`M${a.x + 26} ${y + 15}v12`} />
                      <path d={`M${a.x + 26} ${y + 15}l5 2.5`} />
                      <ellipse cx={a.x + 22.5} cy={y + 28} rx={4} ry={3.2} />
                    </>
                  )}
                </g>
              </g>
            )}
            <text
              x={a.x + 22}
              y={y + 60}
              textAnchor="middle"
              fontSize={9.5}
              fontWeight={isApp ? 600 : 400}
              fill={isApp ? "#FFE9BE" : "rgba(255,253,246,0.8)"}
            >
              {a.label}
            </text>
            {isApp && <Ring x={a.x - 4} y={y - 4} w={52} h={52} r={15} />}
          </g>
        );
      })}
    </svg>
  );
}

export const INSTALL_FIGS: Record<FigKey, () => JSX.Element> = {
  "android-menu": AndroidMenu,
  "android-install": AndroidInstall,
  "android-home": AndroidHome,
};
