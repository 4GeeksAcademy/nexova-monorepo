import Link from "next/link";
import AccountMenu from "@/components/AccountMenu";
import RequireAuth from "@/components/RequireAuth";

export default function DashboardLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <RequireAuth>
    <div className="flex min-h-screen bg-slate-950 text-slate-100 selection:bg-cyan-400/20 selection:text-cyan-100">
      <aside className="hidden w-72 shrink-0 border-r border-slate-800/80 bg-slate-950/90 text-slate-100 lg:block">
        <div className="border-b border-slate-800 px-6 py-6">
          <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">
            Nexova Solutions
          </p>
          <h1 className="mt-2 text-xl font-semibold text-slate-100">Backoffice</h1>
        </div>

        <nav className="px-4 py-6">
          <ul className="space-y-2">
            <li>
              <Link
                href="/"
                className="block rounded-xl border border-cyan-500/20 bg-slate-900 px-4 py-2 text-sm font-medium text-cyan-200 shadow-[0_0_0_1px_rgba(34,211,238,0.05)]"
              >
                Overview Financiero
              </Link>
            </li>
            <li>
              <Link
                href="/incidents"
                prefetch={false}
                className="block rounded-xl px-4 py-2 text-sm text-slate-400 transition hover:bg-slate-900 hover:text-slate-100"
              >
                Analizador de incidencias
              </Link>
            </li>
            <li>
              <Link
                href="/inventory/products"
                prefetch={false}
                className="block rounded-xl px-4 py-2 text-sm text-slate-400 transition hover:bg-slate-900 hover:text-slate-100"
              >
                Inventario de activos
              </Link>
            </li>
            <li>
              <span className="block rounded-xl px-4 py-2 text-sm text-slate-500">
                Flujo de Caja
              </span>
            </li>
            <li>
              <span className="block rounded-xl px-4 py-2 text-sm text-slate-500">
                Facturación
              </span>
            </li>
            <li>
              <span className="block rounded-xl px-4 py-2 text-sm text-slate-500">
                Proyecciones
              </span>
            </li>
          </ul>
        </nav>
      </aside>

      <div className="flex min-h-screen w-full flex-col">
        <header className="sticky top-0 z-20 border-b border-slate-800/80 bg-slate-950/75 px-5 py-4 backdrop-blur-xl sm:px-8">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">
                Dashboard Interno
              </p>
              <p className="text-sm text-slate-300">Control financiero consolidado</p>
            </div>
            <div className="flex items-center gap-3">
              <div className="rounded-full border border-slate-700 bg-slate-900/80 px-3 py-1 text-xs font-medium text-slate-200">
                Entorno: Producción
              </div>
              <AccountMenu />
            </div>
          </div>
        </header>

        <main className="flex-1 bg-[radial-gradient(circle_at_top,_rgba(56,189,248,0.12),_transparent_38%)] p-5 sm:p-8">
          {children}
        </main>
      </div>
    </div>
    </RequireAuth>
  );
}