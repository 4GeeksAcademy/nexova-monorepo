import { getToken } from "./auth-storage";
import {
  type Branch,
  type Category,
  type CreateIncidentPayload,
  type IncidentField,
  type Origin,
  type Status,
  BRANCHES,
  CATEGORIES,
  ORIGINS,
  STATUSES,
  isBranch,
  isCategory,
  isOrigin,
  isStatus,
} from "./incident-manager-domain";
import { handleSessionExpired } from "./session";

/**
 * Cliente del gestor de incidencias (`/api/incident-manager`, vía los proxies
 * de `app/api/incident-manager`). Toda respuesta se valida antes de usarse.
 */

const BASE_PATH = "/api/incident-manager";
const REQUEST_TIMEOUT_MS = 15_000;

export interface Incident {
  id: number;
  title: string;
  description: string;
  category: Category;
  status: Status;
  origin: Origin;
  branch: Branch;
  created_at: string;
  updated_at: string;
}

export type Counts = Record<string, number>;

export interface IncidentSummary {
  total: number;
  by_status: Counts;
  by_category: Counts;
  by_origin: Counts;
  by_branch: Counts;
}

export interface IncidentFilters {
  status?: Status | "";
  origin?: Origin | "";
  branch?: Branch | "";
}

export const GENERIC_ERROR = "No se pudo completar la operación. Inténtalo de nuevo en unos minutos.";
export const CONNECTION_ERROR = "No se pudo conectar con el servidor. Comprueba tu conexión e inténtalo de nuevo.";

export class IncidentManagerError extends Error {
  status: number;
  /** Errores asociados a un campo concreto (400 de validación). */
  fieldErrors: Partial<Record<IncidentField, string>>;

  constructor(status: number, message: string, fieldErrors: Partial<Record<IncidentField, string>> = {}) {
    super(message);
    this.status = status;
    this.fieldErrors = fieldErrors;
  }
}

// --- Validación de respuestas ----------------------------------------------------

type JsonRecord = Record<string, unknown>;

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function str(row: JsonRecord, key: string): string {
  const value = row[key];
  if (typeof value !== "string") throw new Error(`Campo "${key}" no es texto.`);
  return value;
}

function enumValue<T extends string>(row: JsonRecord, key: string, guard: (v: unknown) => v is T): T {
  const value = row[key];
  if (!guard(value)) throw new Error(`Valor inesperado en "${key}".`);
  return value;
}

export function parseIncident(raw: unknown): Incident {
  if (!isRecord(raw)) throw new Error("Incidencia con formato inesperado.");
  const id = raw.id;
  if (typeof id !== "number" || !Number.isInteger(id)) throw new Error('Campo "id" no es un entero.');
  return {
    id,
    title: str(raw, "title"),
    description: str(raw, "description"),
    category: enumValue(raw, "category", isCategory),
    status: enumValue(raw, "status", isStatus),
    origin: enumValue(raw, "origin", isOrigin),
    branch: enumValue(raw, "branch", isBranch),
    created_at: str(raw, "created_at"),
    updated_at: str(raw, "updated_at"),
  };
}

export function parseIncidentList(raw: unknown): Incident[] {
  if (!Array.isArray(raw)) throw new Error("Lista de incidencias con formato inesperado.");
  return raw.map(parseIncident);
}

function parseCounts(raw: unknown, keys: readonly string[], label: string): Counts {
  if (!isRecord(raw)) throw new Error(`Bloque "${label}" con formato inesperado.`);
  const counts: Counts = {};
  for (const key of keys) {
    const value = raw[key];
    if (typeof value !== "number" || !Number.isInteger(value) || value < 0) {
      throw new Error(`Total inválido en "${label}.${key}".`);
    }
    counts[key] = value;
  }
  return counts;
}

export function parseSummary(raw: unknown): IncidentSummary {
  if (!isRecord(raw)) throw new Error("Resumen con formato inesperado.");
  const total = raw.total;
  if (typeof total !== "number" || !Number.isInteger(total) || total < 0) {
    throw new Error('Campo "total" inválido.');
  }
  return {
    total,
    by_status: parseCounts(raw.by_status, STATUSES, "by_status"),
    by_category: parseCounts(raw.by_category, CATEGORIES, "by_category"),
    by_origin: parseCounts(raw.by_origin, ORIGINS, "by_origin"),
    by_branch: parseCounts(raw.by_branch, BRANCHES, "by_branch"),
  };
}

