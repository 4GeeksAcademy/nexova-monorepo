import { getToken } from "./auth-storage";
import { handleSessionExpired } from "./session";

/**
 * Cliente de la consulta a la base de conocimiento (`POST /knowledge/query`,
 * vía el proxy `app/api/knowledge/query`). La API solo devuelve `{ answer }`.
 */

export class KnowledgeError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const QUERY_PATH = "/api/knowledge/query";
// Retrieval + generación con LLM: más lento que el resto de la API.
const REQUEST_TIMEOUT_MS = 60_000;

export const MAX_QUESTION_LENGTH = 1000;

type JsonRecord = Record<string, unknown>;

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseKnowledgeAnswer(raw: unknown): string {
  if (!isRecord(raw) || typeof raw.answer !== "string") {
    throw new Error("Respuesta de la base de conocimiento inválida.");
  }
  return raw.answer;
}

async function readErrorDetail(res: Response): Promise<string> {
  if (res.status === 503) {
    return "La base de conocimiento no está disponible en este momento. Inténtalo de nuevo en unos minutos.";
  }
  if (res.status === 422) return "La pregunta no es válida (entre 3 y 1000 caracteres).";
  return `Error inesperado (HTTP ${res.status}).`;
}

/** Envía la pregunta y devuelve la respuesta generada. Lanza `KnowledgeError` si falla. */
export async function askKnowledgeBase(question: string): Promise<string> {
  const token = getToken();
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  let res: Response;
  try {
    res = await fetch(QUERY_PATH, {
      method: "POST",
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ question }),
    });
  } catch {
    throw new KnowledgeError(0, "No se pudo conectar con el servidor. Inténtalo de nuevo en unos minutos.");
  } finally {
    clearTimeout(timeoutId);
  }

  if (res.status === 401) {
    handleSessionExpired();
    throw new KnowledgeError(401, "Tu sesión expiró.");
  }
  if (!res.ok) throw new KnowledgeError(res.status, await readErrorDetail(res));

  try {
    return parseKnowledgeAnswer(await res.json());
  } catch (err) {
    throw new KnowledgeError(
      res.status,
      err instanceof Error ? err.message : "Respuesta de la base de conocimiento inválida.",
    );
  }
}
