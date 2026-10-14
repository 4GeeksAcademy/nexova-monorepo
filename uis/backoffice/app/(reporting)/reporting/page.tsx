"use client";

import { useEffect, useState } from "react";
import {
  fetchLatestPipelineRun,
  fetchWeeklyPerformance,
  type Office,
  type PipelineRunSummary,
  type WeeklyPerformanceEntry,
  type WeeklyPerformanceReport,
} from "@/lib/reporting";

/**
 * Reporte Semanal de Desempeño por Oficina y Programa — vista para Laura (CEO)
 * y Elena (L&D). Muestra los 4 KPIs del CONTEXT con su nombre exacto, por
 * oficina y programa, sin mezclar nunca EUR y USD.
 */

// --- Fechas: la semana es ISO y en UTC (lunes a domingo) -----------------------

const DAY_MS = 24 * 60 * 60 * 1000;

function isoDateToMs(iso: string): number {
  const [year, month, day] = iso.split("-").map(Number);
  return Date.UTC(year, month - 1, day);
}

function msToIsoDate(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

function addDays(iso: string, days: number): string {
  return msToIsoDate(isoDateToMs(iso) + days * DAY_MS);
}

function currentWeekStart(): string {
  const today = Math.floor(Date.now() / DAY_MS) * DAY_MS;
  const weekday = (new Date(today).getUTCDay() + 6) % 7; // lunes = 0
  return msToIsoDate(today - weekday * DAY_MS);
}

const DAY_FORMAT = new Intl.DateTimeFormat("es-ES", { timeZone: "UTC", day: "numeric" });
const DAY_MONTH_FORMAT = new Intl.DateTimeFormat("es-ES", {
  timeZone: "UTC",
  day: "numeric",
  month: "long",
});
const FULL_DATE_FORMAT = new Intl.DateTimeFormat("es-ES", {
  timeZone: "UTC",
  day: "numeric",
  month: "long",
  year: "numeric",
});
const UPDATED_FORMAT = new Intl.DateTimeFormat("es-ES", { dateStyle: "long", timeStyle: "short" });

/** "Semana del lunes 21 al domingo 27 de septiembre de 2026". */
function formatWeek(weekStart: string): string {
  const start = new Date(isoDateToMs(weekStart));
  const end = new Date(isoDateToMs(weekStart) + 6 * DAY_MS);
  const sameYear = start.getUTCFullYear() === end.getUTCFullYear();
  const sameMonth = sameYear && start.getUTCMonth() === end.getUTCMonth();
  const from = sameMonth
    ? DAY_FORMAT.format(start)
    : sameYear
      ? DAY_MONTH_FORMAT.format(start)
      : FULL_DATE_FORMAT.format(start);
  return `Semana del lunes ${from} al domingo ${FULL_DATE_FORMAT.format(end)}`;
}

function formatUpdated(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : UPDATED_FORMAT.format(date);
}

// --- Etiquetas para negocio -------------------------------------------------------

const OFFICES: { id: Office; label: string }[] = [
  { id: "valencia", label: "Valencia" },
  { id: "miami", label: "Miami" },
];

function programmeLabel(programmeId: string): string {
  return programmeId === "unassigned" ? "Sin programa asignado" : programmeId;
}

function formatMoney(amount: number, currency: string): string {
  return new Intl.NumberFormat("es-ES", { style: "currency", currency }).format(amount);
}

type CountKey = "kits_delivered_count" | "shortage_events_count" | "cost_variance_events_count";

interface CountKpi {
  key: CountKey;
  title: string;
  description: string;
  unit: [singular: string, plural: string];
  /** Si existe, un valor > 0 es una señal de alerta y se resalta. */
  alertHint?: string;
}

// Nombres y descripciones tomados de "KPIs a medir" del CONTEXT.
const COST_KPI = {
  title: "Costo de material por oficina/programa",
  description:
    "Cuánto gastó cada oficina adquiriendo material de formación o incorporación para cada programa durante la semana, en su moneda local.",
};

const COUNT_KPIS: CountKpi[] = [
  {
    key: "kits_delivered_count",
    title: "Kits entregados",
    description:
      "Cuántos kits o certificados se entregaron a clientes, candidatos, consultores o agentes durante la semana.",
    unit: ["kit", "kits"],
  },
  {
    key: "shortage_events_count",
    title: "Frecuencia de escasez",
    description:
      "Cuántas veces durante la semana el stock de material de un programa cayó por debajo del mínimo configurado.",
    unit: ["vez", "veces"],
    alertHint: "Conviene reimprimir o reordenar antes de la próxima ola de matrículas.",
  },
  {
    key: "cost_variance_events_count",
    title: "Frecuencia de variación de costo",
    description:
      "Cuántas veces durante la semana el costo de un proveedor de material subió de forma anómala respecto a su histórico.",
    unit: ["vez", "veces"],
    alertHint: "Conviene renegociar con el proveedor o buscar uno alterno.",
  },
];

function plural(value: number, [singular, pluralForm]: [string, string]): string {
  return `${value} ${value === 1 ? singular : pluralForm}`;
}

// --- Componentes -----------------------------------------------------------------

function Bar({ ratio, tone }: { ratio: number; tone: "teal" | "amber" }) {
  const width = `${Math.max(0, Math.min(1, ratio)) * 100}%`;
  return (
    <div className="h-2.5 w-full rounded-full bg-stone-200">
      <div
        className={`h-2.5 rounded-full ${tone === "amber" ? "bg-amber-500" : "bg-teal-600"}`}
        style={{ width }}
      />
    </div>
  );
}

function KpiCard({
  title,
  description,
  footer,
  children,
}: {
  title: string;
  description: string;
  footer?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-stone-300 bg-white/85 p-6 shadow-sm">
      <h2 className="text-lg font-semibold text-stone-900">{title}</h2>
      <p className="mt-1 text-sm text-stone-600">{description}</p>
      <div className="mt-5 space-y-5">{children}</div>
      {footer}
    </section>
  );
}

function OfficeBlock({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-stone-500">{label}</h3>
      <ul className="space-y-2">{children}</ul>
    </div>
  );
}

function Row({ label, bar, value }: { label: string; bar: React.ReactNode; value: React.ReactNode }) {
  return (
    <li className="grid grid-cols-[minmax(8rem,12rem)_1fr_auto] items-center gap-4 text-sm">
      <span className="truncate text-stone-800" title={label}>
        {label}
      </span>
      {bar}
      <span className="min-w-[6rem] text-right font-medium text-stone-900 tabular-nums">{value}</span>
    </li>
  );
}

function entriesByOffice(entries: WeeklyPerformanceEntry[]) {
  return OFFICES.map((office) => ({
    ...office,
    entries: entries
      .filter((entry) => entry.office === office.id)
      .sort((a, b) => programmeLabel(a.programme_id).localeCompare(programmeLabel(b.programme_id))),
  })).filter((office) => office.entries.length > 0);
}

function CostCard({ entries }: { entries: WeeklyPerformanceEntry[] }) {
  return (
    <KpiCard title={COST_KPI.title} description={COST_KPI.description}>
      {entriesByOffice(entries).map((office) => {
        const currency = office.entries[0].currency;
        const total = office.entries.reduce((sum, entry) => sum + entry.total_material_cost, 0);
        // Cada oficina se escala contra sí misma: EUR y USD no son comparables.
        const max = Math.max(...office.entries.map((entry) => entry.total_material_cost), 0);
        return (
          <OfficeBlock key={office.id} label={`${office.label} · ${currency}`}>
            {office.entries.map((entry) => (
              <Row
                key={entry.programme_id}
                label={programmeLabel(entry.programme_id)}
                bar={<Bar ratio={max > 0 ? entry.total_material_cost / max : 0} tone="teal" />}
                value={formatMoney(entry.total_material_cost, currency)}
              />
            ))}
            <li className="flex justify-between border-t border-stone-200 pt-2 text-sm">
              <span className="text-stone-600">Total {office.label}</span>
              <span className="font-semibold text-stone-900 tabular-nums">
                {formatMoney(total, currency)}
              </span>
            </li>
          </OfficeBlock>
        );
      })}
    </KpiCard>
  );
}

function CountCard({ kpi, entries }: { kpi: CountKpi; entries: WeeklyPerformanceEntry[] }) {
  const max = Math.max(...entries.map((entry) => entry[kpi.key]), 0);
  const total = entries.reduce((sum, entry) => sum + entry[kpi.key], 0);
  const alerting = kpi.alertHint !== undefined && total > 0;

  return (
    <KpiCard
      title={kpi.title}
      description={kpi.description}
      footer={
        alerting ? (
          <p className="mt-5 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            {plural(total, kpi.unit)} esta semana. {kpi.alertHint}
          </p>
        ) : null
      }
    >
      {entriesByOffice(entries).map((office) => (
        <OfficeBlock key={office.id} label={office.label}>
          {office.entries.map((entry) => {
            const value = entry[kpi.key];
            const highlight = kpi.alertHint !== undefined && value > 0;
            return (
              <Row
                key={entry.programme_id}
                label={programmeLabel(entry.programme_id)}
                bar={<Bar ratio={max > 0 ? value / max : 0} tone={highlight ? "amber" : "teal"} />}
                value={<span className={highlight ? "text-amber-700" : undefined}>{plural(value, kpi.unit)}</span>}
              />
            );
          })}
        </OfficeBlock>
      ))}
    </KpiCard>
  );
}

function UpdateStatus({ run }: { run: PipelineRunSummary | null }) {
  if (run === null) return null;
  if (run.status === "running") {
    return <p className="mt-2 text-sm text-stone-500">Actualizando los datos…</p>;
  }
  if (run.status === "failed") {
    return (
      <p role="alert" className="mt-3 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
        La última actualización del reporte ({formatUpdated(run.started_at)}) no se completó. Las cifras pueden no
        incluir los movimientos más recientes.
      </p>
    );
  }
  const when = run.finished_at ?? run.started_at;
  return <p className="mt-2 text-sm text-stone-500">Datos actualizados el {formatUpdated(when)}.</p>;
}

// --- Página ------------------------------------------------------------------------

type LoadState =
  | { key: string; status: "ok"; report: WeeklyPerformanceReport | null; run: PipelineRunSummary | null }
  | { key: string; status: "error"; message: string };

const LATEST = "latest";

export default function ReportingPage() {
  // `null` = la última semana calculada.
  const [requestedWeek, setRequestedWeek] = useState<string | null>(null);
  const [state, setState] = useState<LoadState | null>(null);
  const requestKey = requestedWeek ?? LATEST;

  useEffect(() => {
    let cancelled = false;
    Promise.all([fetchWeeklyPerformance(requestedWeek ?? undefined), fetchLatestPipelineRun()])
      .then(([report, run]) => {
        if (!cancelled) setState({ key: requestKey, status: "ok", report, run });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : "No se pudo cargar el reporte.";
        setState({ key: requestKey, status: "error", message });
      });
    return () => {
      cancelled = true;
    };
  }, [requestedWeek, requestKey]);

  // Mientras la respuesta guardada no corresponda a la semana pedida, se está cargando.
  const isLoading = state === null || state.key !== requestKey;
  const loaded = !isLoading && state?.status === "ok" ? state : null;
  const report = loaded?.report ?? null;
  const shownWeek = report?.week_start ?? requestedWeek;
  const nextWeek = shownWeek ? addDays(shownWeek, 7) : null;
  const canGoForward = nextWeek !== null && nextWeek <= currentWeekStart();

  const navButton =
    "rounded-full border border-stone-300 bg-white px-4 py-1.5 text-sm text-stone-700 transition hover:border-teal-600 disabled:cursor-not-allowed disabled:opacity-40";

  return (
    <section className="px-6 py-12">
      <div className="mx-auto w-full max-w-5xl">
        <header className="mb-8">
          <p className="text-xs font-semibold uppercase tracking-widest text-teal-700">Reporte semanal</p>
          <h1 className="mt-2 text-3xl font-medium tracking-tight text-stone-900 sm:text-4xl">
            Desempeño por Oficina y Programa
          </h1>
          <p className="mt-3 text-base font-medium text-stone-800">
            {shownWeek ? formatWeek(shownWeek) : " "}
          </p>
          {loaded && <UpdateStatus run={loaded.run} />}

          <div className="mt-5 flex flex-wrap gap-2" role="group" aria-label="Cambiar de semana">
            <button
              type="button"
              className={navButton}
              disabled={!shownWeek || isLoading}
              onClick={() => shownWeek && setRequestedWeek(addDays(shownWeek, -7))}
            >
              ← Semana anterior
            </button>
            <button
              type="button"
              className={navButton}
              disabled={!canGoForward || isLoading}
              onClick={() => nextWeek && setRequestedWeek(nextWeek)}
            >
              Semana siguiente →
            </button>
            <button
              type="button"
              className={navButton}
              disabled={requestedWeek === null || isLoading}
              onClick={() => setRequestedWeek(null)}
            >
              Última semana calculada
            </button>
          </div>
        </header>

        {isLoading && <p className="text-sm text-stone-500">Cargando reporte…</p>}

        {!isLoading && state?.status === "error" && (
          <p role="alert" className="rounded-xl border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800">
            No se pudo cargar el reporte. {state.message}
          </p>
        )}

        {loaded && report === null && (
          <p className="rounded-2xl border border-stone-300 bg-white/85 p-6 text-sm text-stone-700">
            {requestedWeek === null
              ? "Todavía no hay semanas calculadas. El reporte se genera automáticamente cada lunes por la mañana."
              : "No hay datos para esta semana."}
          </p>
        )}

        {report && report.entries.length === 0 && (
          <p className="rounded-2xl border border-stone-300 bg-white/85 p-6 text-sm text-stone-700">
            No hubo movimientos de material registrados esta semana.
          </p>
        )}

        {report && report.entries.length > 0 && (
          <div className="grid gap-6">
            <CostCard entries={report.entries} />
            {COUNT_KPIS.map((kpi) => (
              <CountCard key={kpi.key} kpi={kpi} entries={report.entries} />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
