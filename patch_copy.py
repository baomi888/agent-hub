# -*- coding: utf-8 -*-
"""修复「点复制没反应」——仅改前端两个文件，打完必须重新构建前端。

根因：浏览器只在**安全上下文**（HTTPS 或 localhost）暴露 navigator.clipboard。
用公网 IP 走 http:// 访问时它是 undefined，writeText 直接抛 TypeError，
所以按钮点了毫无反应。这与后端、网络、安全组无关。

修法（两处，共用一份实现）：
  1. frontend/src/lib/useCopy.ts
     抽出 copyText() 作为唯一复制入口：安全上下文用 navigator.clipboard，
     否则降级到已废弃但 http 下依然有效的 document.execCommand("copy")
     （临时 textarea + iOS 的 Range 选中处理）。
  2. frontend/src/components/FilePreviewModal.tsx
     这处原本自己直接调 navigator.clipboard，没走 useCopy，属于第二处漏网；
     改为调用共用的 copyText()。

幂等：内容已一致就跳过。改前自动备份为 <文件名>.bak-<时间戳>。

用法：
    python3 patch_copy.py                 # 自动定位项目根目录
    python3 patch_copy.py /root           # 显式指定

打完**必须**重新构建前端（改的是 .ts/.tsx，是编译产物，restart 不会重建）：
    bash deploy.sh deploy
"""

import os
import shutil
import sys
import time

