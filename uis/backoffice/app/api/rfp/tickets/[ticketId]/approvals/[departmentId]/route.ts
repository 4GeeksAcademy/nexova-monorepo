import { backendFetch } from "@/lib/backend-fetch";
const BACKEND_API_URL = process.env.BACKEND_API_URL ?? "http://127.0.0.1:8000";

/** Decisión humana de un departamento (aprobar / pedir cambios / rechazar). El backend responde 202. */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ ticketId: string; departmentId: string }> }
): Promise<Response> {
  const { ticketId, departmentId } = await params;
  const authorization = request.headers.get("authorization");
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (authorization) headers.authorization = authorization;

  const backendResponse = await backendFetch(
    `${BACKEND_API_URL}/rfp/tickets/${encodeURIComponent(ticketId)}/approvals/${encodeURIComponent(departmentId)}`,
    {
      method: "POST",
      headers,
      body: await request.text(),
      cache: "no-store",
    }
  );

  const body = await backendResponse.text();
  return new Response(body, {
    status: backendResponse.status,
    headers: {
      "content-type": backendResponse.headers.get("content-type") ?? "application/json; charset=utf-8",
    },
  });
}
