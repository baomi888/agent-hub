/**
 * 触发一次「保存为文件」的下载（纯前端，不经过后端）。
 *
 * 抽出来的直接原因：同样的十几行在三个地方各写了一遍 ——
 * 课表的「更多 → 导出我的课表」、导入弹窗里的「导出我的课表」、
 * 番茄钟统计面板的「导出记录」。三份实现只有 revoke 延时不一样（0ms / 1000ms），
 * 而 0ms 在部分浏览器上会在下载真正启动前就把 URL 回收掉，表现成「点了没反应」。
 * 这里统一取较稳的 1000ms。
 *
 * 返回值只回答「浏览器有没有拦下这次点击」，不判断用户最终有没有存盘 ——
 * 后者前端拿不到，别假装知道。
 */
/** 回收 objectURL 前的等待时间；1000ms 是三个入口里最稳的一档 */
const REVOKE_DELAY_MS = 1000;

export function downloadTextFile(
  fileName: string,
  text: string,
  mime = "application/json",
): boolean {
  try {
    const blob = new Blob([text], { type: mime });
    const url = URL.createObjectURL(blob);

    const link = document.createElement("a");
    link.href = url;
    link.download = fileName;
    // 必须挂到文档上再 click：Firefox 对游离节点的 click 不触发下载
    document.body.appendChild(link);
    link.click();
    link.remove();

    // 立刻 revoke 会让部分浏览器来不及取文件，等下载启动后再回收
    window.setTimeout(() => URL.revokeObjectURL(url), REVOKE_DELAY_MS);
    return true;
  } catch {
    // Blob / createObjectURL 在极老的 WebView 上可能不存在，交由调用方给文案兜底
    return false;
  }
}
