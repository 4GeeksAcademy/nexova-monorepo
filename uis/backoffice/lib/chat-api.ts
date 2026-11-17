import { getToken } from "./auth-storage";
import { handleSessionExpired } from "./session";

/**
 * Creación de la ChatSession (`POST /chat/sessions`, vía el proxy
 * `app/api/chat/sessions`). El chat en sí va por WebSocket (`chat-socket.ts`).
 */

export class ChatApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const SESSIONS_PATH = "/api/chat/sessions";

export interface ChatSessionInfo {
  sessionId: string;
  agentId: string;
  status: string;
}

export function parseChatSession(raw: unknown): ChatSessionInfo {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    throw new Error("Respuesta de sesión de chat inválida.");
  }
  const record = raw as Record<string, unknown>;
  const { session_id: sessionId, agent_id: agentId, status } = record;
  if (typeof sessionId !== "string" || typeof agentId !== "string" || typeof status !== "string") {
    throw new Error("Respuesta de sesión de chat inválida.");
  }
  return { sessionId, agentId, status };
}

export async function createChatSession(fetchImpl: typeof fetch = fetch): Promise<ChatSessionInfo> {
  const token = getToken();
  let res: Response;
  try {
    res = await fetchImpl(SESSIONS_PATH, {
      method: "POST",
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
  } catch {
    throw new ChatApiError(0, "No se pudo conectar con el servidor. Inténtalo de nuevo en unos minutos.");
  }
  if (res.status === 401) {
    handleSessionExpired();
    throw new ChatApiError(401, "Tu sesión expiró.");
  }
  if (!res.ok) throw new ChatApiError(res.status, `No se pudo iniciar el chat (HTTP ${res.status}).`);
  return parseChatSession(await res.json());
}
