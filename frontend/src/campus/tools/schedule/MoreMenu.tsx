"use client";

/**
 * 「更多」菜单（B7 / 2.1 待勾选清单第 3 条、第 6 条）。
 *
 * 为什么要把这三项收进菜单：
 *  - **学期总览（一览）**：一学期只翻两三次，却和「按周 / 全屏」平起平坐在主按钮组里，
 *    让两个高频操作变难找。收进菜单而不是删掉 —— 学期总览本身很有用。
 *  - **清空课表**：原来在页脚，位置虽然低了，但仍是「一点就删光」的不可逆动作，
 *    和「共 N 门课」这种只读信息贴在一起。收进菜单后需要两步才能触达，误触代价降一个量级。
 *  - **导出我的课表（A08）**：只有换手机 / 换浏览器时才用一次，和「导入」不是同一频率，
 *    留在导入弹窗里够用，顺带也在这里给一个入口，免去「先点导入」的反直觉。
 *
 * 交互上只做三件事：点空白处关、按 Esc 关、点完一项就关。不做键盘方向键漫游 ——
 * 菜单只有三项，`Tab` 天然能走完，额外的漫游逻辑反而会和页面的 Esc 处理打架。
 */

import { useEffect, useRef, useState } from "react";

interface MoreMenuProps {
  /** 当前是不是「一览」视图，决定菜单第一项写「学期总览」还是「回到按周」 */
  allMode: boolean;
  /** 手机版式用胶囊外观，桌面用普通按钮 —— 两处的工具条视觉本来就不同 */
  variant?: "btn" | "pill";
  onToggleAll: () => void;
  onExport: () => void;
  onClear: () => void;
}

export default function MoreMenu({
  allMode,
  variant = "btn",
  onToggleAll,
  onExport,
  onClear,
}: MoreMenuProps) {
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;

    // 用 mousedown 而不是 click：click 要等到 mouseup，按住拖动时菜单会残留一帧
    const onPointerDown = (event: MouseEvent | TouchEvent) => {
      if (!boxRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };

    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("touchstart", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("touchstart", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  /** 所有菜单项共用：选完就收起，不用每处写两行 */
  const pick = (action: () => void) => () => {
    setOpen(false);
    action();
  };

  const triggerClass =
    variant === "pill"
      ? `campus-pill${open || allMode ? " is-on" : ""}`
      : `campus-btn${open || allMode ? " is-on" : ""}`;

  return (
    <div className="campus-more" ref={boxRef}>
      <button
        type="button"
        className={triggerClass}
        aria-haspopup="menu"
        aria-expanded={open}
        // 一览视图下按钮常亮：用户得能看出「现在看的不是周视图」，
        // 否则会以为课表坏了 —— 周视图那套日期条和网格全不见了。
        title="学期总览、导出、清空"
        onClick={() => setOpen((value) => !value)}
      >
        更多
      </button>

      {open && (
        <div className="campus-more-panel" role="menu">
          <button type="button" role="menuitem" className="campus-more-item" onClick={pick(onToggleAll)}>
            {allMode ? "回到按周" : "学期总览（一览）"}
          </button>
          <button type="button" role="menuitem" className="campus-more-item" onClick={pick(onExport)}>
            导出我的课表
          </button>
          <button
            type="button"
            role="menuitem"
            className="campus-more-item is-danger"
            onClick={pick(onClear)}
          >
            清空课表
          </button>
        </div>
      )}
    </div>
  );
}
