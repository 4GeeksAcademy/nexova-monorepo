/**
 * Contrato del chat por WebSocket con el agente de soporte de primera línea
 * (`services/api/chat/schemas.py`). Frames JSON `{ event, data }`.
 *
 * Todo frame del servidor se valida antes de usarlo (`parseServerEvent`); uno
 * que no cumple el contrato se descarta. `chatReducer` es puro: aplica cada
 * evento al estado de la conversación y deduplica los tokens por `sequence`,
 * así un evento repetido nunca duplica texto.
 */

export const MAX_MESSAGE_LENGTH = 1000;

export type MessageRole = "user" | "assistant";
export type MessageStatus = "streaming" | "completed" | "interrupted";
export type SessionStatus = "active" | "interrupted" | "closed";

export interface ChatMessage {
  messageId: string;
  role: MessageRole;
  content: string;
  status: MessageStatus;
  /** Último `sequence` aplicado (solo relevante en mensajes del asistente). */
  sequence: number;
}

export type ServerEvent =
  | { event: "session_snapshot"; sessionId: string; status: SessionStatus; messages: ChatMessage[] }
  | { event: "user_message"; sessionId: string; messageId: string; text: string }
  | { event: "token_chunk"; sessionId: string; messageId: string; token: string; sequence: number }
  | { event: "generation_interrupted"; sessionId: string; messageId: string }
  | { event: "generation_completed"; sessionId: string; messageId: string; content?: string }
  | { event: "error"; code: string; message: string; messageId?: string };

type JsonRecord = Record<string, unknown>;

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function str(record: JsonRecord, key: string): string | null {
  const value = record[key];
  return typeof value === "string" ? value : null;
}

function isRole(value: unknown): value is MessageRole {
  return value === "user" || value === "assistant";
}

function isMessageStatus(value: unknown): value is MessageStatus {
  return value === "streaming" || value === "completed" || value === "interrupted";
}

function isSessionStatus(value: unknown): value is SessionStatus {
  return value === "active" || value === "interrupted" || value === "closed";
}

function parseMessage(raw: unknown): ChatMessage | null {
  if (!isRecord(raw)) return null;
  const messageId = str(raw, "message_id");
  const content = str(raw, "content");
  if (!messageId || content === null || !isRole(raw.role) || !isMessageStatus(raw.status)) return null;
  const sequence = typeof raw.sequence === "number" && Number.isInteger(raw.sequence) ? raw.sequence : 0;
  return { messageId, role: raw.role, content, status: raw.status, sequence };
}

/** Valida un frame del servidor. Devuelve `null` si no cumple el contrato. */
export function parseServerEvent(raw: unknown): ServerEvent | null {
  if (!isRecord(raw) || !isRecord(raw.data)) return null;
  const data = raw.data;
  const sessionId = str(data, "session_id");
  const messageId = str(data, "message_id");

  switch (raw.event) {
    case "session_snapshot": {
      if (!sessionId || !Array.isArray(data.messages)) return null;
      const messages = data.messages
        .map(parseMessage)
        .filter((m): m is ChatMessage => m !== null);
      if (messages.length !== data.messages.length) return null;
      const status = isSessionStatus(data.status) ? data.status : "active";
      return { event: "session_snapshot", sessionId, status, messages };
    }
    case "user_message": {
      const text = str(data, "text");
      if (!sessionId || !messageId || text === null) return null;
      return { event: "user_message", sessionId, messageId, text };
    }
    case "token_chunk": {
      const token = str(data, "token");
      const sequence = data.sequence;
      if (!sessionId || !messageId || token === null) return null;
      if (typeof sequence !== "number" || !Number.isInteger(sequence) || sequence < 1) return null;
      return { event: "token_chunk", sessionId, messageId, token, sequence };
    }
    case "generation_interrupted":
      if (!sessionId || !messageId || data.status !== "interrupted") return null;
      return { event: "generation_interrupted", sessionId, messageId };
    case "generation_completed": {
      if (!sessionId || !messageId) return null;
      const content = str(data, "content");
      return content === null
        ? { event: "generation_completed", sessionId, messageId }
        : { event: "generation_completed", sessionId, messageId, content };
    }
    case "error": {
      const code = str(data, "code");
      const message = str(data, "message");
      if (!code || message === null) return null;
      return messageId
        ? { event: "error", code, message, messageId }
        : { event: "error", code, message };
    }
    default:
      return null;
  }
}

