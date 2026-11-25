"use client";

import { useState } from "react";
import { useAuth } from "@/lib/AuthContext";
import { can } from "@/lib/permissions";
import {
  APPROVAL_STATUS_LABELS,
  DECISION_LABELS,
  DEPARTMENTS,
  RfpError,
  openApprovals,
  submitDecision,
  type Decision,
  type DepartmentSection,
} from "@/lib/rfp";

/**
 * Pausa de aprobación humana de UNA sección (Parte 3). Cada departamento decide
 * por su cuenta: aprobar aquí reanuda solo la rama de este departamento.
 *
 * Quien registra la decisión lo hace en nombre del responsable nombrado (o del
 * árbitro fijo si el conflicto se escaló); el backend guarda ambos.
 */

const STATUS_STYLES: Record<string, string> = {
  pendiente: "border-amber-300 bg-amber-50 text-amber-900",
  procesando: "border-sky-300 bg-sky-50 text-sky-800",
  aprobada: "border-teal-300 bg-teal-50 text-teal-800",
  cambios_solicitados: "border-sky-300 bg-sky-50 text-sky-800",
  rechazada: "border-red-300 bg-red-50 text-red-700",
};

const DECISION_STYLES: Record<Decision, string> = {
  approve: "border-teal-600 bg-teal-600 text-white hover:bg-teal-700",
  request_changes: "border-amber-500 text-amber-800 hover:bg-amber-50",
  reject: "border-red-500 text-red-700 hover:bg-red-50",
};

function formatDate(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("es-ES");
}

interface ApprovalPanelProps {
  ticketId: string;
  section: DepartmentSection;
  maxRounds: number | null;
  onChanged: () => void;
}

export default function ApprovalPanel({ ticketId, section, maxRounds, onChanged }: ApprovalPanelProps) {
  const { user } = useAuth();
  const canDecide = can(user, "approve");
  const [comment, setComment] = useState("");
  const [sending, setSending] = useState<Decision | "reopen" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { approval } = section;
  const owner = DEPARTMENTS[section.department_id].owner;
  const actingAs = approval.expected_approver ?? owner;
  const escalated = approval.status === "pendiente" && actingAs !== owner;

  const decide = async (decision: Decision) => {
    setSending(decision);
    setError(null);
    try {
      await submitDecision(ticketId, section.department_id, { decision, acting_as: actingAs, comment });
      setComment("");
      onChanged();
    } catch (err) {
      setError(err instanceof RfpError ? err.message : "No se pudo registrar la decisión.");
    } finally {
      setSending(null);
    }
  };

  const reopen = async () => {
    setSending("reopen");
    setError(null);
    try {
      await openApprovals(ticketId, section.department_id);
      onChanged();
    } catch (err) {
      setError(err instanceof RfpError ? err.message : "No se pudo reabrir la aprobación.");
    } finally {
      setSending(null);
    }
  };

  return (
    <div className="mt-5 rounded-lg border border-stone-200 bg-stone-50/60 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm font-medium text-stone-900">Aprobación humana</h4>
        {approval.status && (
          <span className={`rounded-full border px-2.5 py-0.5 font-mono text-xs ${STATUS_STYLES[approval.status]}`}>
            {APPROVAL_STATUS_LABELS[approval.status]}
          </span>
        )}
      </div>
      <p className="mt-1 font-mono text-xs text-stone-500">
        Ronda de cambios {approval.round}
        {maxRounds ? ` de ${maxRounds}` : ""}
        {approval.requested_at ? ` · pausa desde ${formatDate(approval.requested_at)}` : ""}
      </p>

      {approval.status === "aprobada" && (
        <p className="mt-3 text-sm text-teal-800">
          Aprobada por <span className="font-medium">{approval.approver}</span> el {formatDate(approval.approved_at)}
          {approval.decided_by_user ? ` (registrada por ${approval.decided_by_user})` : ""}.
        </p>
      )}

      {approval.status === "pendiente" && !canDecide && (
        <p className="mt-3 rounded-lg border border-stone-300 bg-white px-3 py-2 text-sm text-stone-700">
          Pendiente de aprobación. Decidir (aprobar, pedir cambios o rechazar) requiere el rol de supervisor o superior.
        </p>
      )}

      {approval.status === "pendiente" && canDecide && (
        <div className="mt-3 space-y-3">
          {escalated && (
            <p className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
              Conflicto escalado: decide el árbitro fijo, <span className="font-medium">{actingAs}</span>.
            </p>
          )}
          <p className="text-sm text-stone-700">
            Decides en nombre de <span className="font-medium">{actingAs}</span>
            {escalated ? " (árbitro)" : ` (responsable de ${DEPARTMENTS[section.department_id].name})`}.
          </p>
          <textarea
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            rows={3}
            maxLength={2000}
            placeholder="Comentario (obligatorio para pedir cambios o rechazar)"
            className="w-full rounded-lg border border-stone-300 bg-white px-3 py-2 text-sm text-stone-800 focus:border-teal-600 focus:outline-none"
          />
          <div className="flex flex-wrap gap-2">
            {approval.allowed_decisions.map((decision) => (
              <button
                key={decision}
                type="button"
                onClick={() => decide(decision)}
                disabled={sending !== null}
                className={`rounded-lg border px-4 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${DECISION_STYLES[decision]}`}
              >
                {sending === decision ? "Enviando…" : `${DECISION_LABELS[decision]} como ${actingAs}`}
              </button>
            ))}
          </div>
          {!approval.allowed_decisions.includes("request_changes") && (
            <p className="text-xs text-stone-500">
              Se agotó el límite de rondas de cambios: solo se puede aprobar o rechazar.
            </p>
          )}
        </div>
      )}

      {approval.status === "rechazada" && (
        <div className="mt-3 space-y-2">
          <p className="text-sm text-red-700">
            Sección rechazada{approval.comment ? `: ${approval.comment}` : "."} El documento final no se generará
            hasta reabrir y aprobar esta sección.
          </p>
          {canDecide && (
          <button
            type="button"
            onClick={reopen}
            disabled={sending !== null}
            className="rounded-lg border border-teal-600 px-4 py-2 text-sm font-medium text-teal-700 transition hover:bg-teal-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {sending === "reopen" ? "Reabriendo…" : "Reabrir aprobación"}
          </button>
          )}
        </div>
      )}

      {error && <p className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

      {approval.history.length > 0 && (
        <details className="mt-4">
          <summary className="cursor-pointer text-xs font-medium text-stone-600">
            Historial de decisiones ({approval.history.length})
          </summary>
          <ol className="mt-2 space-y-1 text-xs text-stone-600">
            {approval.history.map((entry, index) => (
              <li key={`${entry.round}-${entry.decision}-${index}`}>
                <span className="font-mono">R{entry.round}</span> · {DECISION_LABELS[entry.decision]} por{" "}
                {entry.acting_as ?? "—"}
                {entry.by_rule.length > 0 ? ` (regla: ${entry.by_rule.join(", ")})` : ""}
                {entry.decided_by_user ? ` · registrada por ${entry.decided_by_user}` : ""}
                {entry.comment ? ` — “${entry.comment}”` : ""}
              </li>
            ))}
          </ol>
        </details>
      )}
    </div>
  );
}
