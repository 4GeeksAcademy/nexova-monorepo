"use client";

import { useState } from "react";
import { RfpError, fetchTrace, type TraceEvent } from "@/lib/rfp";

/** Trazabilidad del ticket: cada nodo ejecutado, con su agente, resultado y hora, en orden (Partes 1–3). */

const PART_LABELS: Record<string, string> = {
  intake: "Parte 1 · recepción",
  drafting: "Parte 2 · borradores",
  approval: "Parte 3 · aprobación",
};

const STATUS_STYLES: Record<string, string> = {
  ok: "text-teal-700",
  interrumpido: "text-amber-700",
  error: "text-red-700",
};

export default function TraceTimeline({ ticketId }: { ticketId: string }) {
  const [events, setEvents] = useState<TraceEvent[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      setEvents(await fetchTrace(ticketId));
    } catch (err) {
      setError(err instanceof RfpError ? err.message : "No se pudo cargar la trazabilidad.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="rounded-xl border border-stone-200 bg-white p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-medium text-stone-900">Trazabilidad</h2>
          <p className="text-sm text-stone-500">Qué agente hizo qué y en qué orden.</p>
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="rounded-lg border border-stone-300 px-4 py-2 text-sm text-stone-700 transition hover:bg-stone-50 disabled:opacity-50"
        >
          {loading ? "Cargando…" : events ? "Actualizar" : "Ver trazabilidad"}
        </button>
      </div>
      {error && <p className="mt-3 text-sm text-red-700">{error}</p>}
      {events && events.length === 0 && <p className="mt-3 text-sm text-stone-400">Sin eventos registrados.</p>}
      {events && events.length > 0 && (
        <ol className="mt-4 max-h-[28rem] space-y-1 overflow-y-auto font-mono text-xs">
          {events.map((event) => (
            <li key={event.seq} className="rounded border border-stone-100 px-3 py-1.5" title={event.output_preview}>
              <span className="text-stone-400">#{event.seq}</span>{" "}
              <span className="text-stone-500">{new Date(event.finished_at).toLocaleTimeString("es-ES")}</span>{" "}
              <span className="text-stone-500">[{PART_LABELS[event.part] ?? event.part}]</span>{" "}
              <span className="font-medium text-stone-800">{event.agent}</span>{" "}
              <span className="text-stone-500">({event.node})</span>{" "}
              <span className={STATUS_STYLES[event.status] ?? "text-stone-600"}>{event.status}</span>{" "}
              <span className="text-stone-400">{event.duration_ms} ms</span>
              {event.thread_id && <span className="text-stone-400"> · {event.thread_id}</span>}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
