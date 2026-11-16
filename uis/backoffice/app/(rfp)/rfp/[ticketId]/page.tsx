"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import DraftCard from "@/components/rfp/DraftCard";
import TicketStatusBadge from "@/components/rfp/TicketStatusBadge";
import {
  DEPARTMENTS,
  RfpError,
  fetchRfpTicket,
  isTicketBusy,
  regenerateDrafts,
  type RfpTicketDetail,
} from "@/lib/rfp";

/**
 * Detalle de un ticket de RFP: lo que Ventas necesita para saber, sin leer el
 * documento original, qué le toca a cada departamento y a quién pedírselo.
 */

const POLL_MS = 3_000;
const START_GRACE_MS = 30_000;

type ViewState =
  | { status: "loading" }
  | { status: "not_found" }
  | { status: "error"; message: string }
  | { status: "ok"; ticket: RfpTicketDetail };

function List({ items, empty }: { items: string[]; empty: string }) {
  if (items.length === 0) return <p className="text-sm text-stone-400">{empty}</p>;
  return (
    <ul className="list-disc space-y-1 pl-5 text-sm text-stone-700">
      {items.map((item) => (
        <li key={item}>{item}</li>
      ))}
    </ul>
  );
}

function Field({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div>
      <dt className="font-mono text-xs uppercase tracking-wider text-stone-500">{label}</dt>
      <dd className="mt-0.5 text-sm text-stone-800">{value || <span className="text-stone-400">No indicado</span>}</dd>
    </div>
  );
}

/** Límite del ciclo generador-evaluador que aplicó la Parte 2 (viene en su handoff). */
function maxIterations(ticket: RfpTicketDetail): number | null {
  return ticket.approval_max_iterations;
}

