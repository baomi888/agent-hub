"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import AuthGate from "@/components/AuthGate";
import { useAuth } from "@/lib/hooks/useAuth";

export default function LoginPage() {
  const auth = useAuth();
  const router = useRouter();
  useEffect(() => {
    if (auth.me) router.replace("/");
  }, [auth.me, router]);
  return <AuthGate allowSignup={auth.allowSignup} checking={auth.loading}
    onLogin={auth.login} onRegister={auth.register} />;
}
