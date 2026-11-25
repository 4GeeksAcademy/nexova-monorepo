import DashboardOverview from "@/components/dashboard/DashboardOverview";
import SectionGrid from "@/components/dashboard/SectionGrid";
import SectionViewTracker from "@/components/SectionViewTracker";
import { UPCOMING_SECTIONS } from "@/lib/dashboard-sections";


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
          <SectionGrid />
          <p className="mt-4 text-sm text-stone-500">
            Próximamente: {UPCOMING_SECTIONS.join(" · ")}.
          </p>
        </section>
      </div>
    </section>
  );
}
