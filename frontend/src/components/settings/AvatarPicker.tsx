"use client";

// 头像选择器：内置图标 + 上传自己的照片（带裁剪）。
//
// 图标只列**能当形象用**的那些。CornIcon 里还有 warn（警告三角）、trash、close、
// check、back、arrow、plus —— 它们表达的是"一个操作"，做成头像会让人以为那是个按钮。
// 这份名单必须与后端 `core/account.py::AVATAR_ICONS` 一致：**后端才是权威**
// （它会拒绝白名单外的值），这里只是照着渲染，漏一个的表现是"选项不显示"，
// 多一个的表现是"点了报参数不合法"。
//
// 两种状态共用同一个 DialogShell（不是两个 if 各 return 一份）：
// DialogShell 里那段 effect 只在 `open` 变化时跑，重挂会重新抓 opener、重新抢焦点，
// 表现为"选完图，光标跳了一下"。
import { useCallback, useRef, useState } from "react";

import CornIcon from "@/components/CornIcon";
import { MAX_SOURCE_BYTES, MAX_UPLOAD_BYTES } from "@/lib/imageCrop";
import AvatarCropper, { type CropperHandle } from "./AvatarCropper";
import DialogShell from "./DialogShell";
import UserAvatar from "./UserAvatar";

export const AVATAR_ICON_CHOICES = [
  "corn",
  "book",
  "pen",
  "notes",
  "bolt",
  "briefcase",
  "globe",
  "target",
  "search",
  "bulb",
  "store",
  "tools",
  "clock",
  "calendar",
  "star",
  "link",
  "download",
  "upload",
  "refresh",
  "user",
] as const;

/** 每个图标配一句人话，避免用户对着 20 个图案猜"这是啥" */
const HINTS: Record<string, string> = {
  corn: "知穗",
  book: "读书",
  pen: "写字",
  notes: "笔记",
  bolt: "冲刺",
  briefcase: "实习",
  globe: "外语",
  target: "目标",
  search: "找资料",
  bulb: "有想法",
  store: "逛模板",
  tools: "工具箱",
  clock: "计时",
  calendar: "排日程",
  star: "收藏",
  link: "爱分享",
  download: "存档",
  upload: "交作业",
  refresh: "重来",
  user: "就是我",
};

/** 待裁剪的图。`id` 只用来当 key：同一张图重选也要能重挂裁剪器（回到 1 倍居中） */
interface PendingCrop {
  file: File;
  id: number;
}

interface Props {
  open: boolean;
  onClose: () => void;
  /** 当前头像值（"" / "icon:xxx" / "img:xxx"） */
  current?: string | null;
  /** 昵称或登录名，用于"恢复首字母"时的预览 */
  name?: string | null;
  /**
   * 保存。返回 true 表示成功（本组件据此关闭）。
   * 失败时保持打开，让用户能直接换一个再试 —— 关掉再打开要重新找位置。
   */
  onPick: (avatar: string) => Promise<boolean>;
  /**
   * 上传一张**已裁好**的图。**返回 null = 成功，返回字符串 = 要显示给用户的错误**。
   *
   * 为什么不是 boolean：后端给的错误是中文人话（太大 / 打不开 / 太频繁），
   * 直接显示在弹窗里比"上传没成功"有用得多。
   */
  onUpload: (blob: Blob) => Promise<string | null>;
  /** 是否正在保存某个值（用于把对应项置灰） */
  saving?: string | null;
}

