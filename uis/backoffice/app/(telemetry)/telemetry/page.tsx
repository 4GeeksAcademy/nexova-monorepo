"use client";

import { useEffect, useMemo, useState } from "react";
import {
  fetchTelemetryReport,
  TelemetryReportError,
  type ReportRange,
  type TelemetryReport,
} from "@/lib/telemetry-report";

type RangeId = "7d" | "1d" | "30d";

const RANGES: { id: RangeId; label: string; days: number }[] = [
  { id: "1d", label: "Hoy", days: 0 },
  { id: "7d", label: "7 días", days: 7 },
  { id: "30d", label: "30 días", days: 30 },
];

const DAY_MS = 24 * 60 * 60 * 1000;

/**
 * Los 7 días por defecto se piden sin parámetros (la API los resuelve y los
 * cachea con clave fija). Para el resto se redondea a días UTC completos: así
 * la petición es idéntica durante todo el día y aprovecha la caché de 60 s.
 */
function rangeParams(id: RangeId): ReportRange {
  if (id === "7d") return {};
  const days = RANGES.find((r) => r.id === id)?.days ?? 0;
  const startOfToday = Math.floor(Date.now() / DAY_MS) * DAY_MS;
  return {
    startDate: new Date(startOfToday - days * DAY_MS).toISOString(),
    endDate: new Date(startOfToday + DAY_MS).toISOString(),
  };
}

type LoadState =
  | { range: RangeId; status: "ok"; report: TelemetryReport }
  | { range: RangeId; status: "error"; message: string; forbidden: boolean };

function formatInstant(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toISOString().replace("T", " ").slice(0, 16) + " UTC";
}

