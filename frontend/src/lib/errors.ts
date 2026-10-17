// 后端错误体的统一降级：把 `detail` 归一成一句人能读的话。
//
// 为什么单独一个模块：api.ts（普通请求）与 sse.ts（流式请求）都要用同一套规则。
// 两份各写一份必然漂移，所以抽出来共用。

/**
 * 把后端错误体里的 `detail` 归一成一句人能读的话。
 *
 * 为什么必须做：FastAPI 的 **422 校验失败**返回的 `detail` 是一个**数组**
 * （`[{loc:["body","deliver_sid"], msg:"Field required", ...}]`），而 429/业务
 * 错误返回的 `detail` 是字符串。若直接把 `body.detail` 丢给 `new Error()`,
 * JS 会把数组/对象转成字符串 "[object Object]" —— 用户看到的报错就是这个，
 * 既看不出错在哪个字段，也看不出是校验失败。契约漂移、少传字段、越界……
 * 只要撞上 422，界面上就是一坨 "[object Object]"。
 *
 * 所以这里统一：字符串原样用；数组按 `字段: 原因` 拼装；其余对象退化成 JSON；
 * 都没有则用 fallback（通常是 `HTTP <status>`）。
 */
export function detailToMessage(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail.trim() || fallback;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        if (!item || typeof item !== "object") return typeof item === "string" ? item : "";
        const rec = item as { loc?: unknown; msg?: unknown; type?: unknown };
        // loc 形如 ["body", "deliver_sid"]：去掉 body 这一层，只留用户认识的字段名
        const loc = Array.isArray(rec.loc)
          ? rec.loc.filter((seg) => seg !== "body").join(".")
          : "";
        const msg =
          typeof rec.msg === "string" ? rec.msg : typeof rec.type === "string" ? rec.type : "";
        return loc ? `${loc}: ${msg}` : msg;
      })
      .filter(Boolean);
    if (parts.length > 0) return `请求参数有误：${parts.join("；")}`;
    // 空数组 / 解析不出字段：别把 "[]" 丢给用户，退回 fallback
    return fallback;
  }
  if (detail && typeof detail === "object") {
    try {
      return JSON.stringify(detail);
    } catch {
      /* 循环引用等极端情况：退回 fallback */
    }
  }
  return fallback;
}
