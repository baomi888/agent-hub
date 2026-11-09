"use client";

/**
 * 浏览器是否支持朗读。
 *
 * 为什么不用 useEffect + setState：react-hooks 的 lint 明确禁止在 effect 里同步 setState
 * （级联渲染）。而这里要回答的正是「外部能力查询」这类问题，useSyncExternalStore 的语义刚好对上：
 * 服务端拿不到 window 就返回 false，客户端 hydration 之后再取真实值，不会渲染出两套 DOM。
 */

import { useSyncExternalStore } from "react";
import { canSpeak } from "./speech";

/** 能力探测没有「变化事件」可订阅，订阅函数给空实现即可 */
const subscribe = () => () => {};

const serverSnapshot = () => false;

export function useSpeechSupport(): boolean {
  return useSyncExternalStore(subscribe, canSpeak, serverSnapshot);
}
