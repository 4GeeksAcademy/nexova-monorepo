const BACKEND_API_URL = process.env.BACKEND_API_URL ?? "http://127.0.0.1:8000";

/** Detalle de un ticket de RFP: metadatos, secciones por departamento y resumen para Ventas. */
export async function GET(
  request: Request,
  { params }: { params: Promise<{ ticketId: string }> }
): Promise<Response> {
  const { ticketId } = await params;
  const authorization = request.headers.get("authorization");

  const backendResponse = await fetch(
    `${BACKEND_API_URL}/rfp/tickets/${encodeURIComponent(ticketId)}`,
    {
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
