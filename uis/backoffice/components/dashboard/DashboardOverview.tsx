"use client";

import Link from "next/link";
import { EmptyState, KpiCard, Panel, ROW, Resource } from "@/components/dashboard/Resource";
import {
  ATTENTION_LIST_LIMIT,
  formatDay,
  formatMoney,
  formatPercent,
  loginFailureTotals,
  lowStockItems,
  openIncidentCount,
  pendingApprovalTickets,
  recentOpenIncidents,
  resolvedIncidentCount,
  sortedPerformanceEntries,
} from "@/lib/dashboard-overview";
import { CATEGORY_LABELS } from "@/lib/incident-manager-domain";
import { fetchSummary, listIncidents } from "@/lib/incident-manager";
import { listProducts } from "@/lib/inventory";
import { fetchLatestPipelineRun, fetchWeeklyPerformance } from "@/lib/reporting";
import { fetchRfpTickets } from "@/lib/rfp";
import { fetchTelemetryReport } from "@/lib/telemetry-report";
import { useAsyncResource } from "@/lib/useAsyncResource";

// Cargadores de módulo (identidad estable) para `useAsyncResource`.
const loadSummary = fetchSummary;
const loadOpenIncidents = () => listIncidents({ status: "open" });
const loadProducts = listProducts;
const loadTickets = fetchRfpTickets;
const loadTelemetry = () => fetchTelemetryReport();
const loadWeekly = () => fetchWeeklyPerformance();
const loadLatestRun = fetchLatestPipelineRun;

const BADGE = "shrink-0 rounded-full border px-2.5 py-0.5 font-mono text-xs";

