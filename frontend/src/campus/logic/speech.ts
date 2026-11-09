/**
 * 浏览器自带语音。
 *
 * 为什么不用在线发音接口：板块是免登录纯前端，多一个外部接口就多一个会挂的依赖，
 * 而且用户念一个单词不该产生一次请求。
 * speechSynthesis 是浏览器内置能力，不联网；隐私模式或老浏览器没有它，
 * 调用方先问 `canSpeak()` 再决定要不要显示朗读按钮。
 */

const SPEAK_LANG = "en-US";

/** 语速 0.9：默认 1.0 念单词偏快，听不清音节边界 */
const SPEAK_RATE = 0.9;

export function canSpeak(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window;
}

/**
 * 念一个词。返回是否真的发出去了 —— 有些浏览器在没有用户手势时会静默失败，
 * 这里不抛错：念不出来是小事，不能让刷词中断。
 */
export function speakWord(text: string): boolean {
  if (!canSpeak() || !text) return false;

  try {
    const utter = new SpeechSynthesisUtterance(text);
    utter.lang = SPEAK_LANG;
    utter.rate = SPEAK_RATE;
    // 先掐掉上一句：连点两次不会两个词叠着念
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utter);
    return true;
  } catch {
    return false;
  }
}

/** 退出刷词时把还没念完的掐掉 */
export function stopSpeaking(): void {
  if (!canSpeak()) return;
  try {
    window.speechSynthesis.cancel();
  } catch {
    // 忽略
  }
}
