import { forward } from "@/lib/incident-manager-proxy";

export async function GET(request: Request): Promise<Response> {
  return forward(request, "/summary", { method: "GET" });
}
