/**
 * 苞米风格图标集：替代全站 emoji。
 *
 * 设计语言与 MascotLogo（苞米吉祥物）完全一致：
 *  - 描边走 currentColor（外层 color = --ink），圆头圆角（round cap/join）
 *  - 玉米金 #F5B840 作主填充点缀，叶绿 #7C9A54/#8FAF62、陶橙 #EF8A56、纸白 #FFFDF6 辅助
 *  - Q 版手绘感：粗描边、大圆角、不出现直角锐利形状
 * 明暗主题自动适配（描边随主题墨色，填充为固定品牌色）。
 *
 * 用法：<CornIcon name="book" className="h-7 w-7" />
 * 未知 name（含历史残留 emoji）一律回退到 mini 苞米，保证永不显示成系统 emoji。
 */
export type CornIconName =
  | "book" // 考研复习
  | "pen" // 论文写作
  | "notes" // 课程笔记
  | "bolt" // 期末冲刺
  | "briefcase" // 求职简历
  | "globe" // 四六级/雅思
  | "target" // 社团策划
  | "search" // 文献阅读
  | "bulb" // 理念提示
  | "warn" // 警告/出错
  | "store" // 模板广场
  | "tools" // 校园工具集（扳手）
  | "corn"; // 兜底：mini 苞米

const INK_STROKE = { strokeLinecap: "round", strokeLinejoin: "round" } as const;

function Book() {
  return (
    <g {...INK_STROKE}>
      {/* 书页 */}
      <path
        d="M24 13 C20 9.5 13.5 8.5 8 10.5 V35 C13.5 33 20 34 24 37.5 C28 34 34.5 33 40 35 V10.5 C34.5 8.5 28 9.5 24 13 Z"
        fill="#FFFDF6"
        strokeWidth={3}
      />
      {/* 书脊 */}
      <path d="M24 13 V37.5" strokeWidth={2.6} />
      {/* 页内文字线 */}
      <g strokeWidth={2.2} opacity={0.32}>
        <path d="M12 18 C15 17 18 17.5 20.5 19" fill="none" />
        <path d="M27.5 19 C30 17.5 33 17 36 18" fill="none" />
        <path d="M12 24 C15 23 18 23.5 20.5 25" fill="none" />
        <path d="M27.5 25 C30 23.5 33 23 36 24" fill="none" />
      </g>
      {/* 金色书签 */}
      <path d="M30.5 9.3 V19 L34 15.8 L37.5 19 V8.6 Z" fill="#F5B840" strokeWidth={2.4} />
    </g>
  );
}

function Pen() {
  return (
    <g {...INK_STROKE}>
      {/* 笔尖（圆顶尖底，金填充） */}
      <path
        d="M24 44 C17.5 37 13.5 29 13.5 22 C13.5 14.5 18 9.5 24 9.5 C30 9.5 34.5 14.5 34.5 22 C34.5 29 30.5 37 24 44 Z"
        fill="#F5B840"
        strokeWidth={3}
      />
      {/* 开缝 */}
      <path d="M24 40 V27" strokeWidth={2.6} />
      {/* 通气孔 */}
      <circle cx="24" cy="24.5" r="2.4" fill="#FFFDF6" strokeWidth={2.2} />
      {/* 笔尖装饰线 */}
      <path
        d="M17.5 15 C19.5 13.4 21.8 12.8 24 12.8 C26.2 12.8 28.5 13.4 30.5 15"
        fill="none"
        strokeWidth={2.2}
        opacity={0.4}
      />
    </g>
  );
}

function Notes() {
  return (
    <g {...INK_STROKE}>
      {/* 纸页 */}
      <rect x="11" y="10" width="26" height="31" rx="4.5" fill="#FFFDF6" strokeWidth={3} />
      {/* 线圈 */}
      <g strokeWidth={2.4}>
        <path d="M16.5 10 V5.5" />
        <path d="M24 10 V5.5" />
        <path d="M31.5 10 V5.5" />
      </g>
      {/* 笔记行：首行金色强调 */}
      <path d="M16.5 21 H31.5" stroke="#F5B840" strokeWidth={3} />
      <g strokeWidth={2.2} opacity={0.32}>
        <path d="M16.5 27 H31.5" />
        <path d="M16.5 33 H26.5" />
      </g>
    </g>
  );
}

