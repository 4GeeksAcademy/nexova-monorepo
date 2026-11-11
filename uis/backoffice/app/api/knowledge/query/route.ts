const BACKEND_API_URL = process.env.BACKEND_API_URL ?? "http://127.0.0.1:8000";

export async function POST(request: Request): Promise<Response> {
  const authorization = request.headers.get("authorization");

  const backendResponse = await fetch(`${BACKEND_API_URL}/knowledge/query`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(authorization ? { authorization } : {}),
    },
    body: await request.text(),
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
