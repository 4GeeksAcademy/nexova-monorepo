import { backendFetch } from "@/lib/backend-fetch";
const BACKEND_API_URL = process.env.BACKEND_API_URL ?? "http://127.0.0.1:8000";

/**
 * Crea una ChatSession del usuario autenticado. Solo la creación pasa por aquí:
 * el chat va por WebSocket directo a la API (los route handlers no hacen WebSocket).
 */
export async function POST(request: Request): Promise<Response> {
  const authorization = request.headers.get("authorization");
  const backendResponse = await backendFetch(`${BACKEND_API_URL}/chat/sessions`, {
    method: "POST",
    headers: authorization ? { authorization } : {},
    cache: "no-store",
  });
  return new Response(await backendResponse.text(), {
    status: backendResponse.status,
    headers: {
      "content-type": backendResponse.headers.get("content-type") ?? "application/json; charset=utf-8",
    },
  });
}