function Bolt() {
  return (
    <g {...INK_STROKE}>
      <path d="M26.5 4.5 L12.5 26.5 H21.5 L19.5 43.5 L35.5 19.5 H26 Z" fill="#F5B840" strokeWidth={3} />
    </g>
  );
}

function Briefcase() {
  return (
    <g {...INK_STROKE}>
      {/* 提手 */}
      <path
        d="M18.5 16.5 V12.5 C18.5 10.8 19.8 9.5 21.5 9.5 H26.5 C28.2 9.5 29.5 10.8 29.5 12.5 V16.5"
        fill="none"
        strokeWidth={2.8}
      />
      {/* 箱体 */}
      <rect x="8" y="16.5" width="32" height="22.5" rx="4.5" fill="#F5B840" strokeWidth={3} />
      {/* 锁扣 */}
      <rect x="21" y="24" width="6" height="5.5" rx="1.8" fill="#FFFDF6" strokeWidth={2.2} />
    </g>
  );
}

function Globe() {
  return (
    <g {...INK_STROKE}>
      <circle cx="24" cy="24" r="16" fill="#FFFDF6" strokeWidth={3} />
      {/* 经线 */}
      <ellipse cx="24" cy="24" rx="7" ry="16" fill="none" strokeWidth={2.2} opacity={0.45} />
      {/* 纬线 */}
      <path d="M9.5 19 C14 17.2 34 17.2 38.5 19" fill="none" strokeWidth={2.2} opacity={0.45} />
      <path d="M9.5 29 C14 30.8 34 30.8 38.5 29" fill="none" strokeWidth={2.2} opacity={0.45} />
      {/* 赤道：金色 */}
      <path d="M8.4 24 H39.6" stroke="#F5B840" strokeWidth={2.8} />
    </g>
  );
}

function Target() {
  return (
    <g {...INK_STROKE}>
      <circle cx="24" cy="24" r="15.5" fill="#F5B840" strokeWidth={3} />
      <circle cx="24" cy="24" r="9.3" fill="#FFFDF6" strokeWidth={2.6} />
      <circle cx="24" cy="24" r="3.8" fill="#EF8A56" strokeWidth={2.2} />
    </g>
  );
}

function Search() {
  return (
    <g {...INK_STROKE}>
      <circle cx="21" cy="21" r="12.5" fill="#FFFDF6" strokeWidth={3} />
      {/* 镜片金高光 */}
      <path d="M15.5 18.5 C16.6 16 19 14.3 21.5 14.2" stroke="#F5B840" strokeWidth={2.8} fill="none" />
      {/* 镜内文字线 */}
      <path d="M16 24 H26" strokeWidth={2.2} opacity={0.32} />
      {/* 手柄 */}
      <path d="M30.5 30.5 L40.5 40.5" strokeWidth={3.6} />
    </g>
  );
}

function Bulb() {
  return (
    <g {...INK_STROKE}>
      {/* 光芒 */}
      <g strokeWidth={2.4} opacity={0.55}>
        <path d="M24 2.5 V5" />
        <path d="M10.5 8 L12.5 10.2" />
        <path d="M37.5 8 L35.5 10.2" />
      </g>
      {/* 灯泡 */}
      <path
        d="M24 7 C17 7 12.5 12.2 12.5 18.4 C12.5 22.6 15 25.4 16.9 27.8 C18.1 29.3 18.8 30.6 18.8 32.5 H29.2 C29.2 30.6 29.9 29.3 31.1 27.8 C33 25.4 35.5 22.6 35.5 18.4 C35.5 12.2 31 7 24 7 Z"
        fill="#F5B840"
        strokeWidth={3}
      />
      {/* 灯丝 */}
      <path d="M21 22 C21 19 22.3 17.5 24 17.5 C25.7 17.5 27 19 27 22" fill="none" strokeWidth={2.2} opacity={0.5} />
      {/* 灯座 */}
      <g strokeWidth={2.6}>
        <path d="M19.5 36 H28.5" />
        <path d="M20.5 40 H27.5" />
      </g>
    </g>
  );
}

