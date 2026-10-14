const BACKEND_API_URL = process.env.BACKEND_API_URL ?? "http://127.0.0.1:8000";

export async function GET(request: Request): Promise<Response> {
  const authorization = request.headers.get("authorization");

  const backendResponse = await fetch(`${BACKEND_API_URL}/reporting/pipeline-runs/latest`, {
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
