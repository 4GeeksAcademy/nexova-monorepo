import { STATUS_LABELS, type TicketStatus } from "@/lib/rfp";

const STYLES: Partial<Record<TicketStatus, string>> = {
  analizando: "border-amber-300 bg-amber-50 text-amber-800",
  descartado: "border-stone-300 bg-stone-100 text-stone-600",
  analisis_completo: "border-teal-300 bg-teal-50 text-teal-800",
  generando_borrador: "border-sky-300 bg-sky-50 text-sky-800",
  "en_evaluación": "border-sky-300 bg-sky-50 text-sky-800",
  needs_human_review: "border-amber-400 bg-amber-100 text-amber-900",
};

interface TicketStatusBadgeProps {
  status: TicketStatus;
  /** Parte 2 terminada: `en_evaluación` deja de ser "en curso" y pasa a "evaluados". */
  readyForApproval?: boolean;
}

export default function TicketStatusBadge({ status, readyForApproval = false }: TicketStatusBadgeProps) {
  const evaluated = status === "en_evaluación" && readyForApproval;
  const busy = status === "analizando" || status === "generando_borrador" || (status === "en_evaluación" && !readyForApproval);
  const style = evaluated ? "border-teal-300 bg-teal-50 text-teal-800" : STYLES[status] ?? "border-sky-300 bg-sky-50 text-sky-800";

  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 font-mono text-xs ${style}`}>
      {busy && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" aria-hidden />}
      {evaluated ? "Borradores evaluados" : STATUS_LABELS[status]}
    </span>
  );
}
