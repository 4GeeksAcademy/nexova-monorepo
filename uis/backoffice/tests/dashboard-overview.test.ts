import { describe, expect, it } from "vitest";
import {
  DEFAULT_LOW_STOCK_THRESHOLD,
  formatDay,
  formatMoney,
  formatPercent,
  loginFailureTotals,
  lowStockItems,
  openIncidentCount,
  pendingApprovalTickets,
  recentOpenIncidents,
  sortedPerformanceEntries,
  stockThreshold,
} from "../lib/dashboard-overview";
import type { Incident, IncidentSummary } from "../lib/incident-manager";
import type { Asset } from "../lib/inventory-types";
import type { WeeklyPerformanceEntry } from "../lib/reporting";
import type { RfpTicketSummary } from "../lib/rfp";
import type { TelemetryReport } from "../lib/telemetry-report";

function asset(overrides: Partial<Asset> & Pick<Asset, "id" | "current_stock">): Asset {
  return {
    name: `Producto ${overrides.id}`,
    sku: `SKU-${overrides.id}`,
    category: "hardware",
    office: "Valencia",
    ...overrides,
  };
}

describe("stock bajo", () => {
  it("usa el umbral del producto y, si falta o no es válido, el valor por defecto", () => {
    expect(stockThreshold(asset({ id: 1, current_stock: 3, min_stock_threshold: 10 }))).toBe(10);
    expect(stockThreshold(asset({ id: 2, current_stock: 3 }))).toBe(DEFAULT_LOW_STOCK_THRESHOLD);
    expect(stockThreshold(asset({ id: 3, current_stock: 3, min_stock_threshold: -1 }))).toBe(DEFAULT_LOW_STOCK_THRESHOLD);
    expect(stockThreshold(asset({ id: 4, current_stock: 3, min_stock_threshold: Number.NaN }))).toBe(
      DEFAULT_LOW_STOCK_THRESHOLD,
    );
  });

  it("incluye los productos en o bajo su umbral y excluye los saludables", () => {
    const items = lowStockItems([
      asset({ id: 1, current_stock: 5, min_stock_threshold: 5 }),
      asset({ id: 2, current_stock: 6, min_stock_threshold: 5 }),
      asset({ id: 3, current_stock: 9, min_stock_threshold: 10 }),
    ]);
    expect(items.map((i) => i.id).sort()).toEqual([1, 3]);
  });

  it("ordena primero los productos sin stock y luego por menor ratio", () => {
    const items = lowStockItems([
      asset({ id: 1, current_stock: 4, min_stock_threshold: 5 }),
      asset({ id: 2, current_stock: 1, min_stock_threshold: 5 }),
      asset({ id: 3, current_stock: 0, min_stock_threshold: 5 }),
    ]);
    expect(items.map((i) => i.id)).toEqual([3, 2, 1]);
    expect(items[0].outOfStock).toBe(true);
    expect(items[1].outOfStock).toBe(false);
  });

  it("ignora productos con stock no numérico en lugar de romper", () => {
    const broken = { ...asset({ id: 1, current_stock: 1 }), current_stock: Number.NaN };
    expect(lowStockItems([broken])).toEqual([]);
  });
});

describe("incidencias", () => {
  const summary = (by_status: Record<string, number>): IncidentSummary => ({
    total: 0,
    by_status,
    by_category: {},
    by_origin: {},
    by_branch: {},
  });

  it("cuenta solo las abiertas y tolera que falte la clave", () => {
    expect(openIncidentCount(summary({ open: 27, resolved: 57 }))).toBe(27);
    expect(openIncidentCount(summary({}))).toBe(0);
  });

  it("lista las abiertas, la más reciente primero y con límite", () => {
    const incident = (id: number, status: Incident["status"], created_at: string): Incident => ({
      id,
      title: `Incidencia ${id}`,
      description: "x",
      category: "other",
      status,
      origin: "internal",
      branch: "central",
      created_at,
      updated_at: created_at,
    });
    const result = recentOpenIncidents(
      [
        incident(1, "open", "2026-11-01T10:00:00Z"),
        incident(2, "resolved", "2026-11-05T10:00:00Z"),
        incident(3, "open", "2026-11-03T10:00:00Z"),
        incident(4, "open", "2026-11-02T10:00:00Z"),
      ],
      2,
    );
    expect(result.map((i) => i.id)).toEqual([3, 4]);
  });
});

describe("RFPs", () => {
  it("devuelve solo los tickets esperando aprobación, el más reciente primero", () => {
    const ticket = (id: string, status: RfpTicketSummary["status"], updated_at: string): RfpTicketSummary => ({
      ticket_id: id,
      rfp_id: null,
      status,
      original_filename: `${id}.pdf`,
      client_name: null,
      client_hq: null,
      currency: null,
      departments_needed: [],
      error_message: null,
      attempts: 1,
      ready_for_approval: true,
      created_at: updated_at,
      updated_at,
    });
    const result = pendingApprovalTickets([
      ticket("A", "esperando_aprobación", "2026-11-10T00:00:00Z"),
      ticket("B", "terminado", "2026-11-12T00:00:00Z"),
      ticket("C", "esperando_aprobación", "2026-11-11T00:00:00Z"),
    ]);
    expect(result.map((t) => t.ticket_id)).toEqual(["C", "A"]);
  });
});

describe("fallos de inicio de sesión", () => {
  const report = (rows: Array<{ failed: number; succeeded: number }>): TelemetryReport => ({
    period: { from: "2026-11-11", to: "2026-11-18" },
    metrics: {
      events_per_day: [],
      error_rate_by_type: [],
      auth_failure_rate: rows.map((r, i) => ({ date: `2026-11-1${i}`, ...r, failure_rate: 0 })),
    },
  });

  it("suma los días y calcula la tasa global", () => {
    const totals = loginFailureTotals(report([{ failed: 10, succeeded: 30 }, { failed: 4, succeeded: 14 }]));
    expect(totals).toEqual({ failed: 14, succeeded: 44, attempts: 58, rate: 14 / 58 });
  });

  it("sin intentos no inventa un 0 %", () => {
    expect(loginFailureTotals(report([])).rate).toBeNull();
  });
});

describe("reporte semanal y formato", () => {
  it("ordena por oficina y programa sin mezclar monedas", () => {
    const entry = (office: "valencia" | "miami", programme_id: string, currency: "EUR" | "USD"): WeeklyPerformanceEntry => ({
      office,
      programme_id,
      total_material_cost: 1,
      kits_delivered_count: 0,
      shortage_events_count: 0,
      cost_variance_events_count: 0,
      currency,
    });
    const sorted = sortedPerformanceEntries([
      entry("valencia", "b2b-sales", "EUR"),
      entry("miami", "onboarding", "USD"),
    ]);
    expect(sorted.map((e) => e.office)).toEqual(["miami", "valencia"]);
  });

  it("formatea porcentajes, importes y fechas", () => {
    expect(formatPercent(14 / 58)).toBe("24 %");
    expect(formatMoney(185, "EUR")).toContain("185");
    expect(formatMoney(185, "EUR")).toContain("€");
    expect(formatDay("2026-10-12", "UTC")).toBe("12 oct");
    expect(formatDay("no es una fecha")).toBe("—");
  });
});