export default function DashboardOverview() {
  const summary = useAsyncResource(loadSummary);
  const openIncidents = useAsyncResource(loadOpenIncidents);
  const products = useAsyncResource(loadProducts);
  const tickets = useAsyncResource(loadTickets);
  const telemetry = useAsyncResource(loadTelemetry);
  const weekly = useAsyncResource(loadWeekly);
  const latestRun = useAsyncResource(loadLatestRun);

  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <KpiCard
          label="Incidencias abiertas"
          state={summary.state}
          onRetry={summary.reload}
          render={(data) => {
            const inProgress = data.by_status.in_progress ?? 0;
            return {
              value: String(openIncidentCount(data)),
              detail: `de ${data.total} · ${resolvedIncidentCount(data)} resueltas${inProgress > 0 ? ` · ${inProgress} en curso` : ""}`,
            };
          }}
        />
        <KpiCard
          label="Productos con stock bajo"
          state={products.state}
          onRetry={products.reload}
          render={(data) => {
            const low = lowStockItems(data);
            const out = low.filter((item) => item.outOfStock).length;
            return {
              value: String(low.length),
              detail: `de ${data.length} productos${out > 0 ? ` · ${out} sin stock` : ""}`,
            };
          }}
        />
        <KpiCard
          label="RFPs por aprobar"
          state={tickets.state}
          onRetry={tickets.reload}
          render={(data) => ({
            value: String(pendingApprovalTickets(data).length),
            detail: `de ${data.length} tickets`,
          })}
        />
        <KpiCard
          label="Fallos de inicio de sesión"
          state={telemetry.state}
          onRetry={telemetry.reload}
          render={(data) => {
            const totals = loginFailureTotals(data);
            return totals.rate === null
              ? { value: "—", detail: "Sin intentos en los últimos 7 días" }
              : {
                  value: formatPercent(totals.rate),
                  detail: `${totals.failed} de ${totals.attempts} intentos · 7 días`,
                };
          }}
        />
      </div>

      <section aria-labelledby="attention-heading" className="mt-10">
        <p className="text-xs font-semibold uppercase tracking-widest text-teal-700">Requiere atención</p>
        <h2 id="attention-heading" className="mt-2 text-2xl font-medium tracking-tight text-stone-900">
          Lo que está pendiente ahora
        </h2>
        <div className="mt-5 grid gap-4 lg:grid-cols-3">
          <Panel eyebrow="Inventario" title="Stock bajo" href="/inventory/products" hrefLabel="Ver inventario">
            <Resource state={products.state} onRetry={products.reload}>
              {(data) => {
                const low = lowStockItems(data);
                if (low.length === 0) return <EmptyState>Todos los productos están por encima de su umbral.</EmptyState>;
                return (
                  <ul className="space-y-2">
                    {low.slice(0, ATTENTION_LIST_LIMIT).map((item) => (
                      <li key={item.id} className={`${ROW} flex items-center justify-between gap-3`}>
                        <span className="min-w-0">
                          <span className="block truncate text-sm text-stone-900">{item.name}</span>
                          <span className="block font-mono text-xs text-stone-500">
                            {item.sku} · {item.office}
                          </span>
                        </span>
                        <span
                          className={`${BADGE} ${
                            item.outOfStock
                              ? "border-red-200 bg-red-50 text-red-700"
                              : "border-amber-200 bg-amber-50 text-amber-700"
                          }`}
                        >
                          {item.outOfStock ? "Sin stock" : `${item.currentStock} / ${item.threshold}`}
                        </span>
                      </li>
                    ))}
                  </ul>
                );
              }}
            </Resource>
          </Panel>

          <Panel eyebrow="Ventas" title="RFPs esperando aprobación" href="/rfp" hrefLabel="Ver bandeja de RFPs">
            <Resource state={tickets.state} onRetry={tickets.reload}>
              {(data) => {
                const pending = pendingApprovalTickets(data);
                if (pending.length === 0) return <EmptyState>No hay RFPs esperando aprobación.</EmptyState>;
                return (
                  <ul className="space-y-2">
                    {pending.slice(0, ATTENTION_LIST_LIMIT).map((ticket) => (
                      <li key={ticket.ticket_id}>
                        <Link
                          href={`/rfp/${ticket.ticket_id}`}
                          prefetch={false}
                          className={`${ROW} flex items-center justify-between gap-3 transition hover:border-teal-600`}
                        >
                          <span className="min-w-0">
                            <span className="block truncate text-sm text-stone-900">
                              {ticket.client_name ?? ticket.original_filename}
                            </span>
                            <span className="block font-mono text-xs text-stone-500">
                              {ticket.ticket_id}
                              {ticket.rfp_id ? ` · ${ticket.rfp_id}` : ""}
                            </span>
                          </span>
                          <span className="shrink-0 text-xs text-stone-500">{formatDay(ticket.updated_at)}</span>
                        </Link>
                      </li>
                    ))}
                  </ul>
                );
              }}
            </Resource>
          </Panel>

          <Panel
            eyebrow="Soporte"
            title="Incidencias abiertas recientes"
            href="/incident-manager"
            hrefLabel="Ver incidencias"
          >
            <Resource state={openIncidents.state} onRetry={openIncidents.reload}>
              {(data) => {
                const recent = recentOpenIncidents(data);
                if (recent.length === 0) return <EmptyState>No hay incidencias abiertas.</EmptyState>;
                return (
                  <ul className="space-y-2">
                    {recent.map((incident) => (
                      <li key={incident.id} className={`${ROW} flex items-center justify-between gap-3`}>
                        <span className="min-w-0">
                          <span className="block truncate text-sm text-stone-900">{incident.title}</span>
                          <span className="block text-xs text-stone-500">{CATEGORY_LABELS[incident.category]}</span>
                        </span>
                        <span className="shrink-0 text-xs text-stone-500">{formatDay(incident.created_at)}</span>
                      </li>
                    ))}
                  </ul>
                );
              }}
            </Resource>
          </Panel>
        </div>
      </section>

      <section aria-labelledby="weekly-heading" className="mt-10">
        <p className="text-xs font-semibold uppercase tracking-widest text-teal-700">Reporte semanal</p>
        <h2 id="weekly-heading" className="mt-2 text-2xl font-medium tracking-tight text-stone-900">
          Última semana calculada
        </h2>
        <div className="mt-5">
          <Panel
            eyebrow="Dirección"
            title="Coste de material por oficina y programa"
            href="/reporting"
            hrefLabel="Ver reporte completo"
          >
            <Resource state={weekly.state} onRetry={weekly.reload}>
              {(report) => {
                if (report === null) return <EmptyState>Aún no hay ningún reporte semanal calculado.</EmptyState>;
                const entries = sortedPerformanceEntries(report.entries);
                return (
                  <>
                    <p className="text-sm text-stone-600">
                      Semana del {formatDay(report.week_start, "UTC")}
                      {latestRun.state.status === "ready" && latestRun.state.data?.finished_at
                        ? ` · actualizado el ${formatDay(latestRun.state.data.finished_at)}`
                        : ""}
                    </p>
                    {entries.length === 0 ? (
                      <div className="mt-3">
                        <EmptyState>Esa semana no tiene movimientos registrados.</EmptyState>
                      </div>
                    ) : (
                      <ul className="mt-3 grid gap-2 sm:grid-cols-2">
                        {entries.map((entry) => (
                          <li
                            key={`${entry.office}-${entry.programme_id}`}
                            className={`${ROW} flex items-center justify-between gap-3`}
                          >
                            <span className="min-w-0">
                              <span className="block truncate text-sm capitalize text-stone-900">{entry.office}</span>
                              <span className="block font-mono text-xs text-stone-500">
                                {entry.programme_id} · {entry.kits_delivered_count} {entry.kits_delivered_count === 1 ? "kit" : "kits"}
                              </span>
                            </span>
                            <span className="shrink-0 text-sm font-semibold text-stone-900">
                              {formatMoney(entry.total_material_cost, entry.currency)}
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </>
                );
              }}
            </Resource>
          </Panel>
        </div>
      </section>
    </>
  );
}
