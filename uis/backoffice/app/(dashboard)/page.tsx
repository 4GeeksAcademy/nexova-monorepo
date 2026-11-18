import Link from "next/link";
import DashboardOverview from "@/components/dashboard/DashboardOverview";
import SectionViewTracker from "@/components/SectionViewTracker";
import { DASHBOARD_SECTIONS, UPCOMING_SECTIONS } from "@/lib/dashboard-sections";

const CARD = "rounded-2xl border border-stone-300 bg-white/85 p-5 shadow-sm";

export default function Home() {
  return (
    <section className="px-6 py-12">
      <SectionViewTracker section="dashboard" />
      <div className="mx-auto w-full max-w-6xl">
        <header className="mb-8">
          <p className="text-xs font-semibold uppercase tracking-widest text-teal-700">Overview</p>
          <h1 className="mt-2 text-3xl font-medium tracking-tight text-stone-900 sm:text-4xl">
            Resumen del backoffice
          </h1>
          <p className="mt-3 max-w-2xl text-base leading-7 text-stone-600">
            Estado actual de incidencias, inventario, RFPs y actividad del sistema, con datos en vivo
            de cada área.
          </p>
        </header>

        <DashboardOverview />

        <section aria-labelledby="dashboard-sections" className="mt-10">
          <p className="text-xs font-semibold uppercase tracking-widest text-teal-700">Accesos</p>
          <h2
            id="dashboard-sections"
            className="mt-2 text-2xl font-medium tracking-tight text-stone-900"
          >
            Áreas del backoffice
          </h2>
          <ul className="mt-5 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {DASHBOARD_SECTIONS.map((section) => (
              <li key={section.href}>
                <Link
                  href={section.href}
                  prefetch={false}
                  className={`${CARD} group block h-full transition hover:border-teal-600`}
                >
                  <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-teal-700">
                    {section.area}
                  </p>
                  <p className="mt-2 flex items-center justify-between gap-2 text-base font-medium text-stone-900">
                    {section.title}
                    <span
                      aria-hidden="true"
                      className="text-stone-400 transition group-hover:translate-x-0.5 group-hover:text-teal-700"
                    >
                      →
                    </span>
                  </p>
                  <p className="mt-1 text-sm leading-6 text-stone-600">{section.description}</p>
                </Link>
              </li>
            ))}
          </ul>
          <p className="mt-4 text-sm text-stone-500">
            Próximamente: {UPCOMING_SECTIONS.join(" · ")}.
          </p>
        </section>
      </div>
    </section>
  );
}
