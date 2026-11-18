import { describe, expect, it } from "vitest";
import { connectRfpStream, type StreamState } from "../lib/rfp-stream";
import {
  BACKOFF_BASE_MS,
  BACKOFF_MAX_MS,
  backoffDelayMs,
  frameToTicketCreated,
  parseSseFrames,
  parseTicketCreatedEvent,
  summaryFromCreatedEvent,
  type RfpTicketCreatedEvent,
} from "../lib/sse";

const PAYLOAD = {
  ticket_id: "RFP-0341",
  rfp_id: "rfp_0127",
  client_name: "NubeSoft",
  client_hq: "Miami",
  services_requested: ["soporte"],
  status: "analizando",
  created_at: "2026-07-24T14:32:00Z",
};

const frame = (id: string, payload: object = PAYLOAD) =>
  `id: ${id}\nevent: rfp_ticket_created\ndata: ${JSON.stringify(payload)}\n\n`;

describe("parseSseFrames", () => {
  it("lee id, event y data, e ignora comentarios keep-alive y retry", () => {
    const { frames, rest } = parseSseFrames(`retry: 3000\n\n: keepalive\n\n${frame("1-0")}`);
    expect(rest).toBe("");
    expect(frames).toHaveLength(1);
    expect(frames[0]).toMatchObject({ id: "1-0", event: "rfp_ticket_created" });
    expect(JSON.parse(frames[0].data)).toEqual(PAYLOAD);
  });

  it("conserva un frame partido entre chunks", () => {
    const whole = frame("2-0");
    const cut = Math.floor(whole.length / 2);
    const first = parseSseFrames(whole.slice(0, cut));
    expect(first.frames).toHaveLength(0);
    const second = parseSseFrames(first.rest + whole.slice(cut));
    expect(second.frames).toHaveLength(1);
  });

  it("acepta CRLF", () => {
    expect(parseSseFrames(frame("3-0").replace(/\n/g, "\r\n")).frames).toHaveLength(1);
  });
});

describe("evento rfp_ticket_created", () => {
  it("valida el payload y rechaza lo inesperado", () => {
    expect(parseTicketCreatedEvent(PAYLOAD).ticket_id).toBe("RFP-0341");
    expect(() => parseTicketCreatedEvent({ ...PAYLOAD, ticket_id: "" })).toThrow();
    expect(() => parseTicketCreatedEvent({ ...PAYLOAD, status: "analyzing" })).toThrow();
    expect(() => parseTicketCreatedEvent({ ...PAYLOAD, services_requested: "soporte" })).toThrow();
    expect(() => parseTicketCreatedEvent(null)).toThrow();
  });

  it("ignora otros eventos y JSON inválido", () => {
    expect(frameToTicketCreated({ id: "1-0", event: "message", data: JSON.stringify(PAYLOAD) })).toBeNull();
    expect(frameToTicketCreated({ id: "1-0", event: "rfp_ticket_created", data: "{no-json" })).toBeNull();
  });

  it("la fila de la bandeja conserva los nombres del ticket", () => {
    const row = summaryFromCreatedEvent(parseTicketCreatedEvent(PAYLOAD));
    expect(row).toMatchObject({ ticket_id: "RFP-0341", status: "analizando", client_name: "NubeSoft" });
  });
});

describe("backoffDelayMs", () => {
  it("crece de forma exponencial, con tope y jitter", () => {
    const max = (attempt: number) => backoffDelayMs(attempt, () => 1);
    expect(max(0)).toBe(BACKOFF_BASE_MS);
    expect(max(1)).toBe(BACKOFF_BASE_MS * 2);
    expect(max(2)).toBe(BACKOFF_BASE_MS * 4);
    expect(max(20)).toBe(BACKOFF_MAX_MS);
    expect(backoffDelayMs(3, () => 0)).toBe((BACKOFF_BASE_MS * 8) / 2);
  });
});

// --- Reconexión + recuperación: red simulada ---------------------------------------------
function streamOf(text: string) {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode(text));
      controller.close();
    },
  });
}

describe("connectRfpStream", () => {
  it("reconecta con backoff creciente, reenvía Last-Event-ID y no duplica el ticket repetido", async () => {
    const controller = new AbortController();
    const requests: Record<string, string>[] = [];
    const delays: number[] = [];
    const states: StreamState[] = [];
    const received: RfpTicketCreatedEvent[] = [];
    let call = 0;

    const fetchImpl = (async (_url: string, init: RequestInit) => {
      requests.push(init.headers as Record<string, string>);
      call += 1;
      if (call === 1) return new Response(streamOf(frame("10-0")), { status: 200 });
      if (call === 2) throw new TypeError("red caída");
      if (call === 3) return new Response("boom", { status: 503 });
      // Tras la caída el servidor repite 10-0 y añade 11-0 (replay desde Last-Event-ID).
      if (call === 4) {
        return new Response(streamOf(frame("10-0") + frame("11-0", { ...PAYLOAD, ticket_id: "RFP-0342" })), {
          status: 200,
        });
      }
      controller.abort(); // la persona cierra la página: no debe haber más intentos
      return new Response(streamOf(""), { status: 200 });
    }) as typeof fetch;

    await connectRfpStream({
      signal: controller.signal,
      getToken: () => "jwt",
      onUnauthorized: () => undefined,
      fetchImpl,
      random: () => 1,
      sleep: async (ms) => {
        delays.push(ms);
      },
      onState: (s) => states.push(s),
      onEvent: (e) => received.push(e),
    });

    expect(requests[0]["Authorization"]).toBe("Bearer jwt");
    expect(requests[0]["Last-Event-ID"]).toBeUndefined();
    expect(requests[1]["Last-Event-ID"]).toBe("10-0");
    expect(requests[3]["Last-Event-ID"]).toBe("10-0");
    // La primera conexión recibió tráfico (attempt = 0); luego fallan dos seguidas: 1 s → 2 s → 4 s.
    expect(delays.slice(0, 3)).toEqual([BACKOFF_BASE_MS, BACKOFF_BASE_MS * 2, BACKOFF_BASE_MS * 4]);
    expect(states).toContain("reconnecting");
    expect(states).toContain("live");
    // El cliente entrega todo lo recibido; la deduplicación por ticket_id vive en el hook.
    expect(received.map((e) => e.ticket_id)).toEqual(["RFP-0341", "RFP-0341", "RFP-0342"]);
  });

  it("401 cierra sesión sin reintentar y 403 no reintenta", async () => {
    let unauthorized = 0;
    const states: StreamState[] = [];
    let calls = 0;
    const base = {
      signal: new AbortController().signal,
      getToken: () => "jwt",
      onUnauthorized: () => {
        unauthorized += 1;
      },
      sleep: async () => undefined,
      onEvent: () => undefined,
      onState: (s: StreamState) => states.push(s),
    };

    await connectRfpStream({ ...base, fetchImpl: (async () => (calls++, new Response("", { status: 401 }))) as typeof fetch });
    expect(unauthorized).toBe(1);
    expect(calls).toBe(1);

    await connectRfpStream({ ...base, fetchImpl: (async () => (calls++, new Response("", { status: 403 }))) as typeof fetch });
    expect(states).toContain("forbidden");
    expect(calls).toBe(2);
  });
});
