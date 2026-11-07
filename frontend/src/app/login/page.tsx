"use client";

/* 登录 / 注册页。
 *
 * 为什么从 app/page.tsx 里拆出来：`/` 现在是落地页（未登录访客看到的那一屏），
 * 登录卡片挪到单独的路由，两件事各有各的地址、也各有各的 metadata 余地。
 * 拆开之后页面里只剩"我是谁 → 已登录就去工作台"这一层判断。
 */

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import AuthGate from "@/components/AuthGate";
import InstallBanner from "@/components/InstallBanner";
import { useAuth } from "@/lib/hooks/useAuth";

export default function LoginPage() {
  const auth = useAuth();
  const router = useRouter();

  // 已经登录的人不该停在登录页：直接送回工作台。
  // 用 replace 不用 push，否则后退键会把他推回一张已经登录过的登录卡片。
  useEffect(() => {
    if (!auth.loading && auth.me) router.replace("/");
  }, [auth.loading, auth.me, router]);

  if (auth.loading) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-canvas">
        <p className="text-fs-sm text-faint">正在确认登录状态...</p>
      </div>
    );
  }

  // 登录成功到路由替换之间会有一帧：不渲染卡片，免得表单闪一下再消失
  if (auth.me) return null;

  return (
    <>
      <AuthGate
        allowSignup={auth.allowSignup}
        backHref="/"
        onLogin={auth.login}
        onRegister={auth.register}
      />
      {/* 手机端首次进站的「装到桌面」提示。
          挂在登录门这一屏（而不是落地页）：落地页里已经有一整段教程，
          再弹一条就成了重复；这一屏才是"准备开始用了"的那一刻。 */}
      <InstallBanner />
    </>
  );
}
