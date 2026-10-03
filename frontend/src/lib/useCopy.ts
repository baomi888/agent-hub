"use client";

// 复制到剪贴板的小 hook：统一「copied 状态 + 定时还原 + 失败返回 false」
// 替代 PlanCard / Bubble 里各自维护的一份 useState + setTimeout（原实现定时器不清理）
import { useCallback, useEffect, useRef, useState } from "react";

/**
 * 降级复制：临时 textarea + document.execCommand("copy")。
 *
 * 为什么需要它：`navigator.clipboard` 只在**安全上下文**（HTTPS 或 localhost）下存在。
 * 用公网 IP 走 http:// 访问时（如 http://8.163.62.25:3000）它是 undefined，
 * 直接 writeText 会抛 TypeError —— 这就是「点复制没反应」的根因。
 * execCommand 虽已废弃，但在 http 页面依然有效，是唯一能用的兜底。
 */
function legacyCopy(text: string): boolean {
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    // 别让元素真的出现在视野里：fixed + 1px + 透明，避免页面滚动跳动 / iOS 弹键盘
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "0";
    ta.style.left = "0";
    ta.style.width = "1px";
    ta.style.height = "1px";
    ta.style.padding = "0";
    ta.style.border = "none";
    ta.style.outline = "none";
    ta.style.boxShadow = "none";
    ta.style.background = "transparent";
    ta.style.opacity = "0";
    document.body.appendChild(ta);

    // iOS Safari 只认 Range + setSelectionRange，光 select() 选不中，复制会拿到空串
    const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
    if (isIOS) {
      ta.contentEditable = "true";
      ta.readOnly = false;
      const range = document.createRange();
      range.selectNodeContents(ta);
      const sel = window.getSelection();
      sel?.removeAllRanges();
      sel?.addRange(range);
      ta.setSelectionRange(0, text.length);
    } else {
      ta.focus();
      ta.select();
      ta.setSelectionRange(0, text.length);
    }

    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

/**
 * 唯一的复制入口：安全上下文走 navigator.clipboard，否则降级 execCommand。
 * 供 hook 和「自己管 copied 状态的组件」共用，避免各处再写一份实现。
 */
export async function copyText(text: string): Promise<boolean> {
  // 有现代 API 且在安全上下文里才用它；否则直接走降级，省一次注定失败的 await
  if (typeof navigator !== "undefined" && navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // 权限被拒 / 非用户手势触发，落下去走降级
    }
  }
  return legacyCopy(text);
}

export function useCopy(resetMs = 1500) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 卸载时清定时器，避免对已卸载组件 setState
  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  /** 成功返回 true；两条路都走不通返回 false */
  const copy = useCallback(
    async (text: string): Promise<boolean> => {
      const ok = await copyText(text);
      if (!ok) return false;

      setCopied(true);
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => setCopied(false), resetMs);
      return true;
    },
    [resetMs]
  );

  return { copied, copy };
}
