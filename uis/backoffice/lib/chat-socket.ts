import { abortableSleep } from "./rfp-stream";
import { backoffDelayMs } from "./sse";
import { interruptFrame, parseServerEvent, userMessageFrame, type ServerEvent } from "./chat-protocol";

/**
 * Cliente WebSocket del chat de soporte.
 *
 * - Se conecta directo a la API (los route handlers de Next no hacen WebSocket)
 *   con el mismo JWT del backoffice en `?token=` (los navegadores no permiten
 *   poner `Authorization` en el handshake).
 * - Tras (re)conectar, NO entrega tokens hasta recibir `session_snapshot`: así
 *   la conversación se rehidrata desde el servidor antes de seguir el stream.
 * - Reconexión con backoff exponencial y jitter (`backoffDelayMs`, 1→30 s),
 *   que vuelve a empezar al recibir el snapshot.
 * - 4401: sesión caducada (no reintenta); 4403 / 4404: sin reintento.
 * Las dependencias (socket, token, temporizadores) se inyectan para probarlo.
 */

export type ChatConnectionState =
  | "connecting"
  | "rehydrating"
  | "live"
  | "reconnecting"
  | "unauthorized"
  | "forbidden"
  | "not_found";

export const CLOSE_UNAUTHORIZED = 4401;
export const CLOSE_FORBIDDEN = 4403;
export const CLOSE_NOT_FOUND = 4404;
const OPEN = 1;

/** Lo mínimo de `WebSocket` que usa el cliente (el real cumple esta interfaz). */
export interface SocketLike {
  readonly readyState: number;
  send(data: string): void;
  close(code?: number, reason?: string): void;
  onopen: ((event: Event) => void) | null;
  onmessage: ((event: MessageEvent) => void) | null;
  onclose: ((event: CloseEvent) => void) | null;
  onerror: ((event: Event) => void) | null;
}

export interface ChatSocketOptions {
  sessionId: string;
  signal: AbortSignal;
  getToken: () => string | null;
  onEvent: (event: ServerEvent) => void;
  onState: (state: ChatConnectionState) => void;
  onUnauthorized: () => void;
  baseUrl?: string;
  createSocket?: (url: string) => SocketLike;
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
  random?: () => number;
}

export interface ChatSocket {
  /** Envía un turno del usuario. `false` si no hay conexión rehidratada. */
  sendUserMessage(text: string): boolean;
  /** Interrumpe la respuesta en curso y la redirige con `newInput`. */
  sendInterrupt(newInput: string): boolean;
}

/** URL base del WebSocket: `NEXT_PUBLIC_CHAT_WS_URL` o la API en el puerto 8000 del mismo host. */
export function defaultChatWsBaseUrl(): string {
  const configured = process.env.NEXT_PUBLIC_CHAT_WS_URL;
  if (configured) return configured.replace(/\/$/, "");
  if (typeof window === "undefined") return "ws://localhost:8000";
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  return `${protocol}://${window.location.hostname}:8000`;
}

export function chatSocketUrl(baseUrl: string, sessionId: string, token: string): string {
  return `${baseUrl}/chat/ws/${encodeURIComponent(sessionId)}?token=${encodeURIComponent(token)}`;
}

export function connectChatSocket(options: ChatSocketOptions): ChatSocket {
  const {
    sessionId,
    signal,
    getToken,
    onEvent,
    onState,
    onUnauthorized,
    baseUrl = defaultChatWsBaseUrl(),
    createSocket = (url: string) => new WebSocket(url),
    sleep = abortableSleep,
    random = Math.random,
  } = options;

  let socket: SocketLike | null = null;
  let hydrated = false;
  // La última conexión llegó a rehidratarse: el backoff vuelve a empezar.
  let reachedLive = false;

  function openOnce(token: string, reconnecting: boolean): Promise<number> {
    return new Promise((resolve) => {
      hydrated = false;
      onState(reconnecting ? "reconnecting" : "connecting");
      const ws = createSocket(chatSocketUrl(baseUrl, sessionId, token));
      socket = ws;
      const onAbort = () => ws.close(1000, "client_closed");
      signal.addEventListener("abort", onAbort, { once: true });

      ws.onopen = () => onState("rehydrating");
      ws.onmessage = (message) => {
        if (typeof message.data !== "string") return;
        let raw: unknown;
        try {
          raw = JSON.parse(message.data);
        } catch {
          return;
        }
        const event = parseServerEvent(raw);
        if (!event) return;
        if (!hydrated) {
          // Hasta tener el snapshot no se aplica nada: el hilo se restaura primero.
          if (event.event !== "session_snapshot") return;
          hydrated = true;
          reachedLive = true;
          onState("live");
        }
        onEvent(event);
      };
      ws.onerror = () => {
        /* el cierre llega por onclose */
      };
      ws.onclose = (event) => {
        signal.removeEventListener("abort", onAbort);
        if (socket === ws) socket = null;
        hydrated = false;
        resolve(event.code);
      };
    });
  }

  async function run(): Promise<void> {
    let attempt = 0;
    let reconnecting = false;
    while (!signal.aborted) {
      const token = getToken();
      if (!token) {
        onState("unauthorized");
        onUnauthorized();
        return;
      }
      reachedLive = false;
      const code = await openOnce(token, reconnecting);
      if (signal.aborted) return;
      if (reachedLive) attempt = 0;
      if (code === CLOSE_UNAUTHORIZED) {
        onState("unauthorized");
        onUnauthorized();
        return;
      }
      if (code === CLOSE_FORBIDDEN) return onState("forbidden");
      if (code === CLOSE_NOT_FOUND) return onState("not_found");
      reconnecting = true;
      onState("reconnecting");
      await sleep(backoffDelayMs(attempt, random), signal);
      attempt += 1;
    }
  }

  void run();

  function send(frame: string): boolean {
    const ws = socket;
    if (!ws || !hydrated || ws.readyState !== OPEN) return false;
    ws.send(frame);
    return true;
  }

  return {
    sendUserMessage: (text) => send(userMessageFrame(sessionId, text)),
    sendInterrupt: (newInput) => send(interruptFrame(sessionId, newInput)),
  };
}
