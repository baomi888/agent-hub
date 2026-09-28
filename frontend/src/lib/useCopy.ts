"use client";

// 复制到剪贴板的小 hook：统一「copied 状态 + 定时还原 + 失败返回 false」
// 替代 PlanCard / Bubble 里各自维护的一份 useState + setTimeout（原实现定时器不清理）
import { useCallback, useEffect, useRef, useState } from "react";

export function useCopy(resetMs = 1500) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 卸载时清定时器，避免对已卸载组件 setState
  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  /** 成功返回 true；剪贴板不可用（非 HTTPS / 无权限）返回 false */
  const copy = useCallback(
    async (text: string): Promise<boolean> => {
      try {
        await navigator.clipboard.writeText(text);
      } catch {
        return false;
      }
      setCopied(true);
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => setCopied(false), resetMs);
      return true;
    },
    [resetMs]
  );

  return { copied, copy };
}
