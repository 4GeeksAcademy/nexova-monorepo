import type { RfpTicketSummary } from "./rfp";

/**
 * Piezas puras del cliente SSE de RFPs (Hito 10 · Parte 1): parser de frames,
 * backoff y validación del evento. Sin red ni `window`, para poder probarlas.
 * El `data:` del stream es dato externo: se valida antes de usarse.
 */

export const TICKET_CREATED_EVENT = "rfp_ticket_created";

export interface SseFrame {
  id: string | null;
  event: string;
  data: string;
}

export interface ParsedFrames {
  frames: SseFrame[];
  /** Texto de un frame todavía incompleto: se antepone al siguiente chunk. */
  rest: string;
}

/**
 * Separa los frames completos de un buffer SSE (separador: línea en blanco).
 * Ignora comentarios (`: keepalive`) y frames sin `data:` (p. ej. `retry:`).
 */
export function parseSseFrames(buffer: string): ParsedFrames {
  const normalized = buffer.replace(/\r\n?/g, "\n");
  const blocks = normalized.split("\n\n");
  const rest = blocks.pop() ?? "";
  const frames: SseFrame[] = [];

  for (const block of blocks) {
    let id: string | null = null;
    let event = "message";
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (line === "" || line.startsWith(":")) continue;
      const separator = line.indexOf(":");
      const field = separator === -1 ? line : line.slice(0, separator);
      const value = separator === -1 ? "" : line.slice(separator + 1).replace(/^ /, "");
      if (field === "id") id = value;
      else if (field === "event") event = value;
      else if (field === "data") data.push(value);
    }
    if (data.length > 0) frames.push({ id, event, data: data.join("\n") });
  }
  return { frames, rest };
}

export const BACKOFF_BASE_MS = 1_000;
export const BACKOFF_MAX_MS = 30_000;

/** Backoff exponencial con tope y jitter (50–100 % del valor) para no reconectar todos a la vez. */
export function backoffDelayMs(attempt: number, random: () => number = Math.random): number {
  const exponential = Math.min(BACKOFF_MAX_MS, BACKOFF_BASE_MS * 2 ** Math.max(0, attempt));
  return Math.round(exponential * (0.5 + random() * 0.5));
}

/** Payload de `rfp_ticket_created`: solo campos del ticket (sin sobre `{event, data}`). */
export interface RfpTicketCreatedEvent {
  ticket_id: string;
  rfp_id: string | null;
  client_name: string | null;
  client_hq: string | null;
  services_requested: string[];
  status: "analizando";
  created_at: string;
}

type JsonRecord = Record<string, unknown>;

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireString(row: JsonRecord, key: string): string {
  const value = row[key];
  if (typeof value !== "string" || value === "") throw new Error(`Evento sin "${key}".`);
  return value;
}

function optionalString(row: JsonRecord, key: string): string | null {
  const value = row[key];
  return typeof value === "string" && value !== "" ? value : null;
}

export function parseTicketCreatedEvent(raw: unknown): RfpTicketCreatedEvent {
  if (!isRecord(raw)) throw new Error("Evento con estructura inesperada.");
  if (raw.status !== "analizando") throw new Error('Evento con "status" inesperado.');
  const services = raw.services_requested;
  if (!Array.isArray(services) || !services.every((item) => typeof item === "string")) {
    throw new Error('Evento con "services_requested" inválido.');
  }
  return {
    ticket_id: requireString(raw, "ticket_id"),
    rfp_id: optionalString(raw, "rfp_id"),
    client_name: optionalString(raw, "client_name"),
    client_hq: optionalString(raw, "client_hq"),
    services_requested: services,
    status: "analizando",
    created_at: requireString(raw, "created_at"),
  };
}

/** Convierte un frame en evento tipado; `null` si no es de este tipo o no valida. */
export function frameToTicketCreated(frame: SseFrame): RfpTicketCreatedEvent | null {
  if (frame.event !== TICKET_CREATED_EVENT) return null;
  try {
    return parseTicketCreatedEvent(JSON.parse(frame.data));
  } catch {
    return null;
  }
}

/** Fila mínima de la bandeja a partir del evento; el poll completa el resto (nombre del PDF, etc.). */
export function summaryFromCreatedEvent(event: RfpTicketCreatedEvent): RfpTicketSummary {
  return {
    ticket_id: event.ticket_id,
    rfp_id: event.rfp_id,
    status: event.status,
    original_filename: "",
    client_name: event.client_name,
    client_hq: event.client_hq,
    currency: null,
    departments_needed: [],
    error_message: null,
    attempts: 1,
    ready_for_approval: false,
    created_at: event.created_at,
    updated_at: event.created_at,
  };
}
