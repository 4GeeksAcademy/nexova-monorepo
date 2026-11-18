/**
 * Mensajes de error legibles para el usuario, comunes a todos los clientes de la API.
 *
 * Reglas: nunca se muestra un código HTTP, un cuerpo de parseo («Unexpected token…») ni
 * el texto de un error del servidor (5xx). Solo el `detail` de los errores de cliente
 * (4xx) — que el backend redacta para personas — llega a la pantalla.
 */

export const GENERIC_ERROR_MESSAGE =
  "Algo ha ido mal. Inténtalo de nuevo; si el problema continúa, contacta con el equipo de soporte.";
export const CONNECTION_ERROR_MESSAGE =
  "No se pudo conectar con el servidor. Comprueba tu conexión e inténtalo de nuevo.";
export const TIMEOUT_ERROR_MESSAGE = "El servidor tardó demasiado en responder. Inténtalo de nuevo.";

const MAX_DETAIL_LENGTH = 300;

/** Mensaje por defecto de un estado HTTP, sin mencionar el código. */
export function statusMessage(status: number): string {
  if (status === 401) return "Tu sesión ha expirado. Vuelve a iniciar sesión.";
  if (status === 403) return "No tienes permiso para realizar esta acción.";
  if (status === 404) return "No se encontró lo que buscabas.";
  if (status === 429) return "Demasiadas solicitudes. Espera un momento e inténtalo de nuevo.";
  return GENERIC_ERROR_MESSAGE;
}

/** El texto del servidor solo se usa en 4xx; en 5xx (o sin texto) se muestra el mensaje genérico. */
export function userMessage(status: number, detail: unknown): string {
  const usable =
    status >= 400 && status < 500 && typeof detail === "string" && detail.trim() !== "" && detail.length <= MAX_DETAIL_LENGTH;
  return usable ? (detail as string) : statusMessage(status);
}