export default function AvatarPicker({
  open,
  onClose,
  current = "",
  name,
  onPick,
  onUpload,
  saving = null,
}: Props) {
  const fileRef = useRef<HTMLInputElement>(null);
  const cropperRef = useRef<CropperHandle>(null);
  /** 自增，只用于给裁剪器换 key */
  const seqRef = useRef(0);

  const [crop, setCrop] = useState<PendingCrop | null>(null);
  /** 上传中的阶段文案。裁剪阶段的耗时不在这里（在裁剪器自己的按钮上） */
  const [phase, setPhase] = useState<"idle" | "upload">("idle");
  const [err, setErr] = useState<string | null>(null);

  const cropping = crop !== null;
  const busy = saving !== null || phase !== "idle";

  /** 回到"挑图案"那一屏。裁剪态下先退这一步，再按一次才关整个弹窗 */
  const backToPicker = useCallback(() => {
    setCrop(null);
    setErr(null);
    setPhase("idle");
  }, []);

  const close = useCallback(() => {
    if (cropping) {
      backToPicker();
      return;
    }
    // 错误属于“这一次选择”。关闭当下清掉，下次重新打开就不会先看到上一张图留下的红字
    setErr(null);
    setPhase("idle");
    onClose();
  }, [cropping, backToPicker, onClose]);

  async function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    // 立刻清空 value：不清的话"再选同一张图"不会触发 change，
    // 用户会以为点了没反应（这是 file input 的经典坑）
    e.target.value = "";
    if (!file) return;

    setErr(null);
    // 明显不是图片时先拦一道，省得白等一轮解码。
    // 注意这里**不是**安全边界：类型能伪造，真正的判定在后端。放在这里只是省一次无用的解码
    if (file.type && !/^image\/(jpeg|jpg|png|webp)$/i.test(file.type)) {
      setErr("只支持 JPG / PNG / WebP。iPhone 的 HEIC 可以在「设置 → 相机 → 格式」里改成「兼容性最佳」再拍。");
      return;
    }
    if (file.size > MAX_SOURCE_BYTES) {
      setErr(`这张图有 ${(file.size / 1024 / 1024).toFixed(0)}MB，太大了，换一张 20MB 以内的`);
      return;
    }

    // 原图**不上传**，只在这里解码给裁剪器用。所以不用先压缩，"选完再压"那一步没了
    seqRef.current += 1;
    setCrop({ file, id: seqRef.current });
  }

  /** 解码失败（HEIC 伪装、文件损坏）：退回选择态，把原因说清楚 */
  const handleDecodeError = useCallback(() => {
    setCrop(null);
    setErr("这张图打不开，换一张 JPG / PNG / WebP 试试");
  }, []);

  async function confirmCrop() {
    const el = cropperRef.current;
    if (!el || busy) return;
    setErr(null);
    setPhase("upload");
    try {
      const blob = await el.commit();
      if (!blob) {
        setErr("图片裁切失败，换一张试试");
        return;
      }
      if (blob.size > MAX_UPLOAD_BYTES) {
        setErr(`裁完还有 ${(blob.size / 1024 / 1024).toFixed(1)}MB，换一张试试`);
        return;
      }
      const failed = await onUpload(blob);
      if (failed) setErr(failed);
    } catch {
      setErr("图片处理失败，换一张试试");
    } finally {
      setPhase("idle");
    }
  }

  const pickFooter = (
    <>
      <button
        type="button"
        className="btn btn-ghost btn-sm"
        onClick={() => void onPick("")}
        disabled={busy || !current}
        title={current ? "清掉头像，改回名字首字母" : "当前就是首字母"}
      >
        用名字首字母
      </button>
      <button type="button" className="btn btn-ghost btn-sm" onClick={close} disabled={busy}>
        取消
      </button>
    </>
  );

  const cropFooter = (
    <>
      <button
        type="button"
        className="btn btn-ghost btn-sm"
        onClick={() => fileRef.current?.click()}
        disabled={busy}
      >
        重新选一张
      </button>
      <button type="button" className="btn btn-ghost btn-sm" onClick={backToPicker} disabled={busy}>
        取消
      </button>
      <button
        type="button"
        className="btn btn-primary btn-sm"
        onClick={() => void confirmCrop()}
        disabled={busy}
      >
        {phase === "upload" ? "正在上传…" : "用它做头像"}
      </button>
    </>
  );

  return (
    <DialogShell
      open={open}
      title={cropping ? "调整头像" : "换个头像"}
      desc={
        cropping ? (
          // 显式断行：交给浏览器自动折行时，桌面上会断成"…就是别人看到 / 的。"
          <>
            拖动照片调整位置，双指捏合或拉滑块放大。
            <br />
            圆框里看到的，就是别人看到的。
          </>
        ) : (
          "挑一个喜欢的图案，或者传一张自己的照片。随时能换。"
        )
      }
      onClose={close}
      busy={busy}
      footer={cropping ? cropFooter : pickFooter}
    >
      <input
        ref={fileRef}
        type="file"
        accept="image/jpeg,image/png,image/webp"
        className="sr-only"
        onChange={(e) => void handleFile(e)}
        aria-label="选择要上传的头像图片"
      />

      {cropping ? (
        <>
          <AvatarCropper
            key={crop.id}
            ref={cropperRef}
            file={crop.file}
            busy={busy}
            onDecodeError={handleDecodeError}
          />
          {err && (
            <p className="acct-upload-err" role="alert">
              {err}
            </p>
          )}
        </>
      ) : (
        <>
          <div className="acct-avatar-preview">
            <UserAvatar avatar={current} name={name} className="h-16 w-16" />
            <div>
              <p className="acct-avatar-preview-name">{name || "（还没有名字）"}</p>
              <p className="hint" style={{ marginTop: 2 }}>
                头像会显示在侧栏和设置页
              </p>
            </div>
          </div>

          <div className="acct-upload">
            <button
              type="button"
              className="btn btn-ghost btn-sm acct-upload-btn"
              onClick={() => fileRef.current?.click()}
              disabled={busy}
            >
              <CornIcon name="upload" className="h-4 w-4" />
              <span>上传自己的照片</span>
            </button>
            <p className="hint acct-upload-hint">
              JPG / PNG / WebP。选完可以拖动调整位置，只上传裁好的那块。
            </p>
            {err && (
              <p className="acct-upload-err" role="alert">
                {err}
              </p>
            )}
          </div>

          <div className="acct-pick-grid" role="group" aria-label="可选头像">
            {AVATAR_ICON_CHOICES.map((key) => {
              const value = `icon:${key}`;
              const on = current === value;
              return (
                <button
                  key={key}
                  type="button"
                  className={`acct-pick-item${on ? " is-on" : ""}`}
                  onClick={() => void onPick(value)}
                  disabled={busy}
                  aria-pressed={on}
                  title={HINTS[key] || key}
                >
                  <CornIcon name={key} className="h-7 w-7" />
                  <span className="acct-pick-label">{HINTS[key] || key}</span>
                  {saving === value && <span className="acct-pick-busy">保存中</span>}
                </button>
              );
            })}
          </div>
        </>
      )}
    </DialogShell>
  );
}
