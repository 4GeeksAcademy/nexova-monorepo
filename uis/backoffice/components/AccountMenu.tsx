"use client";

import Link from "next/link";
import { useAuth } from "@/lib/AuthContext";

const VARIANT_CLASSES = {
  dark: {
    email: "text-slate-400",
    pill: "border-slate-700 bg-slate-900/80 text-slate-200 hover:border-slate-600",
  },
  light: {
    email: "text-stone-600",
    pill: "border-stone-300 bg-white text-stone-700 hover:border-stone-400 hover:text-stone-900",
  },
} as const;

export default function AccountMenu({ variant = "dark" }: { variant?: "dark" | "light" }) {
  const { user, logout } = useAuth();
  const classes = VARIANT_CLASSES[variant];

  return (
    <div className="flex items-center gap-3 text-sm">
      {user && <span className={`hidden sm:inline ${classes.email}`}>{user.email}</span>}
      <Link
        href="/account/profile"
        className={`whitespace-nowrap rounded-lg border px-3 py-1 font-medium transition ${classes.pill}`}
      >
        Mi cuenta
      </Link>
      <button
        type="button"
        onClick={logout}
        className={`whitespace-nowrap rounded-lg border px-3 py-1 font-medium transition ${classes.pill}`}
      >
        Cerrar sesión
      </button>
    </div>
  );
}
