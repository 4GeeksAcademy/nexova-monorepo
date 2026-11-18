"use client";

import ErrorNotice from "@/components/ErrorNotice";
import { CONNECTION_ERROR_MESSAGE } from "@/lib/http-errors";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import NewTicketToast, { type NewTicketNotice } from "@/components/rfp/NewTicketToast";
import PdfUploader from "@/components/rfp/PdfUploader";
import TicketStatusBadge from "@/components/rfp/TicketStatusBadge";
import { DEPARTMENTS, RfpError, fetchRfpTickets, isTicketBusy, uploadRfp, type RfpTicketSummary } from "@/lib/rfp";
import type { StreamState } from "@/lib/rfp-stream";
import { summaryFromCreatedEvent, type RfpTicketCreatedEvent } from "@/lib/sse";
import { useRfpTicketStream } from "@/lib/useRfpTicketStream";

/**
 * Bandeja de RFPs (modo ticket). Cada PDF subido crea un ticket; el análisis
 * corre en segundo plano. Las RFPs nuevas (también las que sube otra persona)
 * llegan solas por el stream SSE (`rfp_ticket_created`); además esta página
 * consulta el estado cada pocos segundos mientras quede alguna en `analizando`.
 */

const POLL_MS = 3_000;
const ERROR_POLL_MS = 10_000;
const TOAST_MS = 8_000;
const FRESH_MS = 60_000;

const STREAM_LABELS: Record<StreamState, { text: string; dot: string }> = {
  connecting: { text: "Conectando…", dot: "bg-stone-400" },
  live: { text: "En vivo", dot: "bg-teal-500" },
  reconnecting: { text: "Reconectando…", dot: "bg-amber-500 animate-pulse" },
  forbidden: { text: "Sin acceso a las notificaciones", dot: "bg-red-500" },
};

/** Lista del servidor + filas llegadas por SSE que esa lista (anterior al evento) aún no trae. */
function mergeLive(list: RfpTicketSummary[], previous: RfpTicketSummary[] | null, liveIds: Set<string>) {
  const known = new Set(list.map((t) => t.ticket_id));
  const kept = (previous ?? []).filter((t) => liveIds.has(t.ticket_id) && !known.has(t.ticket_id));
  return [...kept, ...list];
}
const DATE_FORMAT = new Intl.DateTimeFormat("es-ES", { dateStyle: "medium", timeStyle: "short" });

function errorMessage(err: unknown): string {
  if (err instanceof RfpError) {
    return err.status === 403 ? "Solo el personal interno de Nexova puede ver las RFPs." : err.message;
  }
  return CONNECTION_ERROR_MESSAGE;
}

