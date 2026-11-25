import { backendFetch, badRequest } from "@/lib/backend-fetch";

const BACKEND_API_URL = process.env.BACKEND_API_URL ?? "http://127.0.0.1:8000";

/** Solo estos recursos del backend se exponen por este proxy (la autorización real la hace la API: 403). */
const ALLOWED_RESOURCES: ReadonlySet<string> = new Set(["roles", "departments", "users", "internal-reports"]);
const SEGMENT = /^[A-Za-z0-9._-]+$/;

type Context = { params: Promise<{ path: string[] }> };

async function forward(request: Request, { params }: Context): Promise<Response> {
  const { path } = await params;
  if (!path.length || !ALLOWED_RESOURCES.has(path[0]) || !path.every((segment) => SEGMENT.test(segment))) {
    return Response.json({ detail: "No se encontró lo que buscabas." }, { status: 404 });
  }

  const headers: Record<string, string> = {};
  const authorization = request.headers.get("authorization");
  if (authorization) headers.authorization = authorization;

  let body: string | undefined;
  if (request.method !== "GET" && request.method !== "DELETE") {
    try {
      body = await request.text();
    } catch {
      return badRequest("Revisa los datos del formulario e inténtalo de nuevo.");
    }
    headers["content-type"] = "application/json";
  }

  const { search } = new URL(request.url);
  const backendResponse = await backendFetch(`${BACKEND_API_URL}/${path.join("/")}${search}`, {
    method: request.method,
    headers,
    body,
    cache: "no-store",
  });

  return new Response(await backendResponse.text(), {
    status: backendResponse.status,
    headers: { "content-type": backendResponse.headers.get("content-type") ?? "application/json; charset=utf-8" },
  });
}

export const GET = forward;
export const POST = forward;
export const PUT = forward;
export const PATCH = forward;
export const DELETE = forward;