/** Copia de la lista con el estado de una incidencia cambiado (actualización optimista y su rollback). */
export function withStatus(incidents: Incident[], id: number, status: Status): Incident[] {
  return incidents.map((incident) => (incident.id === id ? { ...incident, status } : incident));
}

const FORM_FIELDS: readonly IncidentField[] = ["title", "description", "category", "origin", "branch"];

/**
 * Interpreta el cuerpo de un 400 (`{"errors": [{"field", "message"}]}`). Los
 * mensajes del backend ya están en lenguaje claro; los campos desconocidos van
 * al mensaje general.
 */
export function parseErrorBody(body: unknown): { message: string; fieldErrors: Partial<Record<IncidentField, string>> } {
  const fieldErrors: Partial<Record<IncidentField, string>> = {};
  const general: string[] = [];
  if (isRecord(body) && Array.isArray(body.errors)) {
    for (const item of body.errors) {
      if (!isRecord(item) || typeof item.message !== "string") continue;
      const field = item.field;
      const known = FORM_FIELDS.find((f) => f === field);
      if (known) fieldErrors[known] = item.message;
      else general.push(item.message);
    }
  }
  const message = general[0] ?? (Object.keys(fieldErrors).length > 0 ? "Revisa los campos marcados." : "");
  return { message, fieldErrors };
}

// --- Peticiones ------------------------------------------------------------------

async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const token = getToken();
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    return await fetch(`${BASE_PATH}${path}`, {
      ...init,
      signal: controller.signal,
      headers: {
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    });
  } catch {
    throw new IncidentManagerError(0, CONNECTION_ERROR);
  } finally {
    clearTimeout(timeoutId);
  }
}

async function toError(res: Response): Promise<IncidentManagerError> {
  if (res.status === 401) {
    handleSessionExpired();
    return new IncidentManagerError(401, "Tu sesión expiró. Vuelve a iniciar sesión.");
  }
  if (res.status === 403) {
    return new IncidentManagerError(403, "Solo el personal interno de Nexova puede gestionar incidencias.");
  }
  if (res.status === 404) return new IncidentManagerError(404, "La incidencia ya no existe.");
  if (res.status >= 500) return new IncidentManagerError(res.status, GENERIC_ERROR);

  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    // cuerpo no JSON: mensaje genérico
  }
  const { message, fieldErrors } = parseErrorBody(body);
  return new IncidentManagerError(res.status, message || GENERIC_ERROR, fieldErrors);
}

async function getJson<T>(path: string, parse: (raw: unknown) => T): Promise<T> {
  const res = await request(path);
  if (!res.ok) throw await toError(res);
  try {
    return parse(await res.json());
  } catch {
    throw new IncidentManagerError(res.status, GENERIC_ERROR);
  }
}

export function listIncidents(filters: IncidentFilters = {}): Promise<Incident[]> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value) params.set(key, value);
  }
  const query = params.toString();
  return getJson(query ? `?${query}` : "", parseIncidentList);
}

export function fetchSummary(): Promise<IncidentSummary> {
  return getJson("/summary", parseSummary);
}

export async function createIncident(payload: CreateIncidentPayload): Promise<Incident> {
  const res = await request("", { method: "POST", body: JSON.stringify(payload) });
  if (!res.ok) throw await toError(res);
  try {
    return parseIncident(await res.json());
  } catch {
    throw new IncidentManagerError(res.status, GENERIC_ERROR);
  }
}

export async function updateIncidentStatus(id: number, status: Status): Promise<Incident> {
  const res = await request(`/${id}/status`, { method: "PATCH", body: JSON.stringify({ status }) });
  if (!res.ok) throw await toError(res);
  try {
    return parseIncident(await res.json());
  } catch {
    throw new IncidentManagerError(res.status, GENERIC_ERROR);
  }
}