FILES = [
    ('frontend/src/lib/useCopy.ts',
     r'''"use client";

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
'''),
    ('frontend/src/components/FilePreviewModal.tsx',
     r'''"use client";

// 库内文件正文预览弹层。
// 用 portal 挂到 body：≤1279px 时右栏是带 transform 的抽屉，
// transform 会成为 fixed 定位的包含块，弹层不 portal 就会被裁进 300px 的抽屉里。
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { api } from "@/lib/api";
import type { FilePreview } from "@/lib/types";
import { copyText } from "@/lib/useCopy";

interface Props {
  kbId: string;
  /** 后端记录里的原始文件名（老库可能是完整临时路径，展示时只取文件名） */
  name: string;
  /** 该文件在库里的片段数，展示用 */
  chunks?: number | null;
  onClose: () => void;
}

/** 老库入库时把临时绝对路径写进了 source，展示只取最后一段 */
export function shortName(name: string): string {
  const s = (name || "").replace(/\\/g, "/");
  const seg = s.split("/").filter(Boolean).pop();
  return seg || name;
}

type PreviewState =
  | { key: string; status: "loading" }
  | { key: string; status: "ok"; data: FilePreview }
  | { key: string; status: "err"; error: string };

// 只在客户端 portal：SSR 阶段 document 不存在，用 getServerSnapshot 给出 false 避免水合不一致
const subscribe = () => () => {};
const mountedSnapshot = () => true;
const serverSnapshot = () => false;

export default function FilePreviewModal({ kbId, name, chunks, onClose }: Props) {
  const reqKey = `${kbId}::${name}`;
  // 请求结果跟「请求哪个文件」绑在一起存：换文件时在 render 阶段直接重置，
  // 不用在 effect 里同步 setState（会触发 react-hooks/set-state-in-effect）
  const [state, setState] = useState<PreviewState>({ key: reqKey, status: "loading" });
  if (state.key !== reqKey) setState({ key: reqKey, status: "loading" });

  const [copied, setCopied] = useState(false);
  // 复制失败单独存，不覆盖正文：正文还在就照常显示
  const [copyError, setCopyError] = useState<string | null>(null);
  const mounted = useSyncExternalStore(subscribe, mountedSnapshot, serverSnapshot);
  const cardRef = useRef<HTMLDivElement>(null);
  // 用惰性初始值在首次 render 阶段抓触发元素：autoFocus 会在 commit 阶段抢走焦点，
  // 等到 effect 里再读 activeElement，拿到的已经是弹层自己的关闭按钮了
  const [opener] = useState<Element | null>(() =>
    typeof document === "undefined" ? null : document.activeElement
  );

  // 关闭时把焦点还回去，否则键盘用户会掉回页面开头
  useEffect(
    () => () => {
      if (opener instanceof HTMLElement) opener.focus?.();
    },
    [opener]
  );

  useEffect(() => {
    let alive = true;
    api.getFileContent(kbId, name).then(
      (data) => alive && setState({ key: reqKey, status: "ok", data }),
      (e) =>
        alive &&
        setState({
          key: reqKey,
          status: "err",
          error: e instanceof Error ? e.message : "读取失败",
        })
    );
    return () => {
      alive = false;
    };
  }, [kbId, name, reqKey]);

  // Esc 关闭 + Tab 锁在弹层内
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== "Tab" || !cardRef.current) return;
      const nodes = cardRef.current.querySelectorAll<HTMLElement>(
        'button, [href], input, textarea, [tabindex]:not([tabindex="-1"])'
      );
      if (nodes.length === 0) return;
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  const doCopy = useCallback(async () => {
    if (state.status !== "ok") return;
    // 走 useCopy 里那份共用实现：公网 http 下 navigator.clipboard 不存在，
    // 内部会自动降级到 execCommand（安全上下文下才用现代 API）
    const ok = await copyText(state.data.content);
    if (!ok) {
      setCopyError("复制失败：当前环境不允许写入剪贴板，请手动选中文本复制");
      return;
    }
    setCopyError(null);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  }, [state]);

  if (!mounted) return null;

  const title = shortName(name);
  const isPdf = /\.pdf$/i.test(title);
  const data = state.status === "ok" ? state.data : null;
  const error = state.status === "err" ? state.error : null;

  return createPortal(
    <div
      className="pv-mask"
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={cardRef}
        className="pv-card"
        role="dialog"
        aria-modal="true"
        aria-label={`预览文件：${title}`}
      >
        <header className="pv-head">
          <div className="min-w-0">
            <h2 className="pv-title" title={name}>
              {title}
            </h2>
            <p className="pv-meta">
              <span className="pv-badge">
                {data ? (data.origin === "archive" ? "原文" : "片段拼回") : "读取中"}
              </span>
              {chunks != null && <span>{chunks} 片段</span>}
              {data && <span>{data.content.length.toLocaleString()} 字</span>}
            </p>
          </div>
          <button
            autoFocus
            onClick={onClose}
            title="关闭预览（Esc）"
            aria-label="关闭预览"
            className="pv-close"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </header>

        {error ? (
          <div className="pv-body">
            <p className="pv-error">{error}</p>
          </div>
        ) : !data ? (
          <div className="pv-body" aria-busy="true">
            <span className="skel-line" />
            <span className="skel-line" />
            <span className="skel-line short" />
          </div>
        ) : (
          <div className="pv-body">
            {copyError && <p className="pv-error">{copyError}</p>}
            {data.content.trim() ? (
              <>
                {data.origin === "chunks" && (
                  <p className="pv-note">
                    这个文件入库较早，原始文件没有留档，以下是它在库里的片段按顺序拼回的内容
                    {isPdf ? "（PDF 文字层）" : ""}。
                  </p>
                )}
                {data.origin === "archive" && isPdf && (
                  <p className="pv-note">PDF 预览显示的是文字层内容，不含版式与图片。</p>
                )}
                <pre className="pv-text">{data.content}</pre>
                {data.truncated && (
                  <p className="pv-note">
                    内容较长，预览只截取前 {data.content.length.toLocaleString()} 字。
                  </p>
                )}
              </>
            ) : (
              <p className="pv-empty">没能读到这个文件的正文（可能是扫描版 PDF 或空文件）。</p>
            )}
          </div>
        )}

        <footer className="pv-foot">
          <button
            onClick={doCopy}
            disabled={!data || !data.content}
            className="pv-btn"
            title="复制预览到的正文"
          >
            {copied ? "已复制" : "复制正文"}
          </button>
          <button onClick={onClose} className="pv-btn primary">
            关闭
          </button>
        </footer>
      </div>
    </div>,
    document.body
  );
}
''')
]