export default function RfpTicketPage() {
  const params = useParams<{ ticketId: string }>();
  const ticketId = params.ticketId;
  const [state, setState] = useState<ViewState>({ status: "loading" });
  const [refreshKey, setRefreshKey] = useState(0);
  const [regenerating, setRegenerating] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // Tras un 202 el worker tarda unos instantes en recoger la tarea: hasta ver el
  // ticket en marcha se sigue consultando (como mucho START_GRACE_MS).
  const awaitingStartUntil = useRef(0);

  const handleRegenerate = async () => {
    setRegenerating(true);
    setActionError(null);
    try {
      await regenerateDrafts(ticketId);
      awaitingStartUntil.current = Date.now() + START_GRACE_MS;
    } catch (err) {
      setActionError(err instanceof RfpError ? err.message : "No se pudo conectar con la API.");
    } finally {
      setRegenerating(false);
      setRefreshKey((key) => key + 1);
    }
  };

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const load = () => {
      fetchRfpTicket(ticketId)
        .then((ticket) => {
          if (cancelled) return;
          if (!ticket) {
            setState({ status: "not_found" });
            return;
          }
          setState({ status: "ok", ticket });
          const busy = isTicketBusy(ticket);
          if (busy) awaitingStartUntil.current = 0;
          if (busy || Date.now() < awaitingStartUntil.current) timer = setTimeout(load, POLL_MS);
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          setState({
            status: "error",
            message: err instanceof RfpError ? err.message : "No se pudo conectar con la API.",
          });
        });
    };
    load();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [ticketId, refreshKey]);

  const back = (
    <Link href="/rfp" prefetch={false} className="font-mono text-xs text-teal-700 hover:underline">
      ← Volver a la bandeja
    </Link>
  );

  if (state.status === "loading") return <div className="space-y-4">{back}<p className="text-sm text-stone-500">Cargando ticket…</p></div>;
  if (state.status === "not_found") return <div className="space-y-4">{back}<p className="text-sm text-stone-600">El ticket {ticketId} no existe.</p></div>;
  if (state.status === "error") {
    return (
      <div className="space-y-4">
        {back}
        <p className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{state.message}</p>
      </div>
    );
  }

  const { ticket } = state;
  const metadata = ticket.metadata;
  const readability = metadata?.readability;

  return (
    <div className="space-y-10">
      <header className="space-y-3">
        {back}
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="font-mono text-3xl font-medium tracking-tight text-stone-900">{ticket.ticket_id}</h1>
          <TicketStatusBadge status={ticket.status} readyForApproval={ticket.ready_for_approval} />
          {ticket.ready_for_approval ? (
            <span className="rounded-full border border-stone-300 px-2.5 py-0.5 font-mono text-xs text-stone-600">
              Listo para aprobación (Parte 3)
            </span>
          ) : (
            ticket.ready_for_drafting && (
              <span className="rounded-full border border-stone-300 px-2.5 py-0.5 font-mono text-xs text-stone-600">
                Análisis listo para la Parte 2
              </span>
            )
          )}
        </div>
        <p className="text-sm text-stone-500">
          {ticket.original_filename}
          {ticket.rfp_id ? ` · Ref. ${ticket.rfp_id}` : ""}
        </p>
        {ticket.error_message && (
          <p className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{ticket.error_message}</p>
        )}
      </header>

      {ticket.status === "analizando" && (
        <p className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          Conversión a Markdown y agentes en curso. Esta página se actualiza sola.
        </p>
      )}

      {isTicketBusy(ticket) && ticket.status !== "analizando" && (
        <p className="rounded-lg border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-800">
          Generando y evaluando los borradores por departamento. Esta página se actualiza sola.
        </p>
      )}

      {ticket.ready_for_drafting && (
        <section>
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-medium text-stone-900">Borradores de la propuesta</h2>
              <p className="text-sm text-stone-500">
                Un generador por departamento; legibilidad, pertinencia y cumplimiento evaluados en paralelo.
              </p>
            </div>
            <button
              type="button"
              onClick={handleRegenerate}
              disabled={regenerating || isTicketBusy(ticket)}
              className="rounded-lg border border-teal-600 px-4 py-2 text-sm font-medium text-teal-700 transition hover:bg-teal-50 disabled:cursor-not-allowed disabled:border-stone-300 disabled:text-stone-400"
            >
              {regenerating ? "Encolando…" : "Regenerar borradores"}
            </button>
          </div>
          {actionError && (
            <p className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{actionError}</p>
          )}
          {ticket.sections.some((s) => s.section_status) ? (
            <div className="grid gap-4">
              {ticket.sections.map((section) => (
                <DraftCard
                  key={section.department_id}
                  section={section}
                  maxIterations={maxIterations(ticket)}
                />
              ))}
            </div>
          ) : (
            <p className="text-sm text-stone-500">Todavía no se han generado borradores para este ticket.</p>
          )}
        </section>
      )}

      {ticket.status === "descartado" && (
        <section className="rounded-xl border border-stone-200 bg-white p-6">
          <h2 className="text-lg font-medium text-stone-900">Documento descartado</h2>
          <p className="mt-2 text-sm text-stone-700">
            {ticket.classification?.reason || "El clasificador determinó que no es una RFP de cliente."}
          </p>
        </section>
      )}

      {ticket.synthesis && (
        <section className="rounded-xl border border-teal-200 bg-teal-50/60 p-6">
          <p className="font-mono text-xs uppercase tracking-widest text-teal-700">Resumen para Ventas</p>
          <p className="mt-3 text-base leading-relaxed text-stone-800">{ticket.synthesis.summary_for_sales}</p>
          {ticket.synthesis.next_steps.length > 0 && (
            <div className="mt-4">
              <h3 className="text-sm font-medium text-stone-900">Próximos pasos</h3>
              <div className="mt-2">
                <List items={ticket.synthesis.next_steps} empty="" />
              </div>
            </div>
          )}
          {ticket.synthesis.conflicts.length > 0 && (
            <div className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-4">
              <h3 className="text-sm font-medium text-amber-900">Contradicciones detectadas (pendientes de Ventas)</h3>
              <ul className="mt-2 space-y-1 text-sm text-amber-900">
                {ticket.synthesis.conflicts.map((conflict) => (
                  <li key={conflict.concept}>
                    <span className="font-medium">{conflict.concept}:</span>{" "}
                    {conflict.values.map((v) => `${v.value} (${v.source})`).join(" vs ")}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}

      {ticket.sections.length > 0 && (
        <section>
          <h2 className="mb-4 text-lg font-medium text-stone-900">Qué pedir a cada departamento</h2>
          <div className="grid gap-4 lg:grid-cols-2">
            {ticket.sections.map((section) => (
              <article key={section.department_id} className="rounded-xl border border-stone-200 bg-white p-6">
                <p className="font-mono text-xs uppercase tracking-wider text-stone-500">{section.department_id}</p>
                <h3 className="mt-1 text-lg font-medium text-stone-900">{DEPARTMENTS[section.department_id].name}</h3>
                <p className="text-sm text-teal-700">Responsable: {section.owner_name}</p>

                <h4 className="mt-5 text-sm font-medium text-stone-900">Aspectos clave</h4>
                <div className="mt-1"><List items={section.key_aspects} empty="Sin aspectos registrados." /></div>

                <h4 className="mt-4 text-sm font-medium text-stone-900">Pedir a {section.owner_name}</h4>
                <div className="mt-1"><List items={section.requests_to_owner} empty="Sin peticiones." /></div>

                <h4 className="mt-4 text-sm font-medium text-stone-900">Preguntas abiertas al cliente</h4>
                <div className="mt-1"><List items={section.open_questions} empty="Ninguna." /></div>

                {section.figures_cited.length > 0 && (
                  <>
                    <h4 className="mt-4 text-sm font-medium text-stone-900">Cifras citadas de la RFP</h4>
                    <ul className="mt-1 space-y-1 text-sm text-stone-700">
                      {section.figures_cited.map((figure) => (
                        <li key={`${figure.concept}-${figure.value}`}>
                          <span className="font-medium">{figure.value}</span> — {figure.concept}{" "}
                          <span className="text-xs italic text-stone-500">“{figure.quote}”</span>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </article>
            ))}
          </div>
        </section>
      )}

      {metadata && (
        <section className="grid gap-4 lg:grid-cols-3">
          <div className="rounded-xl border border-stone-200 bg-white p-6 lg:col-span-2">
            <h2 className="text-lg font-medium text-stone-900">Metadatos de la RFP</h2>
            <dl className="mt-4 grid gap-4 sm:grid-cols-2">
              <Field label="Cliente" value={metadata.client_name} />
              <Field label="Sede → moneda" value={`${metadata.client_hq}${metadata.currency ? ` → ${metadata.currency}` : " (moneda por confirmar)"}`} />
              <Field label="Tipo" value={metadata.rfp_type} />
              <Field label="Fecha límite" value={metadata.deadline ?? metadata.deadline_text} />
              <Field label="Presupuesto de referencia" value={metadata.budget_range} />
              <Field label="Contacto" value={metadata.client_contact} />
            </dl>
            <div className="mt-4">
              <dt className="font-mono text-xs uppercase tracking-wider text-stone-500">Alcance</dt>
              <p className="mt-0.5 text-sm text-stone-800">{metadata.scope ?? "No indicado"}</p>
            </div>
            <div className="mt-4">
              <dt className="font-mono text-xs uppercase tracking-wider text-stone-500">Servicios solicitados</dt>
              <div className="mt-1"><List items={metadata.services_requested} empty="No indicados." /></div>
            </div>
            {metadata.unmapped_services.length > 0 && (
              <div className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-4">
                <p className="text-sm font-medium text-amber-900">Servicios sin departamento en Nexova</p>
                <div className="mt-1"><List items={metadata.unmapped_services} empty="" /></div>
              </div>
            )}
          </div>

          <div className="rounded-xl border border-stone-200 bg-white p-6">
            <h2 className="text-lg font-medium text-stone-900">Legibilidad y costo</h2>
            {readability ? (
              <dl className="mt-4 space-y-3">
                <Field label="Costo estimado" value={readability.cost_tier} />
                <Field label="Palabras / tokens" value={`${readability.word_count ?? "—"} / ~${readability.estimated_tokens ?? "—"}`} />
                <Field label="Flesch-Kincaid (grado)" value={readability.flesch_kincaid_grade?.toString()} />
                <Field label="Gunning Fog" value={readability.gunning_fog?.toString()} />
                {readability.note && <p className="text-xs text-stone-500">{readability.note}</p>}
              </dl>
            ) : (
              <p className="mt-2 text-sm text-stone-400">Sin métricas.</p>
            )}
          </div>
        </section>
      )}
    </div>
  );
}