export default function RfpInboxPage() {
  const [tickets, setTickets] = useState<RfpTicketSummary[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [freshIds, setFreshIds] = useState<ReadonlySet<string>>(new Set());
  const [notices, setNotices] = useState<NewTicketNotice[]>([]);
  const liveIdsRef = useRef<Set<string>>(new Set());
  const ticketsRef = useRef<RfpTicketSummary[] | null>(null);

  useEffect(() => {
    ticketsRef.current = tickets;
  }, [tickets]);

  // Una carga por cambio de `refreshKey`; el poll de abajo la repite mientras haga falta.
  useEffect(() => {
    let cancelled = false;
    fetchRfpTickets()
      .then((list) => {
        if (cancelled) return;
        setTickets((previous) => mergeLive(list, previous, liveIdsRef.current));
        setLoadError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setLoadError(errorMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  // Poll de estado solo mientras haya tickets en curso (o tras un error). Un evento SSE
  // añade la fila sin recargar la lista; al estar `analizando`, este poll la completa.
  const hasBusy = tickets?.some(isTicketBusy) ?? false;
  useEffect(() => {
    if (!hasBusy && !loadError) return;
    const timer = setTimeout(() => setRefreshKey((key) => key + 1), loadError ? ERROR_POLL_MS : POLL_MS);
    return () => clearTimeout(timer);
  }, [hasBusy, loadError, tickets, refreshKey]);

  const handleTicketCreated = useCallback((event: RfpTicketCreatedEvent) => {
    const alreadyListed = ticketsRef.current?.some((t) => t.ticket_id === event.ticket_id) ?? false;
    liveIdsRef.current.add(event.ticket_id);
    // Upsert por ticket_id: nunca duplica la fila, llegue antes o después de la lista.
    setTickets((previous) =>
      (previous ?? []).some((t) => t.ticket_id === event.ticket_id)
        ? previous
        : [summaryFromCreatedEvent(event), ...(previous ?? [])]
    );
    if (alreadyListed) return;
    setFreshIds((ids) => new Set(ids).add(event.ticket_id));
    setNotices((current) => [
      { ticket_id: event.ticket_id, client_name: event.client_name, client_hq: event.client_hq,
        services_requested: event.services_requested },
      ...current,
    ]);
    setTimeout(() => setNotices((current) => current.filter((n) => n.ticket_id !== event.ticket_id)), TOAST_MS);
    setTimeout(() => setFreshIds((ids) => {
      const next = new Set(ids);
      next.delete(event.ticket_id);
      return next;
    }), FRESH_MS);
  }, []);

  const streamState = useRfpTicketStream(handleTicketCreated);
  const dismissNotice = useCallback(
    (ticketId: string) => setNotices((current) => current.filter((n) => n.ticket_id !== ticketId)),
    []
  );

  const handleFile = useCallback(async (file: File) => {
    setIsUploading(true);
    setUploadError(null);
    setNotice(null);
    try {
      const accepted = await uploadRfp(file);
      setNotice(`Ticket ${accepted.ticket_id} creado. El análisis está en curso.`);
    } catch (err) {
      setUploadError(errorMessage(err));
    } finally {
      setIsUploading(false);
      setRefreshKey((key) => key + 1);
    }
  }, []);

  return (
    <div className="space-y-10">
      <header>
        <p className="font-mono text-xs uppercase tracking-widest text-teal-700">Recepción de RFPs</p>
        <h1 className="mt-2 text-4xl font-medium tracking-tight text-stone-900">Bandeja de solicitudes</h1>
        <p className="mt-3 max-w-2xl text-sm text-stone-600">
          Sube la RFP del cliente en PDF. El flujo comprueba si es una RFP, extrae sus datos y reparte el
          análisis solo entre los departamentos que la solicitud necesita.
        </p>
      </header>

      <section className="space-y-3">
        <PdfUploader onFileSelected={handleFile} onInvalidFile={setUploadError} isUploading={isUploading} />
        {notice && (
          <p className="rounded-lg border border-teal-200 bg-teal-50 px-4 py-3 text-sm text-teal-800">{notice}</p>
        )}
        {uploadError && (
          <p className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{uploadError}</p>
        )}
      </section>

      <section>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-medium text-stone-900">Tickets</h2>
          <p className="flex items-center gap-2 font-mono text-xs text-stone-500" aria-live="off">
            <span className={`h-2 w-2 rounded-full ${STREAM_LABELS[streamState].dot}`} aria-hidden />
            {STREAM_LABELS[streamState].text}
          </p>
        </div>
        {loadError && (
          <ErrorNotice
            className="mb-4"
            title="No se ha podido cargar la lista de RFPs"
            message={loadError}
            onRetry={() => setRefreshKey((key) => key + 1)}
          />
        )}
        {tickets === null && !loadError ? (
          <p className="text-sm text-stone-500">Cargando tickets…</p>
        ) : tickets && tickets.length === 0 ? (
          <p className="text-sm text-stone-500">Todavía no se ha subido ninguna RFP.</p>
        ) : (
          <div className="overflow-hidden rounded-xl border border-stone-200 bg-white">
            <table className="w-full text-left text-sm">
              <thead className="bg-stone-50 font-mono text-xs uppercase tracking-wider text-stone-500">
                <tr>
                  <th className="px-4 py-3">Ticket</th>
                  <th className="px-4 py-3">Cliente</th>
                  <th className="px-4 py-3">Estado</th>
                  <th className="px-4 py-3">Departamentos</th>
                  <th className="px-4 py-3">Subido</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-stone-100">
                {(tickets ?? []).map((ticket) => (
                  <tr
                    key={ticket.ticket_id}
                    className={`align-top hover:bg-stone-50/70 ${
                      freshIds.has(ticket.ticket_id) ? "bg-teal-50/70 ring-1 ring-inset ring-teal-200" : ""
                    }`}
                  >
                    <td className="px-4 py-3">
                      <Link
                        href={`/rfp/${ticket.ticket_id}`}
                        prefetch={false}
                        className="font-mono text-teal-700 underline-offset-2 hover:underline"
                      >
                        {ticket.ticket_id}
                      </Link>
                      {freshIds.has(ticket.ticket_id) && (
                        <span className="ml-2 inline-flex items-center gap-1 rounded-full border border-teal-300 bg-teal-100 px-2 py-0.5 font-mono text-[10px] uppercase text-teal-800">
                          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-teal-600" aria-hidden />
                          Nuevo
                        </span>
                      )}
                      <p className="mt-0.5 max-w-[14rem] truncate text-xs text-stone-500" title={ticket.original_filename}>
                        {ticket.original_filename}
                      </p>
                    </td>
                    <td className="px-4 py-3 text-stone-800">
                      {ticket.client_name ?? <span className="text-stone-400">—</span>}
                      {ticket.client_hq && (
                        <p className="text-xs text-stone-500">
                          {ticket.client_hq}
                          {ticket.currency ? ` · ${ticket.currency}` : ""}
                        </p>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <TicketStatusBadge status={ticket.status} readyForApproval={ticket.ready_for_approval} />
                      {ticket.error_message && (
                        <p className="mt-1 max-w-xs text-xs text-red-700">{ticket.error_message}</p>
                      )}
                    </td>
                    <td className="px-4 py-3 text-stone-700">
                      {ticket.departments_needed.length === 0 ? (
                        <span className="text-stone-400">—</span>
                      ) : (
                        <ul className="space-y-0.5">
                          {ticket.departments_needed.map((id) => (
                            <li key={id}>
                              <span className="font-mono text-xs text-stone-500">{id}</span> ·{" "}
                              {DEPARTMENTS[id].owner}
                            </li>
                          ))}
                        </ul>
                      )}
                    </td>
                    <td className="px-4 py-3 text-xs text-stone-500">
                      {DATE_FORMAT.format(new Date(ticket.created_at))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <NewTicketToast notices={notices} onDismiss={dismissNotice} />
    </div>
  );
}
