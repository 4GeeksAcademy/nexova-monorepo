"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { getToken } from "@/lib/auth-storage";

export default function RequireAuth({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [authorized, setAuthorized] = useState<boolean | null>(null);

  useEffect(() => {
    if (getToken()) {
      setAuthorized(true);
    } else {
      setAuthorized(false);
      router.replace("/login");
    }
  }, [router]);

  if (!authorized) {
    return null;
  }

  return <>{children}</>;
}
