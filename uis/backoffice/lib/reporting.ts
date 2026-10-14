import { getToken } from "./auth-storage";
import { handleSessionExpired } from "./session";

/**
 * Cliente del Reporte Semanal de Desempeño por Oficina y Programa
 * (`GET /reporting/weekly-office-program-performance` y
 * `GET /reporting/pipeline-runs/latest`, vía los proxies de `app/api/reporting`).
 * La forma de los KPIs es el contrato del CONTEXT del pipeline.
 */

export type Office = "valencia" | "miami";
export type Currency = "EUR" | "USD";

export interface WeeklyPerformanceEntry {
  office: Office;
  programme_id: string;
  total_material_cost: number;
  kits_delivered_count: number;
  shortage_events_count: number;
  cost_variance_events_count: number;
  currency: Currency;
}

export interface WeeklyPerformanceReport {
  week_start: string;
  entries: WeeklyPerformanceEntry[];
}

export type PipelineRunStatus = "running" | "completed" | "failed" | "skipped";

export interface PipelineRunSummary {
  status: PipelineRunStatus;
  started_at: string;
  finished_at: string | null;
}

export class ReportingError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const WEEKLY_PERFORMANCE_PATH = "/api/reporting/weekly-office-program-performance";
const LATEST_RUN_PATH = "/api/reporting/pipeline-runs/latest";
const REQUEST_TIMEOUT_MS = 15_000;

// --- Validación de la respuesta (dato externo: nunca se castea sin comprobar) ---

type JsonRecord = Record<string, unknown>;

const OFFICES: readonly Office[] = ["valencia", "miami"];
const CURRENCIES: readonly Currency[] = ["EUR", "USD"];
const RUN_STATUSES: readonly PipelineRunStatus[] = ["running", "completed", "failed", "skipped"];
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function str(row: JsonRecord, key: string): string {
  const value = row[key];
  if (typeof value !== "string") throw new Error(`Campo "${key}" no es texto.`);
  return value;
}

function num(row: JsonRecord, key: string): number {
  const value = row[key];
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    throw new Error(`Campo "${key}" no es un número válido.`);
  }
  return value;
}

function oneOf<T extends string>(row: JsonRecord, key: string, allowed: readonly T[]): T {
  const value = str(row, key);
  if (!(allowed as readonly string[]).includes(value)) {
    throw new Error(`Valor inesperado en "${key}": ${value}.`);
  }
  return value as T;
}

export function parseWeeklyPerformanceReport(raw: unknown): WeeklyPerformanceReport {
  if (!isRecord(raw) || !Array.isArray(raw.entries)) {
    throw new Error("Estructura de reporte inesperada.");
  }
  const weekStart = str(raw, "week_start");
  if (!ISO_DATE.test(weekStart)) throw new Error("Semana del reporte inválida.");

  return {
    week_start: weekStart,
    entries: raw.entries.map((entry) => {
      if (!isRecord(entry)) throw new Error("Fila de reporte inválida.");
      const office = oneOf(entry, "office", OFFICES);
      const currency = oneOf(entry, "currency", CURRENCIES);
      return {
        office,
        programme_id: str(entry, "programme_id"),
        total_material_cost: num(entry, "total_material_cost"),
        kits_delivered_count: num(entry, "kits_delivered_count"),
        shortage_events_count: num(entry, "shortage_events_count"),
        cost_variance_events_count: num(entry, "cost_variance_events_count"),
        currency,
      };
    }),
  };
}

export function parsePipelineRunSummary(raw: unknown): PipelineRunSummary {
  if (!isRecord(raw)) throw new Error("Estructura de corrida inesperada.");
  const finished = raw.finished_at;
  if (finished !== null && typeof finished !== "string") {
    throw new Error('Campo "finished_at" inválido.');
  }
  return {
    status: oneOf(raw, "status", RUN_STATUSES),
    started_at: str(raw, "started_at"),
    finished_at: finished,
  };
}

// --- Peticiones -----------------------------------------------------------------

async function readErrorDetail(res: Response): Promise<string> {
  try {
    const body: unknown = await res.json();
    if (isRecord(body) && typeof body.detail === "string") return body.detail;
  } catch {
    // el cuerpo no era JSON, seguimos con el mensaje genérico
  }
  return `Error inesperado (HTTP ${res.status}).`;
}

/** GET autenticado. Devuelve `null` en 404 (todavía no hay datos). */
async function getJson<T>(url: string, parse: (raw: unknown) => T): Promise<T | null> {
  const token = getToken();
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  let res: Response;
  try {
    res = await fetch(url, {
      signal: controller.signal,
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
  } catch {
    throw new ReportingError(0, "No se pudo conectar con el servidor. Inténtalo de nuevo en unos minutos.");
  } finally {
    clearTimeout(timeoutId);
  }

  if (res.status === 401) {
    handleSessionExpired();
    throw new ReportingError(401, "Tu sesión expiró.");
  }
  if (res.status === 404) return null;
  if (!res.ok) throw new ReportingError(res.status, await readErrorDetail(res));

  try {
    return parse(await res.json());
  } catch (err) {
    throw new ReportingError(
      res.status,
      err instanceof Error ? err.message : "Respuesta del reporte inválida.",
    );
  }
}

/** KPIs de la semana (lunes `YYYY-MM-DD`) o de la última calculada si se omite. */
export function fetchWeeklyPerformance(weekStart?: string): Promise<WeeklyPerformanceReport | null> {
  const query = weekStart ? `?week_start=${encodeURIComponent(weekStart)}` : "";
  return getJson(`${WEEKLY_PERFORMANCE_PATH}${query}`, parseWeeklyPerformanceReport);
}

/** Estado de la última actualización del reporte, o `null` si nunca se generó. */
export function fetchLatestPipelineRun(): Promise<PipelineRunSummary | null> {
  return getJson(LATEST_RUN_PATH, parsePipelineRunSummary);
}
