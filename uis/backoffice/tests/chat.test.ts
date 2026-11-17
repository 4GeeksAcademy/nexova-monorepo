import { describe, expect, it } from "vitest";
import {
  chatReducer,
  INITIAL_CHAT_STATE,
  interruptFrame,
  isGenerating,
  parseServerEvent,
  type ChatState,
  type ServerEvent,
} from "../lib/chat-protocol";
import { connectChatSocket, type ChatConnectionState, type SocketLike } from "../lib/chat-socket";

const SID = "6f1c2c1e-0000-4000-8000-000000000001";

const raw = (event: string, data: object) => ({ event, data: { session_id: SID, ...data } });

function apply(state: ChatState, ...frames: object[]): ChatState {
  return frames.reduce<ChatState>((acc, frame) => {
    const event = parseServerEvent(frame);
    if (!event) throw new Error(`frame inválido: ${JSON.stringify(frame)}`);
    return chatReducer(acc, event);
  }, state);
}

const token = (sequence: number, text: string, messageId = "msg_a") =>
  raw("token_chunk", { message_id: messageId, token: text, sequence });

describe("parseServerEvent", () => {
  it("valida el contrato y descarta frames incompletos o desconocidos", () => {
    expect(parseServerEvent(token(1, "Hola"))).toEqual({
      event: "token_chunk",
      sessionId: SID,
      messageId: "msg_a",
      token: "Hola",
      sequence: 1,
    });
    expect(parseServerEvent(raw("token_chunk", { message_id: "m", token: "x", sequence: 0 }))).toBeNull();
    expect(parseServerEvent(raw("token_chunk", { token: "x", sequence: 1 }))).toBeNull();
    expect(parseServerEvent(raw("generation_interrupted", { message_id: "m", status: "completed" }))).toBeNull();
    expect(parseServerEvent(raw("rfp_ticket_created", { ticket_id: "RFP-1" }))).toBeNull();
    expect(parseServerEvent("no es json")).toBeNull();
    expect(
      parseServerEvent(raw("session_snapshot", { status: "active", messages: [{ message_id: "m" }] })),
    ).toBeNull();
  });
});

describe("chatReducer", () => {
  it("escribe la respuesta token a token y deduplica por sequence", () => {
    const state = apply(
      INITIAL_CHAT_STATE,
      raw("user_message", { message_id: "msg_u", text: "¿Mi factura?" }),
      token(1, "Tu "),
      token(2, "factura "),
      token(2, "factura "), // repetido
      token(1, "Tu "), // atrasado
      token(3, "vence el 30."),
    );
    expect(state.messages.map((m) => [m.role, m.content, m.status])).toEqual([
      ["user", "¿Mi factura?", "completed"],
      ["assistant", "Tu factura vence el 30.", "streaming"],
    ]);
    expect(isGenerating(state)).toBe(true);
    const done = apply(state, raw("generation_completed", { message_id: "msg_a" }));
    expect(done.messages[1].status).toBe("completed");
    expect(isGenerating(done)).toBe(false);
  });

  it("una interrupción conserva el parcial y la respuesta redirigida es otro mensaje", () => {
    const state = apply(
      INITIAL_CHAT_STATE,
      raw("user_message", { message_id: "msg_u1", text: "¿Cómo cambio el envío?" }),
      token(1, "Para cambiar "),
      token(2, "el envío "),
      raw("generation_interrupted", { message_id: "msg_a", status: "interrupted" }),
      token(3, "debes…"), // de la generación abortada: se ignora
      raw("user_message", { message_id: "msg_u2", text: "era sobre la factura" }),
      token(1, "Sobre la factura: ", "msg_b"),
      raw("generation_completed", { message_id: "msg_b" }),
    );
    expect(state.messages.map((m) => [m.messageId, m.content, m.status])).toEqual([
      ["msg_u1", "¿Cómo cambio el envío?", "completed"],
      ["msg_a", "Para cambiar el envío ", "interrupted"],
      ["msg_u2", "era sobre la factura", "completed"],
      ["msg_b", "Sobre la factura: ", "completed"],
    ]);
  });

  it("generation_completed con content reemplaza el texto mostrado (guard de salida)", () => {
    const state = apply(
      INITIAL_CHAT_STATE,
      token(1, "Dato de otra empresa"),
      raw("generation_completed", { message_id: "msg_a", content: "No puedo compartir eso." }),
    );
    expect(state.messages[0]).toMatchObject({ content: "No puedo compartir eso.", status: "completed" });
  });

  it("el snapshot reemplaza el estado y conserva el sequence del mensaje en curso", () => {
    const stale = apply(INITIAL_CHAT_STATE, token(1, "viejo"));
    const state = apply(
      stale,
      raw("session_snapshot", {
        status: "active",
        messages: [
          { message_id: "msg_u", role: "user", content: "hola", status: "completed", created_at: "x" },
          { message_id: "msg_b", role: "assistant", content: "Hola, ", status: "streaming", sequence: 2 },
        ],
      }),
      token(2, "Hola, ", "msg_b"), // ya incluido en el snapshot
      token(3, "¿en qué te ayudo?", "msg_b"),
    );
    expect(state.messages.map((m) => m.content)).toEqual(["hola", "Hola, ¿en qué te ayudo?"]);
  });
});

