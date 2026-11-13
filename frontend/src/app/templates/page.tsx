import { redirect } from "next/navigation";

/**
 * 模板广场已下线（2026-11-13），这个位置换成了公开知识库广场（/library）。
 *
 * 为什么保留这个路由而不是直接删：老链接、PWA 里缓存过的入口、
 * 以及别人浏览器里的书签都还指着 /templates。直接删会变成 404，
 * 而"先看到一个 404、再自己找新地方"是最没必要的摩擦。
 *
 * 原来的模板广场是「挑一个场景 → 后端建好知识库 + 会话 + 提示词」。
 * 换掉的理由：用户真正缺的常常不是一套提示词，而是**可以问的资料**；
 * 站内公开知识库上线后，挑一个别人的库挂进自己的对话更直接。
 * 旧的模板接口（/api/templates）保留在仓库里但不再有入口，
 * 想找回来可以看 git 历史里的 templates/page.tsx（最后一个版本是 c640ae3）。
 */
export default function TemplatesRedirect() {
  redirect("/library");
}