function Store() {
  return (
    <g {...INK_STROKE}>
      {/* 店体 */}
      <path d="M11.5 21 V37.5 C11.5 39.4 13 41 15 41 H33 C35 41 36.5 39.4 36.5 37.5 V21" fill="#FFFDF6" strokeWidth={3} />
      {/* 门 */}
      <path d="M20 41 V31 C20 29.3 21.3 28 23 28 H25 C26.7 28 28 29.3 28 31 V41" fill="none" strokeWidth={2.4} />
      {/* 窗 */}
      <path d="M15.5 25.5 H18.5" strokeWidth={2.2} opacity={0.35} />
      <path d="M29.5 25.5 H32.5" strokeWidth={2.2} opacity={0.35} />
      {/* 金色雨棚 + 扇贝边 */}
      <path
        d="M9.5 14.5 C9.5 11 12 8.5 15.5 8.5 H32.5 C36 8.5 38.5 11 38.5 14.5 V16.5 Q34.75 20.5 31 16.5 Q27.25 20.5 23.5 16.5 Q19.75 20.5 16 16.5 Q12.25 20.5 9.5 16.5 Z"
        fill="#F5B840"
        strokeWidth={2.8}
      />
    </g>
  );
}

/** 校园工具集：扳手（Q 版圆角手绘，金填充） */
function Tools() {
  return (
    <g {...INK_STROKE}>
      {/* 扳手头 */}
      <path
        d="M33.5 12.5a6 6 0 0 0-7.9 7.1L11.5 33.8a3.6 3.6 0 0 0 5.1 5.1l13.9-14.1a6 6 0 0 0 7.1-7.9l-3.4 3.4-2.9-2.9 3.4-3.4Z"
        fill="#F5B840"
        strokeWidth={3}
      />
      {/* 开口高光 */}
      <path d="M29 16.5 C30.2 14.6 32.8 14.4 34.4 16 C36 17.6 36.2 20.2 34.4 21.4" fill="none" strokeWidth={2.2} opacity={0.45} />
    </g>
  );
}

/** 兜底：mini 苞米（复用吉祥物造型语言） */
function Corn() {
  return (
    <g {...INK_STROKE}>
      {/* 玉米须 */}
      <path d="M21 10.5 C21 7.5 22.3 5.5 24 4.5 M24.5 10.5 C24.7 8 25.8 6.3 27.5 5.5" fill="none" strokeWidth={2.2} />
      {/* 叶子 */}
      <path d="M18.5 27 C13 28.5 9.5 33 8.5 38.5 C14 37.5 17.8 34 19 29.5 Z" fill="#7C9A54" strokeWidth={2.4} />
      <path d="M29.5 27 C35 28.5 38.5 33 39.5 38.5 C34 37.5 30.2 34 29 29.5 Z" fill="#8FAF62" strokeWidth={2.4} />
      {/* 玉米身 */}
      <rect x="18" y="11" width="12" height="21" rx="6" fill="#F5B840" strokeWidth={2.8} />
      {/* 玉米粒纹理 */}
      <g fill="none" strokeWidth={1.8} opacity={0.3}>
        <path d="M21.8 13.5 C21 19 21 25 21.8 29.5" />
        <path d="M26.2 13.5 C27 19 27 25 26.2 29.5" />
      </g>
    </g>
  );
}

/** 警告：圆角三角 + 感叹号（替代 ⚠ 字符） */
function Warn() {
  return (
    <g {...INK_STROKE}>
      <path d="M24 7.5 L42.5 38.5 C43.4 40 42.3 42 40.5 42 H7.5 C5.7 42 4.6 40 5.5 38.5 Z" fill="#F5B840" strokeWidth={3} />
      <path d="M24 18.5 V27.5" strokeWidth={2.8} />
      <circle cx="24" cy="33.5" r="1.8" fill="currentColor" stroke="none" />
    </g>
  );
}

const ICONS: Record<string, () => React.JSX.Element> = {
  book: Book,
  pen: Pen,
  notes: Notes,
  bolt: Bolt,
  briefcase: Briefcase,
  globe: Globe,
  target: Target,
  search: Search,
  bulb: Bulb,
  warn: Warn,
  store: Store,
  tools: Tools,
  corn: Corn,
};

type Props = {
  name: string;
  className?: string;
  /** 无障碍标签；不传则视为装饰性图标 */
  title?: string;
};

export default function CornIcon({ name, className = "h-6 w-6", title }: Props) {
  const Draw = ICONS[name] ?? Corn;
  return (
    <svg
      viewBox="0 0 48 48"
      className={className}
      width="100%"
      height="100%"
      style={{ display: "block", overflow: "visible" }}
      role={title ? "img" : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      fill="none"
      stroke="currentColor"
    >
      <Draw />
    </svg>
  );
}
