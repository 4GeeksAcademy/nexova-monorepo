import { forward } from "@/lib/incident-manager-proxy";

/** Lista de incidencias (filtros `status`, `origin`, `branch`, `category` por query string). */
export async function GET(request: Request): Promise<Response> {
  const { search } = new URL(request.url);
  return forward(request, search, { method: "GET" });
}

/** Alta de incidencia. */
export async function POST(request: Request): Promise<Response> {
  return forward(request, "", { method: "POST", withBody: true });
}
