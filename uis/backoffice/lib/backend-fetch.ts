/**
 * `fetch` hacia el backend para los route handlers proxy (`app/api/**`).
 *
 * Si el backend no responde (caído, reiniciándose, timeout de red) devuelve un 502 JSON con
 * un mensaje legible, en lugar de dejar que la excepción acabe en un 500 HTML de Next. Los
 * clientes de `lib/*.ts` ya saben mostrar ese mensaje con una salida (reintentar).
 */
export const BACKEND_UNAVAILABLE_DETAIL = "El servicio no está disponible en este momento. Inténtalo de nuevo en unos minutos.";

export async function backendFetch(input: string | URL, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(input, init);
  } catch {
    return Response.json({ detail: BACKEND_UNAVAILABLE_DETAIL }, { status: 502 });
  }
}

/** 400 JSON para un cuerpo de petición que no se puede leer (JSON o multipart mal formado). */
export function badRequest(detail = "La petición no es válida."): Response {
  return Response.json({ detail }, { status: 400 });
}