export interface ChatState {
  messages: ChatMessage[];
  sessionStatus: SessionStatus;
  error: { code: string; message: string } | null;
}

export const INITIAL_CHAT_STATE: ChatState = { messages: [], sessionStatus: "active", error: null };

function updateMessage(
  messages: ChatMessage[],
  messageId: string,
  update: (message: ChatMessage) => ChatMessage,
): ChatMessage[] {
  return messages.map((m) => (m.messageId === messageId ? update(m) : m));
}

export function chatReducer(state: ChatState, event: ServerEvent): ChatState {
  switch (event.event) {
    case "session_snapshot":
      return { messages: event.messages, sessionStatus: event.status, error: null };

    case "user_message":
      if (state.messages.some((m) => m.messageId === event.messageId)) return state;
      return {
        ...state,
        sessionStatus: "active",
        error: null,
        messages: [
          ...state.messages,
          { messageId: event.messageId, role: "user", content: event.text, status: "completed", sequence: 0 },
        ],
      };

    case "token_chunk": {
      const existing = state.messages.find((m) => m.messageId === event.messageId);
      if (!existing) {
        const created: ChatMessage = {
          messageId: event.messageId,
          role: "assistant",
          content: event.token,
          status: "streaming",
          sequence: event.sequence,
        };
        return { ...state, messages: [...state.messages, created] };
      }
      // Token repetido (o de una generación ya cerrada): no se vuelve a aplicar.
      if (existing.status !== "streaming" || event.sequence <= existing.sequence) return state;
      return {
        ...state,
        messages: updateMessage(state.messages, event.messageId, (m) => ({
          ...m,
          content: m.content + event.token,
          sequence: event.sequence,
        })),
      };
    }

    case "generation_interrupted":
      // Interrumpida antes del primer token: el mensaje (vacío) existe igual en el historial.
      if (!state.messages.some((m) => m.messageId === event.messageId)) {
        const created: ChatMessage = {
          messageId: event.messageId,
          role: "assistant",
          content: "",
          status: "interrupted",
          sequence: 0,
        };
        return { ...state, sessionStatus: "interrupted", messages: [...state.messages, created] };
      }
      return {
        ...state,
        sessionStatus: "interrupted",
        messages: updateMessage(state.messages, event.messageId, (m) => ({ ...m, status: "interrupted" })),
      };

    case "generation_completed": {
      const exists = state.messages.some((m) => m.messageId === event.messageId);
      if (!exists) {
        if (event.content === undefined) return state;
        const created: ChatMessage = {
          messageId: event.messageId,
          role: "assistant",
          content: event.content,
          status: "completed",
          sequence: 0,
        };
        return { ...state, messages: [...state.messages, created] };
      }
      return {
        ...state,
        messages: updateMessage(state.messages, event.messageId, (m) => ({
          ...m,
          status: "completed",
          // `content` solo llega si el texto definitivo difiere del mostrado (guard de salida).
          content: event.content ?? m.content,
        })),
      };
    }

    case "error":
      return { ...state, error: { code: event.code, message: event.message } };
  }
}

/** El agente aún no ha emitido ningún token de la respuesta al último mensaje del usuario. */
export function isAwaitingFirstToken(state: ChatState): boolean {
  return state.messages.at(-1)?.role === "user" && state.sessionStatus !== "closed";
}

/**
 * Hay una respuesta del agente en curso: un mensaje nuevo la interrumpirá.
 * Empieza en cuanto el servidor confirma el `user_message` (el agente enruta,
 * consulta fuentes y genera; el primer token puede tardar varios segundos), no
 * con el primer `token_chunk`.
 */
export function isGenerating(state: ChatState): boolean {
  return (
    isAwaitingFirstToken(state) ||
    state.messages.some((m) => m.role === "assistant" && m.status === "streaming")
  );
}

export function userMessageFrame(sessionId: string, text: string): string {
  return JSON.stringify({ event: "user_message", data: { session_id: sessionId, text } });
}

export function interruptFrame(sessionId: string, newInput: string): string {
  return JSON.stringify({ event: "interrupt_requested", data: { session_id: sessionId, new_input: newInput } });
}
