import {
  backoffDelayMs,
  frameToTicketCreated,
  parseSseFrames,
  type RfpTicketCreatedEvent,
} from "./sse";

/**
 * Cliente del stream SSE de RFPs con `fetch` + `ReadableStream` (no `EventSource`:
 * no permite enviar `Authorization`). Reconecta con backoff progresivo y, al
 * volver, manda `Last-Event-ID` para que el servidor reenvíe lo que se perdió.
 * Las dependencias (red, token, temporizadores) se inyectan para poder probarlo.
 */

export type StreamState = "connecting" | "live" | "reconnecting" | "forbidden";

export const STREAM_URL = "/api/rfp/events";
/** El servidor emite un keep-alive cada 15 s: sin bytes en 3 ciclos, la conexión está muerta. */
export const STALL_TIMEOUT_MS = 45_000;

export interface RfpStreamOptions {
  signal: AbortSignal;
  onEvent: (event: RfpTicketCreatedEvent) => void;
  onState: (state: StreamState) => void;
  getToken: () => string | null;
  onUnauthorized: () => void;
  fetchImpl?: typeof fetch;
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
  random?: () => number;
  stallTimeoutMs?: number;
}

export function abortableSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const timer = setTimeout(done, ms);
    function done() {
      clearTimeout(timer);
      signal.removeEventListener("abort", done);
      resolve();
    }
    signal.addEventListener("abort", done);
  });
}

/** Lee una conexión hasta que se corte. Devuelve cuando el stream termina o se aborta. */
async function readStream(
  body: ReadableStream<Uint8Array>,
  connection: AbortController,
  stallTimeoutMs: number,
  onFrameBytes: () => void,
  onFrames: (frames: ReturnType<typeof parseSseFrames>["frames"]) => void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let stall: ReturnType<typeof setTimeout> | undefined;
  const armStall = () => {
    clearTimeout(stall);
    stall = setTimeout(() => connection.abort(), stallTimeoutMs);
  };

  armStall();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return;
      armStall();
      onFrameBytes();
      const parsed = parseSseFrames(buffer + decoder.decode(value, { stream: true }));
      buffer = parsed.rest;
      onFrames(parsed.frames);
    }
  } finally {
    clearTimeout(stall);
    reader.cancel().catch(() => undefined);
  }
}

export async function connectRfpStream(options: RfpStreamOptions): Promise<void> {
  const {
    signal,
    onEvent,
    onState,
    getToken,
    onUnauthorized,
    fetchImpl = fetch,
    sleep = abortableSleep,
    random = Math.random,
    stallTimeoutMs = STALL_TIMEOUT_MS,
  } = options;

  let attempt = 0;
  let lastEventId: string | null = null;

  while (!signal.aborted) {
    onState(attempt === 0 && lastEventId === null ? "connecting" : "reconnecting");

    // Una conexión por intento; abortar el efecto (signal) la corta también.
    const connection = new AbortController();
    const abortConnection = () => connection.abort();
    signal.addEventListener("abort", abortConnection);

    try {
      const headers: Record<string, string> = { Accept: "text/event-stream" };
      const token = getToken();
      if (token) headers.Authorization = `Bearer ${token}`;
      if (lastEventId) headers["Last-Event-ID"] = lastEventId;

      const response = await fetchImpl(STREAM_URL, { headers, signal: connection.signal, cache: "no-store" });
      if (response.status === 401) {
        onUnauthorized();
        return;
      }
      if (response.status === 403) {
        onState("forbidden"); // no es transitorio: reintentar no lo arregla
        return;
      }
      if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);

      onState("live");
      await readStream(
        response.body,
        connection,
        stallTimeoutMs,
        () => {
          attempt = 0; // hubo tráfico: la próxima caída vuelve a empezar desde el backoff mínimo
        },
        (frames) => {
          for (const frame of frames) {
            if (frame.id) lastEventId = frame.id;
            const event = frameToTicketCreated(frame);
            if (event) onEvent(event);
          }
        },
      );
    } catch {
      // Red caída, 5xx, corte o stall: se reconecta tras el backoff.
    } finally {
      signal.removeEventListener("abort", abortConnection);
    }

    if (signal.aborted) return;
    onState("reconnecting");
    await sleep(backoffDelayMs(attempt, random), signal);
    attempt += 1;
  }
}
