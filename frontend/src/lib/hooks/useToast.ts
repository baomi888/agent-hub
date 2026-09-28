"use client";

// Toast 队列：右下角轻量提示，2.8s 自动消失
import { useCallback, useState } from "react";
import { uid } from "@/lib/id";

export interface Toast {
  id: string;
  type: "success" | "error" | "info";
  msg: string;
}

export type ShowToast = (type: Toast["type"], msg: string) => void;

export function useToast() {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const showToast = useCallback<ShowToast>((type, msg) => {
    const id = uid();
    setToasts((prev) => [...prev, { id, type, msg }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 2800);
  }, []);

  return { toasts, showToast };
}
