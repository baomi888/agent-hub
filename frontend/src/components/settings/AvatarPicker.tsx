"use client";

// 头像选择器（第 1 批：内置图标；第 2 批会在同一网格里加"上传自己的图"页签）。
//
// 图标只列**能当形象用**的那些。CornIcon 里还有 warn（警告三角）、trash、close、
// check、back、arrow、plus —— 它们表达的是"一个操作"，做成头像会让人以为那是个按钮。
// 这份名单必须与后端 `core/account.py::AVATAR_ICONS` 一致：**后端才是权威**
// （它会拒绝白名单外的值），这里只是照着渲染，漏一个的表现是"选项不显示"，
// 多一个的表现是"点了报参数不合法"。
import CornIcon from "@/components/CornIcon";
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
  /** 是否正在保存某个值（用于把对应项置灰） */
  saving?: string | null;
}

export default function AvatarPicker({
  open,
  onClose,
  current = "",
  name,
  onPick,
  saving = null,
}: Props) {
  const busy = saving !== null;

  return (
    <DialogShell
      open={open}
      title="换个头像"
      desc="挑一个喜欢的图案。头像只是给自己看的门面，随时能换。"
      onClose={onClose}
      busy={busy}
      footer={
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
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose} disabled={busy}>
            取消
          </button>
        </>
      }
    >
      <div className="acct-avatar-preview">
        <UserAvatar avatar={current} name={name} className="h-16 w-16" />
        <div>
          <p className="acct-avatar-preview-name">{name || "（还没有名字）"}</p>
          <p className="hint" style={{ marginTop: 2 }}>
            头像会显示在侧栏和设置页
          </p>
        </div>
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
    </DialogShell>
  );
}
