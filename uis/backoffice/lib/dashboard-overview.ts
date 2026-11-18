/**
 * Lógica pura del Overview del dashboard: convierte las respuestas ya validadas
 * de cada módulo (incidencias, inventario, RFPs, telemetría, reporte semanal) en
 * lo que se muestra en pantalla. Sin red ni React, para poder probarla con vitest.
 */

import type { Incident, IncidentSummary } from "./incident-manager";
import type { Asset } from "./inventory-types";
import type { WeeklyPerformanceEntry } from "./reporting";
import type { RfpTicketSummary } from "./rfp";
import type { TelemetryReport } from "./telemetry-report";

/** Umbral de stock bajo cuando el producto no declara el suyo (`min_stock_threshold`). */
export const DEFAULT_LOW_STOCK_THRESHOLD = 5;

/** Filas máximas de cada lista de «Requiere atención». */
export const ATTENTION_LIST_LIMIT = 5;

export const PENDING_APPROVAL_STATUS = "esperando_aprobación";

// --- Inventario ---------------------------------------------------------------

export interface LowStockItem {
  id: number;
  name: string;
  sku: string;
  office: string;
  currentStock: number;
  threshold: number;
  outOfStock: boolean;
}

/** El backend envía `min_stock_threshold` por producto; si falta o no es válido, el valor por defecto. */
export function stockThreshold(asset: Asset): number {
  const value = asset.min_stock_threshold;
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : DEFAULT_LOW_STOCK_THRESHOLD;
}

/** Productos en o bajo su umbral, los más urgentes primero (sin stock, luego menor ratio stock/umbral). */
export function lowStockItems(assets: readonly Asset[]): LowStockItem[] {
  return assets
    .filter((asset) => Number.isFinite(asset.current_stock))
    .map((asset) => {
      const threshold = stockThreshold(asset);
      return {
        id: asset.id,
        name: asset.name,
        sku: asset.sku,
        office: asset.office,
        currentStock: asset.current_stock,
        threshold,
        outOfStock: asset.current_stock <= 0,
      };
    })
    .filter((item) => item.currentStock <= item.threshold)
    .sort((a, b) => {
      if (a.outOfStock !== b.outOfStock) return a.outOfStock ? -1 : 1;
      const ratioA = a.threshold > 0 ? a.currentStock / a.threshold : a.currentStock;
      const ratioB = b.threshold > 0 ? b.currentStock / b.threshold : b.currentStock;
      return ratioA - ratioB || a.name.localeCompare(b.name, "es");
    });
}

// --- Incidencias --------------------------------------------------------------

export function openIncidentCount(summary: IncidentSummary): number {
  return summary.by_status.open ?? 0;
}

export function resolvedIncidentCount(summary: IncidentSummary): number {
  return summary.by_status.resolved ?? 0;
}

function timestamp(iso: string): number {
  const value = Date.parse(iso);
  return Number.isNaN(value) ? 0 : value;
}

/** Incidencias abiertas, de la más reciente a la más antigua. */
export function recentOpenIncidents(incidents: readonly Incident[], limit = ATTENTION_LIST_LIMIT): Incident[] {
  return incidents
    .filter((incident) => incident.status === "open")
    .sort((a, b) => timestamp(b.created_at) - timestamp(a.created_at))
    .slice(0, limit);
}

// --- RFPs ---------------------------------------------------------------------

export function pendingApprovalTickets(tickets: readonly RfpTicketSummary[]): RfpTicketSummary[] {
  return tickets
    .filter((ticket) => ticket.status === PENDING_APPROVAL_STATUS)
    .sort((a, b) => timestamp(b.updated_at) - timestamp(a.updated_at));
}

// --- Telemetría ---------------------------------------------------------------

export interface LoginFailureTotals {
  failed: number;
  succeeded: number;
  attempts: number;
  /** Fracción 0–1, o `null` si no hubo intentos (no se inventa un 0 %). */
  rate: number | null;
}

export function loginFailureTotals(report: TelemetryReport): LoginFailureTotals {
  let failed = 0;
  let succeeded = 0;
  for (const row of report.metrics.auth_failure_rate) {
    failed += row.failed;
    succeeded += row.succeeded;
  }
  const attempts = failed + succeeded;
  return { failed, succeeded, attempts, rate: attempts > 0 ? failed / attempts : null };
}

// --- Reporte semanal ----------------------------------------------------------

/** Mayor coste primero; las monedas no se suman entre sí. */
export function sortedPerformanceEntries(entries: readonly WeeklyPerformanceEntry[]): WeeklyPerformanceEntry[] {
  return [...entries].sort(
    (a, b) => a.office.localeCompare(b.office) || a.programme_id.localeCompare(b.programme_id),
  );
}

// --- Formato ------------------------------------------------------------------

export function formatPercent(fraction: number): string {
  return `${Math.round(fraction * 100)} %`;
}

export function formatMoney(amount: number, currency: string): string {
  try {
    return new Intl.NumberFormat("es-ES", { style: "currency", currency }).format(amount);
  } catch {
    return `${amount.toFixed(2)} ${currency}`;
  }
}

/** «12 oct», a partir de una fecha ISO. `timeZone: "UTC"` para fechas sin hora (`2026-10-12`). */
export function formatDay(iso: string, timeZone?: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("es-ES", { day: "numeric", month: "short", timeZone })
    .format(date)
    .replace(".", "");
}
