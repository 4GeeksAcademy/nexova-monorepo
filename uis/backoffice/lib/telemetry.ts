/**
 * TelemetryService — único punto de entrada para capturar telemetría en el
 * backoffice. Ver docs/telemetry/telemetry-plan.md y event-schemas.json.
 *
 * Nadie más debe llamar fetch/axios/sendBeacon para telemetría: todo pasa
 * por `track()`.
 */

export const TELEMETRY_SCHEMA_VERSION = "1.0.0";

const FLUSH_INTERVAL_MS = 10_000;
const MAX_BATCH_SIZE = 20;
const MAX_RETRIES = 3;
const RETRY_BASE_DELAY_MS = 1_000;

interface TelemetryEvent {
  eventId: string;
  timestamp: string;
  event_type: string;
  schemaVersion: string;
  sessionId: string | null;
  userId: string | null;
  requestId: string | null;
  source: "frontend";
  properties: Record<string, unknown>;
}

let sessionId: string | null = null;
let userId: string | null = null;
let queue: TelemetryEvent[] = [];
let flushTimer: ReturnType<typeof setTimeout> | null = null;

function getEndpoint(): string | null {
  const endpoint = process.env.NEXT_PUBLIC_TELEMETRY_ENDPOINT;
  return endpoint && endpoint.length > 0 ? endpoint : null;
}

/** Genera un sessionId nuevo. Llamar una vez, tras un login exitoso. */
export function startTelemetrySession(): void {
  sessionId = crypto.randomUUID();
}

/** Sincroniza el userId conocido (o null si no hay sesión autenticada). */
export function setTelemetryUser(id: string | null): void {
  userId = id;
}

function scheduleFlush(): void {
  if (flushTimer !== null) {
    return;
  }
  flushTimer = setTimeout(() => {
    flushTimer = null;
    void flush(false);
  }, FLUSH_INTERVAL_MS);
}

function clearScheduledFlush(): void {
  if (flushTimer !== null) {
    clearTimeout(flushTimer);
    flushTimer = null;
  }
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function sendWithRetry(batch: TelemetryEvent[], endpoint: string): Promise<void> {
  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    try {
      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ events: batch }),
      });
      if (res.ok) {
        return;
      }
    } catch {
      // red caída o CORS — se reintenta según el backoff de abajo
    }
    if (attempt < MAX_RETRIES - 1) {
      await delay(RETRY_BASE_DELAY_MS * 2 ** attempt);
    }
  }
  console.warn(`[telemetry] descartado lote de ${batch.length} evento(s) tras ${MAX_RETRIES} intentos.`);
}

async function flush(useBeacon: boolean): Promise<void> {
  clearScheduledFlush();
  if (queue.length === 0) {
    return;
  }
  const endpoint = getEndpoint();
  if (!endpoint) {
    queue = [];
    return;
  }

  const batch = queue.splice(0, queue.length);

  if (useBeacon && typeof navigator !== "undefined" && typeof navigator.sendBeacon === "function") {
    const blob = new Blob([JSON.stringify({ events: batch })], { type: "application/json" });
    navigator.sendBeacon(endpoint, blob);
    return;
  }

  await sendWithRetry(batch, endpoint);
}

/** Fuerza el envío del lote pendiente vía sendBeacon (página ocultándose/cerrándose). */
export function flushOnHide(): void {
  void flush(true);
}

const ERROR_DEBOUNCE_MS = 60_000;
const errorDebounce = new Map<string, { lastSentAt: number; occurrenceCount: number }>();

function currentRoute(): string {
  return window.location.pathname;
}

function firstOwnStackFrame(stack: string | undefined): string {
  if (!stack) {
    return "";
  }
  const line = stack
    .split("\n")
    .slice(1)
    .find((frame) => !frame.includes("node_modules"));
  return (line ?? "").trim();
}

function fingerprint(message: string, stack: string | undefined): string {
  const raw = `${message}|${firstOwnStackFrame(stack)}`;
  let hash = 0;
  for (let i = 0; i < raw.length; i++) {
    hash = (hash * 31 + raw.charCodeAt(i)) | 0;
  }
  return hash.toString(36);
}

/**
 * Captura un error de JS no manejado (window.onerror / unhandledrejection).
 * Debounce por fingerprint: máx. 1 evento cada 60s por error+sesión, las
 * repeticiones se acumulan en `occurrence_count`.
 */
export function trackFrontendError(message: string, stack?: string): void {
  const errorFingerprint = fingerprint(message, stack);
  const now = Date.now();
  const existing = errorDebounce.get(errorFingerprint);

  if (existing && now - existing.lastSentAt < ERROR_DEBOUNCE_MS) {
    existing.occurrenceCount += 1;
    return;
  }

  const occurrenceCount = existing ? existing.occurrenceCount + 1 : 1;
  errorDebounce.set(errorFingerprint, { lastSentAt: now, occurrenceCount: 0 });

  track("frontend_error_captured", {
    route: currentRoute(),
    error_fingerprint: errorFingerprint,
    error_message: message.slice(0, 300),
    severity: "recoverable",
    occurrence_count: occurrenceCount,
  });
}

/**
 * Único punto de entrada de telemetría del backoffice.
 * `eventType` se convierte en `event_type` del envelope; el resto de los
 * campos del envelope se completan automáticamente.
 */
export function track(eventType: string, properties: Record<string, unknown>): void {
  if (typeof window === "undefined") {
    return;
  }

  const event: TelemetryEvent = {
    eventId: crypto.randomUUID(),
    timestamp: new Date().toISOString(),
    event_type: eventType,
    schemaVersion: TELEMETRY_SCHEMA_VERSION,
    sessionId,
    userId,
    requestId: crypto.randomUUID(),
    source: "frontend",
    properties,
  };

  const wasEmpty = queue.length === 0;
  queue.push(event);

  if (queue.length >= MAX_BATCH_SIZE) {
    void flush(false);
  } else if (wasEmpty) {
    scheduleFlush();
  }
}
