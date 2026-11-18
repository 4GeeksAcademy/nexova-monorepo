"use client";

import IncidentForm from "@/components/incident-manager/IncidentForm";
import { useSectionView } from "@/lib/useSectionView";

export default function NewIncidentPage() {
  useSectionView("incidents");

  return (
    <section className="px-6 py-12">
      <div className="mx-auto w-full max-w-6xl">
        <header className="mb-8">
          <p className="font-mono text-xs uppercase tracking-widest text-teal-700">Nexova · Incidencias</p>
          <h1 className="mt-2 text-4xl font-medium tracking-tight text-stone-900">Nueva incidencia</h1>
          <p className="mt-3 max-w-2xl text-base leading-7 text-stone-600">
            Registra una incidencia desde cualquier punto de la empresa. Todos los campos son obligatorios.
          </p>
        </header>
        <IncidentForm />
      </div>
    </section>
  );
}
