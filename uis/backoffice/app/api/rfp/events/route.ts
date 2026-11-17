const BACKEND_API_URL = process.env.BACKEND_API_URL ?? "http://127.0.0.1:8000";

function jsonError(status: number, detail: string): Response {
  return new Response(JSON.stringify({ detail }), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

/**
 * Proxy del stream SSE de RFPs. A diferencia del resto de proxies, NO espera el
 * cuerpo (`.text()`): devuelve el `ReadableStream` del backend tal cual para que
 * cada evento llegue al navegador al instante. Reenvía `Authorization` y
 * `Last-Event-ID` (reanudación tras una caída) y cancela la petición al backend
 * cuando el navegador se desconecta.
 */
export async function GET(request: Request): Promise<Response> {
  const headers: Record<string, string> = { accept: "text/event-stream" };
  const authorization = request.headers.get("authorization");
  const lastEventId = request.headers.get("last-event-id");
  if (authorization) headers.authorization = authorization;
  if (lastEventId) headers["last-event-id"] = lastEventId;

  let backendResponse: Response;
  try {
    backendResponse = await fetch(`${BACKEND_API_URL}/rfp/events`, {
      headers,
      cache: "no-store",
      signal: request.signal,
    });
  } catch {
    return jsonError(502, "No se pudo conectar con el servidor de notificaciones.");
  }

  if (!backendResponse.ok || !backendResponse.body) {
    return new Response(await backendResponse.text(), {
      status: backendResponse.status,
      headers: {
        "content-type": backendResponse.headers.get("content-type") ?? "application/json; charset=utf-8",
      },
    });
  }

  return new Response(backendResponse.body, {
    status: 200,
    headers: {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache, no-transform",
      "x-accel-buffering": "no",
    },
  });
}