# 每个文件打完必须出现的标记，用来兜底确认没写坏
MARKERS = {
    "frontend/src/lib/useCopy.ts": ["export async function copyText", "legacyCopy"],
    "frontend/src/components/FilePreviewModal.tsx": [
        "copyText",
        "from \"@/lib/useCopy\"",
    ],
}

# 旧实现的特征：出现了说明这个文件没被补丁覆盖到
STALE = {
    "frontend/src/lib/useCopy.ts": [],
    "frontend/src/components/FilePreviewModal.tsx": ["navigator.clipboard.writeText"],
}

CANDIDATE_ROOTS = ["/root", "/root/baomiagent", "/home/baomiagent", "/opt/baomiagent"]


def looks_like_project(d):
    return os.path.isfile(os.path.join(d, "frontend", "src", "lib", "useCopy.ts"))


def find_root():
    for d in CANDIDATE_ROOTS:
        if looks_like_project(d):
            return d
    cwd = os.getcwd()
    if looks_like_project(cwd):
        return cwd
    # 最后兜底：从 CWD 向上找 5 层
    d = cwd
    for _ in range(5):
        d = os.path.dirname(d)
        if not d or d == os.path.sep:
            break
        if looks_like_project(d):
            return d
    return None


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else None
    if root:
        if not looks_like_project(root):
            print("错误：%s 下找不到 frontend/src/lib/useCopy.ts，不像项目根目录" % root)
            return 1
    else:
        root = find_root()

    if not root:
        print("错误：自动定位失败。请显式指定项目根目录：")
        print("      python3 patch_copy.py /root")
        return 1

    print("项目根目录：%s" % root)
    print("-" * 60)

    changed, skipped = [], []

    for rel, body in FILES:
        p = os.path.join(root, rel)
        existed = os.path.isfile(p)

        if existed:
            with open(p, encoding="utf-8") as f:
                cur = f.read()
            if cur == body:
                skipped.append(rel + "（内容已一致，跳过）")
                continue
            bak = p + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
            shutil.copy2(p, bak)
            changed.append("%s（备份 %s）" % (rel, os.path.basename(bak)))
        else:
            changed.append(rel + "（新建）")

        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(body)

    for line in changed:
        print("  已写入  " + line)
    for line in skipped:
        print("  跳过    " + line)

    print("-" * 60)

    # 复核：标记在、旧特征不在
    bad = []
    for rel, _body in FILES:
        p = os.path.join(root, rel)
        if not os.path.isfile(p):
            bad.append(rel + " 不存在")
            continue
        with open(p, encoding="utf-8") as f:
            cur = f.read()
        for m in MARKERS.get(rel, []):
            if m not in cur:
                bad.append("%s 缺少标记 %r" % (rel, m))
        for s in STALE.get(rel, []):
            if s in cur:
                bad.append("%s 仍残留旧写法 %r" % (rel, s))

    if bad:
        print("复核未通过：")
        for b in bad:
            print("  - " + b)
        return 1

    print("复核通过：两份文件标记齐全、无旧写法残留")
    print()
    if changed:
        print("下一步（必须，否则改动不生效）：")
        print("    cd %s && bash deploy.sh deploy" % root)
        print()
        print("注意：是 deploy，不是 restart。restart 带 SKIP_BUILD=1，")
        print("      .next 已存在就跳过构建，改了 .tsx 不重建等于白改。")
    else:
        print("文件已是修复后的版本。若还没构建过，仍需：")
        print("    cd %s && bash deploy.sh deploy" % root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
