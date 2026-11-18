import { backendFetch, badRequest } from "@/lib/backend-fetch";
const BACKEND_API_URL = process.env.BACKEND_API_URL ?? "http://127.0.0.1:8000";

export async function POST(request: Request): Promise<Response> {
  const authorization = request.headers.get("authorization");
  let formData: FormData;
  try {
    formData = await request.formData();
  } catch {
    return badRequest("No se pudo leer el fichero subido. Inténtalo de nuevo.");
  }

  const backendResponse = await backendFetch(`${BACKEND_API_URL}/api/incidents/analyze`, {
    method: "POST",
    headers: authorization ? { authorization } : {},
    body: formData,
    cache: "no-store",
  });

  const body = await backendResponse.text();

  return new Response(body, {
    status: backendResponse.status,
    headers: {
      "content-type":
        backendResponse.headers.get("content-type") ?? "application/json; charset=utf-8",
    },
  });
}