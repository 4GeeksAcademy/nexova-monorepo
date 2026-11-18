import { backendFetch } from "@/lib/backend-fetch";
const BACKEND_API_URL = process.env.BACKEND_API_URL ?? "http://127.0.0.1:8000";

export async function GET(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
): Promise<Response> {
  const { id } = await params;
  const authorization = request.headers.get("authorization");

  const backendResponse = await backendFetch(`${BACKEND_API_URL}/inventory/products/${encodeURIComponent(id)}`, {
    headers: authorization ? { authorization } : {},
    cache: "no-store",
  });

  const body = await backendResponse.text();

  return new Response(body, {
    status: backendResponse.status,
    headers: {
      "content-type": backendResponse.headers.get("content-type") ?? "application/json; charset=utf-8",
    },
  });
}
