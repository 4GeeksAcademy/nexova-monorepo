import Link from "next/link";
import type { ReactNode } from "react";
import AccountMenu from "@/components/AccountMenu";
import RequireAuth from "@/components/RequireAuth";

interface Tab {
  href: string;
  label: string;
}

interface AppShellProps {
  eyebrow: string;
  subtitle: string;
  tabs?: Tab[];
  children: ReactNode;
}

export default function AppShell({ eyebrow, subtitle, tabs, children }: AppShellProps) {
  return (
    <RequireAuth>
      <div className="min-h-screen bg-[linear-gradient(180deg,#f7f5ef_0%,#efe8dc_100%)] text-stone-900">
        <header className="border-b border-stone-300/80 bg-stone-50/75 backdrop-blur-xl">
          <div className="mx-auto flex w-full max-w-6xl items-center justify-between px-6 py-4">
            <div>
              <p className="font-mono text-[11px] uppercase tracking-[0.24em] text-stone-500">
                {eyebrow}
              </p>
              <p className="mt-1 text-sm text-stone-600">{subtitle}</p>
            </div>
            <div className="flex items-center gap-3">
              <Link
                href="/"
                prefetch={false}
                className="rounded-full border border-stone-300 bg-white px-4 py-2 text-sm font-medium text-stone-700 transition hover:border-stone-400 hover:text-stone-900"
              >
                Volver al dashboard
              </Link>
              <AccountMenu variant="light" />
            </div>
          </div>
          {tabs && (
            <nav className="mx-auto w-full max-w-6xl px-6 pb-3">
              <ul className="flex flex-wrap gap-2">
                {tabs.map((tab) => (
                  <li key={tab.href}>
                    <Link
                      href={tab.href}
                      className="inline-block rounded-full border border-stone-300 bg-white px-4 py-1.5 text-sm text-stone-700 transition hover:border-teal-600 hover:text-teal-700"
                    >
                      {tab.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </nav>
          )}
        </header>

        {children}
      </div>
    </RequireAuth>
  );
}
