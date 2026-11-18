"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import IncidentsTable from "@/components/incident-manager/IncidentsTable";
import {
  BRANCHES,
  BRANCH_LABELS,
  ORIGINS,
  ORIGIN_LABELS,
  STATUSES,
  STATUS_LABELS,
  type Status,
  isBranch,
  isOrigin,
  isStatus,
} from "@/lib/incident-manager-domain";
import {
  CONNECTION_ERROR,
  IncidentManagerError,
  listIncidents,
  updateIncidentStatus,
  withStatus,
  type Incident,
  type IncidentFilters,
} from "@/lib/incident-manager";
import { useSectionView } from "@/lib/useSectionView";

interface Loaded {
  /** Clave de la petición a la que pertenece el resultado (filtros + intento). */
  key: string;
  incidents: Incident[] | null;
  error: string | null;
}

const SELECT =
  "rounded-md border border-stone-300 bg-white px-3 py-2 text-sm text-stone-900 focus:border-teal-600 focus:outline-none";

function errorMessage(err: unknown): string {
  return err instanceof IncidentManagerError ? err.message : CONNECTION_ERROR;
}

export default function IncidentListPage() {
  useSectionView("incidents");
  const [filters, setFilters] = useState<IncidentFilters>({});
  const [attempt, setAttempt] = useState(0);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [pendingIds, setPendingIds] = useState<ReadonlySet<number>>(new Set());
  const [notice, setNotice] = useState<string | null>(null);

  const key = `${filters.status ?? ""}|${filters.origin ?? ""}|${filters.branch ?? ""}|${attempt}`;
  // Cargando = todavía no hay resultado para los filtros actuales (sin setState síncrono en el efecto).
  const isLoading = loaded?.key !== key;

  useEffect(() => {
    let cancelled = false;
    listIncidents(filters)
      .then((incidents) => {
        if (!cancelled) setLoaded({ key, incidents, error: null });
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoaded({ key, incidents: null, error: errorMessage(err) });
      });
    return () => {
      cancelled = true;
    };
    // `key` resume `filters` y `attempt`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  function setIncidents(update: (rows: Incident[]) => Incident[]) {
    setLoaded((prev) =>
      prev && prev.incidents ? { ...prev, incidents: update(prev.incidents) } : prev,
    );
  }

  async function handleChangeStatus(incident: Incident, next: Status) {
    const previous = incident.status;
    setNotice(null);
    setIncidents((rows) => withStatus(rows, incident.id, next)); // optimista
    setPendingIds((ids) => new Set(ids).add(incident.id));
    try {
      const saved = await updateIncidentStatus(incident.id, next);
      setIncidents((rows) => withStatus(rows, saved.id, saved.status));
    } catch (err) {
      setIncidents((rows) => withStatus(rows, incident.id, previous)); // rollback
      setNotice(
        `No se pudo cambiar el estado de la incidencia ${incident.id}; se ha restaurado «${previous}». ${errorMessage(err)}`,
      );
    } finally {
      setPendingIds((ids) => {
        const rest = new Set(ids);
        rest.delete(incident.id);
        return rest;
      });
    }
  }

  function updateFilter<K extends keyof IncidentFilters>(name: K, value: string) {
    const guards = { status: isStatus, origin: isOrigin, branch: isBranch } as const;
    setFilters((prev) => ({ ...prev, [name]: guards[name](value) ? value : "" }));
    setNotice(null);
  }

  const incidents = loaded?.key === key ? loaded.incidents : null;
  const error = loaded?.key === key ? loaded.error : null;
  const hasFilters = Boolean(filters.status || filters.origin || filters.branch);

  return (
    <section className="px-6 py-12">
      <div className="mx-auto w-full max-w-6xl">
        <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="font-mono text-xs uppercase tracking-widest text-teal-700">Nexova · Incidencias</p>
            <h1 className="mt-2 text-4xl font-medium tracking-tight text-stone-900">Listado de incidencias</h1>
            <p className="mt-3 max-w-2xl text-base leading-7 text-stone-600">
              Todas las incidencias registradas. Cambia su estado directamente desde la tabla.
            </p>
          </div>
          <Link
            href="/incident-manager/new"
            className="rounded-lg bg-teal-700 px-5 py-2 text-sm font-medium text-white transition hover:bg-teal-800"
          >
            Nueva incidencia
          </Link>
        </header>

        <div className="mb-6 flex flex-wrap gap-3" role="group" aria-label="Filtros">
          <label className="text-sm text-stone-600">
            <span className="mr-2">Estado</span>
            <select className={SELECT} value={filters.status ?? ""} onChange={(e) => updateFilter("status", e.target.value)}>
              <option value="">Todos</option>
              {STATUSES.map((s) => (
                <option key={s} value={s}>{STATUS_LABELS[s]}</option>
              ))}
            </select>
          </label>
          <label className="text-sm text-stone-600">
            <span className="mr-2">Origen</span>
            <select className={SELECT} value={filters.origin ?? ""} onChange={(e) => updateFilter("origin", e.target.value)}>
              <option value="">Todos</option>
              {ORIGINS.map((o) => (
                <option key={o} value={o}>{ORIGIN_LABELS[o]}</option>
              ))}
            </select>
          </label>
          <label className="text-sm text-stone-600">
            <span className="mr-2">Sede</span>
            <select className={SELECT} value={filters.branch ?? ""} onChange={(e) => updateFilter("branch", e.target.value)}>
              <option value="">Todas</option>
              {BRANCHES.map((b) => (
                <option key={b} value={b}>{BRANCH_LABELS[b]}</option>
              ))}
            </select>
          </label>
          {hasFilters && (
            <button
              type="button"
              onClick={() => setFilters({})}
              className="text-sm text-teal-700 underline-offset-2 hover:underline"
            >
              Quitar filtros
            </button>
          )}
        </div>

        {notice && (
          <div role="alert" className="mb-4 flex items-start justify-between gap-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            <span>{notice}</span>
            <button type="button" onClick={() => setNotice(null)} className="shrink-0 font-medium underline-offset-2 hover:underline">
              Cerrar
            </button>
          </div>
        )}

        {isLoading && (
          <p role="status" className="rounded-lg border border-stone-200 bg-white px-4 py-6 text-sm text-stone-600">
            Cargando incidencias…
          </p>
        )}

        {!isLoading && error && (
          <div role="alert" className="rounded-lg border border-red-200 bg-red-50 px-4 py-4 text-sm text-red-700">
            <p>{error}</p>
            <button
              type="button"
              onClick={() => setAttempt((n) => n + 1)}
              className="mt-3 rounded-lg border border-red-300 bg-white px-4 py-1.5 font-medium text-red-700 transition hover:bg-red-100"
            >
              Reintentar
            </button>
          </div>
        )}

        {!isLoading && !error && incidents && incidents.length === 0 && (
          <p className="rounded-lg border border-stone-200 bg-white px-4 py-6 text-sm text-stone-600">
            {hasFilters
              ? "No hay incidencias que coincidan con los filtros aplicados."
              : "Todavía no hay incidencias registradas."}{" "}
            {hasFilters ? (
              <button type="button" onClick={() => setFilters({})} className="text-teal-700 underline-offset-2 hover:underline">
                Quitar filtros
              </button>
            ) : (
              <Link href="/incident-manager/new" className="text-teal-700 underline-offset-2 hover:underline">
                Registrar la primera
              </Link>
            )}
          </p>
        )}

        {!isLoading && !error && incidents && incidents.length > 0 && (
          <IncidentsTable incidents={incidents} pendingIds={pendingIds} onChangeStatus={handleChangeStatus} />
        )}
      </div>
    </section>
  );
}