describe("isGenerating", () => {
  it("es true desde que el servidor confirma el user_message, antes del primer token", () => {
    const waiting = apply(INITIAL_CHAT_STATE, raw("user_message", { message_id: "msg_u", text: "hola" }));
    expect(isGenerating(waiting)).toBe(true);
    const streaming = apply(waiting, token(1, "Hola"));
    expect(isGenerating(streaming)).toBe(true);
    const done = apply(streaming, raw("generation_completed", { message_id: "msg_a" }));
    expect(isGenerating(done)).toBe(false);
  });

  it("interrumpir antes del primer token deja el mensaje vacío marcado y termina la espera", () => {
    const state = apply(
      INITIAL_CHAT_STATE,
      raw("user_message", { message_id: "msg_u", text: "hola" }),
      raw("generation_interrupted", { message_id: "msg_a", status: "interrupted" }),
    );
    expect(state.messages.at(-1)).toMatchObject({ messageId: "msg_a", content: "", status: "interrupted" });
    expect(isGenerating(state)).toBe(false);
  });
});

class FakeSocket implements SocketLike {
  readyState = 0;
  sent: string[] = [];
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  constructor(public url: string) {}
  open() {
    this.readyState = 1;
    this.onopen?.({} as Event);
  }
  receive(frame: object) {
    this.onmessage?.({ data: JSON.stringify(frame) } as MessageEvent);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close(code = 1000) {
    this.readyState = 3;
    this.onclose?.({ code } as CloseEvent);
  }
}

const snapshot = raw("session_snapshot", { status: "active", messages: [] });
const flush = () => new Promise((r) => setTimeout(r, 0));

function setup(token: string | null = "jwt") {
  const sockets: FakeSocket[] = [];
  const states: ChatConnectionState[] = [];
  const events: ServerEvent[] = [];
  const delays: number[] = [];
  let unauthorized = 0;
  const controller = new AbortController();
  const client = connectChatSocket({
    sessionId: SID,
    signal: controller.signal,
    getToken: () => token,
    onEvent: (e) => events.push(e),
    onState: (s) => states.push(s),
    onUnauthorized: () => (unauthorized += 1),
    baseUrl: "ws://api",
    createSocket: (url) => {
      const socket = new FakeSocket(url);
      sockets.push(socket);
      return socket;
    },
    sleep: async (ms) => {
      delays.push(ms);
    },
    random: () => 1,
  });
  return { client, sockets, states, events, delays, controller, unauthorized: () => unauthorized };
}

describe("connectChatSocket", () => {
  it("manda el JWT y el session_id en la URL y no aplica tokens antes del snapshot", async () => {
    const t = setup();
    const socket = t.sockets[0];
    expect(socket.url).toBe(`ws://api/chat/ws/${SID}?token=jwt`);
    socket.open();
    socket.receive(token(1, "temprano"));
    expect(t.client.sendUserMessage("hola")).toBe(false); // aún sin rehidratar
    socket.receive(snapshot);
    socket.receive(token(1, "Hola"));
    expect(t.events.map((e) => e.event)).toEqual(["session_snapshot", "token_chunk"]);
    expect(t.states).toEqual(["connecting", "rehydrating", "live"]);
    expect(t.client.sendInterrupt("era sobre la factura")).toBe(true);
    expect(socket.sent).toEqual([interruptFrame(SID, "era sobre la factura")]);
    t.controller.abort();
  });

  it("reconecta con backoff creciente y vuelve a pedir el snapshot", async () => {
    const t = setup();
    t.sockets[0].close(1006);
    await flush();
    t.sockets[1].close(1006);
    await flush();
    expect(t.delays).toEqual([1_000, 2_000]);
    const third = t.sockets[2];
    expect(third.url).toContain(`/chat/ws/${SID}`);
    third.open();
    third.receive(token(5, "perdido"));
    third.receive(snapshot);
    expect(t.events.map((e) => e.event)).toEqual(["session_snapshot"]);
    expect(t.states.at(-1)).toBe("live");
    third.close(1006); // tras llegar a "live", el backoff empieza de nuevo
    await flush();
    expect(t.delays.at(-1)).toBe(1_000);
    t.controller.abort();
  });

  it("4401 cierra la sesión sin reintentar; 4403 y 4404 tampoco reintentan", async () => {
    const t = setup();
    t.sockets[0].close(4401);
    await flush();
    expect(t.unauthorized()).toBe(1);
    expect(t.sockets).toHaveLength(1);

    const forbidden = setup();
    forbidden.sockets[0].close(4403);
    await flush();
    expect(forbidden.states.at(-1)).toBe("forbidden");
    expect(forbidden.sockets).toHaveLength(1);

    const missing = setup();
    missing.sockets[0].close(4404);
    await flush();
    expect(missing.states.at(-1)).toBe("not_found");
  });

  it("sin token no abre el socket", () => {
    const t = setup(null);
    expect(t.sockets).toHaveLength(0);
    expect(t.unauthorized()).toBe(1);
  });
});
