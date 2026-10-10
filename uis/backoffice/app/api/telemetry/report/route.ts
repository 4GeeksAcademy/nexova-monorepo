const BACKEND_API_URL = process.env.BACKEND_API_URL ?? "http://127.0.0.1:8000";

export async function GET(request: Request): Promise<Response> {
  const authorization = request.headers.get("authorization");
  // Reenvía start_date / end_date tal cual; la API valida y aplica el defecto de 7 días.
  const { search } = new URL(request.url);

  const backendResponse = await fetch(`${BACKEND_API_URL}/telemetry/report${search}`, {
    headers: authorization ? { authorization } : {},
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
