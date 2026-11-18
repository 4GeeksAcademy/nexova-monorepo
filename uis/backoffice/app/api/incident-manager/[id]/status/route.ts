import { forward } from "@/lib/incident-manager-proxy";

/** Cambia solo el estado de la incidencia (el backend valida la transición). */
export async function PATCH(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await params;
  return forward(request, `/${encodeURIComponent(id)}/status`, { method: "PATCH", withBody: true });
}
