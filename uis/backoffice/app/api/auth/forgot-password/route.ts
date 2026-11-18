import { backendFetch } from "@/lib/backend-fetch";
const BACKEND_API_URL = process.env.BACKEND_API_URL ?? "http://127.0.0.1:8000";

export async function POST(request: Request): Promise<Response> {
  const body = await request.text();

  const backendResponse = await backendFetch(`${BACKEND_API_URL}/auth/forgot-password`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body,
    cache: "no-store",
  });

  const responseBody = await backendResponse.text();

  return new Response(responseBody, {
    status: backendResponse.status,
    headers: {
      "content-type": backendResponse.headers.get("content-type") ?? "application/json; charset=utf-8",
    },
  });
}
