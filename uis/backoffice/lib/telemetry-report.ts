import { getToken } from "./auth-storage";
import { handleSessionExpired } from "./session";

export interface ReportPeriod {
  from: string;
  to: string;
}

export interface EventsPerDayRow {
  date: string;
  event_type: string;
  count: number;
}

export interface ErrorRateRow {
  date: string;
  event_type: string;
  errors: number;
  total_events: number;
  error_rate: number;
}

export interface AuthFailureRateRow {
  date: string;
  failed: number;
  succeeded: number;
  failure_rate: number;
}

export interface TelemetryReport {
  period: ReportPeriod;
  metrics: {
    events_per_day: EventsPerDayRow[];
    error_rate_by_type: ErrorRateRow[];
    auth_failure_rate: AuthFailureRateRow[];
  };
}

export class TelemetryReportError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const REPORT_PATH = "/api/telemetry/report";
const REQUEST_TIMEOUT_MS = 15_000;

// --- Validación de la respuesta (dato externo: nunca se castea sin comprobar) ---

type JsonRecord = Record<string, unknown>;

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
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`Campo "${key}" no es numérico.`);
  }
  return value;
}

function rows<T>(value: unknown, name: string, parse: (row: JsonRecord) => T): T[] {
  if (!Array.isArray(value)) throw new Error(`La métrica "${name}" no es una lista.`);
  return value.map((row) => {
    if (!isRecord(row)) throw new Error(`Fila inválida en "${name}".`);
    return parse(row);
  });
}

export function parseTelemetryReport(raw: unknown): TelemetryReport {
  if (!isRecord(raw) || !isRecord(raw.period) || !isRecord(raw.metrics)) {
    throw new Error("Estructura de reporte inesperada.");
  }
  const { period, metrics } = raw;
  return {
    period: { from: str(period, "from"), to: str(period, "to") },
    metrics: {
      events_per_day: rows(metrics.events_per_day, "events_per_day", (r) => ({
        date: str(r, "date"),
        event_type: str(r, "event_type"),
        count: num(r, "count"),
      })),
      error_rate_by_type: rows(metrics.error_rate_by_type, "error_rate_by_type", (r) => ({
        date: str(r, "date"),
        event_type: str(r, "event_type"),
        errors: num(r, "errors"),
        total_events: num(r, "total_events"),
        error_rate: num(r, "error_rate"),
      })),
      auth_failure_rate: rows(metrics.auth_failure_rate, "auth_failure_rate", (r) => ({
        date: str(r, "date"),
        failed: num(r, "failed"),
        succeeded: num(r, "succeeded"),
        failure_rate: num(r, "failure_rate"),
      })),
    },
  };
}

async function readErrorDetail(res: Response): Promise<string> {
  try {
    const body: unknown = await res.json();
    if (isRecord(body) && typeof body.detail === "string") return body.detail;
  } catch {
    // el cuerpo no era JSON, seguimos con el mensaje genérico
  }
  return `Error inesperado (HTTP ${res.status}).`;
}

export interface ReportRange {
  startDate?: string;
  endDate?: string;
}

export async function fetchTelemetryReport(range: ReportRange = {}): Promise<TelemetryReport> {
  const params = new URLSearchParams();
  if (range.startDate) params.set("start_date", range.startDate);
  if (range.endDate) params.set("end_date", range.endDate);
  const query = params.toString();

  const token = getToken();
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  let res: Response;
  try {
    res = await fetch(`${REPORT_PATH}${query ? `?${query}` : ""}`, {
      signal: controller.signal,
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
  } catch {
    throw new TelemetryReportError(0, "No se pudo conectar con la API. Comprueba que esté en marcha.");
  } finally {
    clearTimeout(timeoutId);
  }

  if (res.status === 401) {
    handleSessionExpired();
    throw new TelemetryReportError(401, "Sesión expirada.");
  }
  if (!res.ok) {
    throw new TelemetryReportError(res.status, await readErrorDetail(res));
  }

  try {
    return parseTelemetryReport(await res.json());
  } catch (err) {
    throw new TelemetryReportError(
      res.status,
      err instanceof Error ? err.message : "Respuesta de reporte inválida.",
    );
  }
}
