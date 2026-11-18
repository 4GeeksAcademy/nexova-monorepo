/** Utilidades de servidor para los proxies de `app/api/incident-manager/*`. */

const BACKEND_API_URL = process.env.BACKEND_API_URL ?? "http://127.0.0.1:8000";
const BACKEND_PATH = "/api/incident-manager";

/** Reenvía la petición al backend con el mismo `Authorization` y devuelve su respuesta tal cual. */
export async function forward(
  request: Request,
  suffix: string,
  init: { method: "GET" | "POST" | "PATCH"; withBody?: boolean },
): Promise<Response> {
  const authorization = request.headers.get("authorization");
  const headers: Record<string, string> = {};
  if (authorization) headers.authorization = authorization;
  if (init.withBody) headers["content-type"] = "application/json";

  try {
    const backendResponse = await fetch(`${BACKEND_API_URL}${BACKEND_PATH}${suffix}`, {
      method: init.method,
      headers,
      body: init.withBody ? await request.text() : undefined,
      cache: "no-store",
    });
    return new Response(await backendResponse.text(), {
      status: backendResponse.status,
      headers: {
        "content-type": backendResponse.headers.get("content-type") ?? "application/json; charset=utf-8",
      },
    });
  } catch {
    return Response.json({ detail: "El servicio de incidencias no está disponible." }, { status: 502 });
  }
}
