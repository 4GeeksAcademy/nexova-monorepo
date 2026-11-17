import { STATUS_LABELS, type TicketStatus } from "@/lib/rfp";

const STYLES: Partial<Record<TicketStatus, string>> = {
  analizando: "border-amber-300 bg-amber-50 text-amber-800",
  descartado: "border-stone-300 bg-stone-100 text-stone-600",
  analisis_completo: "border-teal-300 bg-teal-50 text-teal-800",
};

export default function TicketStatusBadge({ status }: { status: TicketStatus }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 font-mono text-xs ${
        STYLES[status] ?? "border-sky-300 bg-sky-50 text-sky-800"
      }`}
    >
      {status === "analizando" && (
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-500" aria-hidden />
      )}
      {STATUS_LABELS[status]}
    </span>
  );
}
