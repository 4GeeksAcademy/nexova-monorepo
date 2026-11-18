import { backendFetch, badRequest } from "@/lib/backend-fetch";
const BACKEND_API_URL = process.env.BACKEND_API_URL ?? "http://127.0.0.1:8000";

function relay(backendResponse: Response, body: string): Response {
  return new Response(body, {
    status: backendResponse.status,
    headers: {
      "content-type": backendResponse.headers.get("content-type") ?? "application/json; charset=utf-8",
    },
  });
}

/** Lista de tickets de RFP (la página hace poll mientras haya alguno en `analizando`). */
export async function GET(request: Request): Promise<Response> {
  const authorization = request.headers.get("authorization");
  const { search } = new URL(request.url);
  const backendResponse = await backendFetch(`${BACKEND_API_URL}/rfp/tickets${search}`, {
    headers: authorization ? { authorization } : {},
    cache: "no-store",
  });
  return relay(backendResponse, await backendResponse.text());
}

/** Subida de la RFP en PDF (multipart). El backend responde 202 y analiza en segundo plano. */
export async function POST(request: Request): Promise<Response> {
  const authorization = request.headers.get("authorization");
  let formData: FormData;
  try {
    formData = await request.formData();
  } catch {
    return badRequest("No se pudo leer el fichero subido. Inténtalo de nuevo.");
  }
  const backendResponse = await backendFetch(`${BACKEND_API_URL}/rfp/tickets`, {
    method: "POST",
    headers: authorization ? { authorization } : {},
    body: formData,
    cache: "no-store",
  });
  return relay(backendResponse, await backendResponse.text());
}
