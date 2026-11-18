import { forward } from "@/lib/incident-manager-proxy";

export async function GET(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await params;
  return forward(request, `/${encodeURIComponent(id)}`, { method: "GET" });
}
