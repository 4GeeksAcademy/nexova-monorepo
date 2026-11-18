import { backendFetch } from "@/lib/backend-fetch";
const BACKEND_API_URL = process.env.BACKEND_API_URL ?? "http://127.0.0.1:8000";

/** (Re)abre la aprobación humana de las secciones (Parte 3); `?department_id=` para una sola. El backend responde 202. */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ ticketId: string }> }
): Promise<Response> {
  const { ticketId } = await params;
  const authorization = request.headers.get("authorization");
  const departmentId = new URL(request.url).searchParams.get("department_id");
  const query = departmentId ? `?department_id=${encodeURIComponent(departmentId)}` : "";

  const backendResponse = await backendFetch(
    `${BACKEND_API_URL}/rfp/tickets/${encodeURIComponent(ticketId)}/approvals${query}`,
    {
      method: "POST",
      headers: authorization ? { authorization } : {},
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
