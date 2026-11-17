"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import PdfUploader from "@/components/rfp/PdfUploader";
import TicketStatusBadge from "@/components/rfp/TicketStatusBadge";
import { DEPARTMENTS, RfpError, fetchRfpTickets, uploadRfp, type RfpTicketSummary } from "@/lib/rfp";

/**
 * Bandeja de RFPs (modo ticket). Cada PDF subido crea un ticket; el análisis
 * corre en segundo plano y esta página consulta el estado cada pocos segundos
 * mientras quede alguno en `analizando`.
 */

const POLL_MS = 3_000;
const ERROR_POLL_MS = 10_000;
const DATE_FORMAT = new Intl.DateTimeFormat("es-ES", { dateStyle: "medium", timeStyle: "short" });

function errorMessage(err: unknown): string {
  if (err instanceof RfpError) {
    return err.status === 403 ? "Solo el personal interno de Nexova puede ver las RFPs." : err.message;
  }
  return "No se pudo conectar con la API. Revisa que el backend esté levantado.";
}

export default function RfpInboxPage() {
  const [tickets, setTickets] = useState<RfpTicketSummary[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const load = () => {
      fetchRfpTickets()
        .then((list) => {
          if (cancelled) return;
          setTickets(list);
          setLoadError(null);
          if (list.some((t) => t.status === "analizando")) timer = setTimeout(load, POLL_MS);
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          setLoadError(errorMessage(err));
          timer = setTimeout(load, ERROR_POLL_MS);
        });
    };
    load();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [refreshKey]);

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
        <h2 className="mb-4 text-lg font-medium text-stone-900">Tickets</h2>
        {loadError && (
          <p className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{loadError}</p>
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
                  <tr key={ticket.ticket_id} className="align-top hover:bg-stone-50/70">
                    <td className="px-4 py-3">
                      <Link
                        href={`/rfp/${ticket.ticket_id}`}
                        prefetch={false}
                        className="font-mono text-teal-700 underline-offset-2 hover:underline"
                      >
                        {ticket.ticket_id}
                      </Link>
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
                      <TicketStatusBadge status={ticket.status} />
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
    </div>
  );
}