function percent(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`;
}

function Card({ title, question, children }: { title: string; question: string; children: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-stone-300 bg-white/80 p-6 shadow-sm">
      <h2 className="font-mono text-sm font-semibold text-stone-900">{title}</h2>
      <p className="mt-1 text-sm text-stone-600">{question}</p>
      <div className="mt-4">{children}</div>
    </section>
  );
}

function Empty() {
  return <p className="text-sm text-stone-500">Sin datos en este período.</p>;
}

function Bar({ ratio, tone = "teal" }: { ratio: number; tone?: "teal" | "red" }) {
  const width = `${Math.max(0, Math.min(1, ratio)) * 100}%`;
  return (
    <div className="h-2 w-full rounded-full bg-stone-200">
      <div
        className={`h-2 rounded-full ${tone === "red" ? "bg-red-500" : "bg-teal-600"}`}
        style={{ width }}
      />
    </div>
  );
}

const TH = "px-3 py-2 text-left font-mono text-[11px] uppercase tracking-wider text-stone-500";
const TD = "px-3 py-2 text-sm text-stone-800";

export default function TelemetryPage() {
  const [range, setRange] = useState<RangeId>("7d");
  const [state, setState] = useState<LoadState | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchTelemetryReport(rangeParams(range))
      .then((report) => {
        if (!cancelled) setState({ range, status: "ok", report });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const forbidden = err instanceof TelemetryReportError && err.status === 403;
        const message = err instanceof Error ? err.message : "No se pudo cargar el reporte.";
        setState({ range, status: "error", message, forbidden });
      });
    return () => {
      cancelled = true;
    };
  }, [range]);

  // Mientras la respuesta guardada no corresponda al rango elegido, se está cargando.
  const isLoading = state === null || state.range !== range;
  const report = state?.status === "ok" && !isLoading ? state.report : null;

  const perDay = useMemo(() => {
    const totals = new Map<string, number>();
    for (const row of report?.metrics.events_per_day ?? []) {
      totals.set(row.date, (totals.get(row.date) ?? 0) + row.count);
    }
    return [...totals.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [report]);
  const maxPerDay = Math.max(1, ...perDay.map(([, total]) => total));

  return (
    <section className="px-6 py-12">
      <div className="mx-auto w-full max-w-6xl">
        <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="font-mono text-xs uppercase tracking-widest text-teal-700">
              Nexova · Telemetría técnica
            </p>
            <h1 className="mt-2 text-4xl font-medium tracking-tight text-stone-900">
              Salud del sistema
            </h1>
            <p className="mt-3 text-sm text-stone-600">
              {report ? (
                <>
                  Período: <span className="font-mono">{formatInstant(report.period.from)}</span> →{" "}
                  <span className="font-mono">{formatInstant(report.period.to)}</span>
                </>
              ) : (
                "Período: —"
              )}
            </p>
          </div>
          <div className="flex gap-2" role="group" aria-label="Rango de fechas">
            {RANGES.map((r) => (
              <button
                key={r.id}
                type="button"
                onClick={() => setRange(r.id)}
                aria-pressed={range === r.id}
                className={`rounded-full border px-4 py-1.5 text-sm transition ${
                  range === r.id
                    ? "border-teal-600 bg-teal-600 text-white"
                    : "border-stone-300 bg-white text-stone-700 hover:border-teal-600"
                }`}
              >
                {r.label}
              </button>
            ))}
          </div>
        </header>

        {isLoading && <p className="text-sm text-stone-500">Cargando reporte…</p>}

        {!isLoading && state?.status === "error" && (
          <p role="alert" className="rounded-xl border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800">
            {state.forbidden ? "No tienes permisos para ver el reporte de telemetría." : state.message}
          </p>
        )}

        {report && (
          <div className="grid gap-6">
            <Card title="events_per_day" question="¿Qué eventos ocurren y con qué frecuencia cada día?">
              {perDay.length === 0 ? (
                <Empty />
              ) : (
                <>
                  <ul className="mb-6 space-y-2">
                    {perDay.map(([date, total]) => (
                      <li key={date} className="grid grid-cols-[6rem_1fr_3rem] items-center gap-3 text-sm">
                        <span className="font-mono text-stone-600">{date}</span>
                        <Bar ratio={total / maxPerDay} />
                        <span className="text-right font-mono text-stone-800">{total}</span>
                      </li>
                    ))}
                  </ul>
                  <div className="max-h-72 overflow-auto rounded-lg border border-stone-200">
                    <table className="w-full">
                      <thead className="sticky top-0 bg-stone-100">
                        <tr>
                          <th className={TH}>Fecha</th>
                          <th className={TH}>Tipo de evento</th>
                          <th className={`${TH} text-right`}>Eventos</th>
                        </tr>
                      </thead>
                      <tbody>
                        {report.metrics.events_per_day.map((row) => (
                          <tr key={`${row.date}-${row.event_type}`} className="border-t border-stone-100">
                            <td className={`${TD} font-mono`}>{row.date}</td>
                            <td className={`${TD} font-mono`}>{row.event_type}</td>
                            <td className={`${TD} text-right font-mono`}>{row.count}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </Card>

            <Card
              title="error_rate_by_type"
              question="¿Qué tipos de error aparecen y qué peso tienen sobre el total de eventos del día?"
            >
              {report.metrics.error_rate_by_type.length === 0 ? (
                <Empty />
              ) : (
                <table className="w-full">
                  <thead>
                    <tr>
                      <th className={TH}>Fecha</th>
                      <th className={TH}>Tipo de error</th>
                      <th className={`${TH} text-right`}>Errores</th>
                      <th className={`${TH} text-right`}>Total día</th>
                      <th className={`${TH} w-1/3`}>Tasa</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.metrics.error_rate_by_type.map((row) => (
                      <tr key={`${row.date}-${row.event_type}`} className="border-t border-stone-100">
                        <td className={`${TD} font-mono`}>{row.date}</td>
                        <td className={`${TD} font-mono`}>{row.event_type}</td>
                        <td className={`${TD} text-right font-mono`}>{row.errors}</td>
                        <td className={`${TD} text-right font-mono`}>{row.total_events}</td>
                        <td className={TD}>
                          <div className="flex items-center gap-3">
                            <Bar ratio={row.error_rate} tone="red" />
                            <span className="w-14 text-right font-mono">{percent(row.error_rate)}</span>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Card>

            <Card
              title="auth_failure_rate"
              question="¿Qué fracción de los intentos de login falla cada día?"
            >
              {report.metrics.auth_failure_rate.length === 0 ? (
                <Empty />
              ) : (
                <table className="w-full">
                  <thead>
                    <tr>
                      <th className={TH}>Fecha</th>
                      <th className={`${TH} text-right`}>Fallidos</th>
                      <th className={`${TH} text-right`}>Correctos</th>
                      <th className={`${TH} w-1/3`}>Tasa de fallo</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.metrics.auth_failure_rate.map((row) => (
                      <tr key={row.date} className="border-t border-stone-100">
                        <td className={`${TD} font-mono`}>{row.date}</td>
                        <td className={`${TD} text-right font-mono`}>{row.failed}</td>
                        <td className={`${TD} text-right font-mono`}>{row.succeeded}</td>
                        <td className={TD}>
                          <div className="flex items-center gap-3">
                            <Bar ratio={row.failure_rate} tone="red" />
                            <span className="w-14 text-right font-mono">{percent(row.failure_rate)}</span>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Card>
          </div>
        )}
      </div>
    </section>
  );
}
