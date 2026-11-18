"use client";

import SummaryPanel from "@/components/incident-manager/SummaryPanel";
import { useSectionView } from "@/lib/useSectionView";

export default function IncidentSummaryPage() {
  useSectionView("incidents");

  return (
    <section className="px-6 py-12">
      <div className="mx-auto w-full max-w-6xl">
        <header className="mb-8">
          <p className="font-mono text-xs uppercase tracking-widest text-teal-700">Nexova · Incidencias</p>
          <h1 className="mt-2 text-4xl font-medium tracking-tight text-stone-900">Resumen de incidencias</h1>
          <p className="mt-3 max-w-2xl text-base leading-7 text-stone-600">
            Totales por estado, origen, categoría y sede.
          </p>
        </header>
        <SummaryPanel />
      </div>
    </section>
  );
}
