"use client";

import { DEPARTMENTS, type FinalDocument } from "@/lib/rfp";

/** Documento final consolidado (solo existe cuando todas las secciones activas están aprobadas). */
export default function FinalDocumentView({ ticketId, document }: { ticketId: string; document: FinalDocument }) {
  const download = () => {
    const blob = new Blob([document.content_markdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = window.document.createElement("a");
    link.href = url;
    link.download = `propuesta-${ticketId}.md`;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <section className="rounded-xl border border-teal-300 bg-white p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="font-mono text-xs uppercase tracking-widest text-teal-700">Documento final</p>
          <h2 className="mt-1 text-lg font-medium text-stone-900">Propuesta lista para Ventas</h2>
          <p className="text-sm text-stone-500">
            Generada el {new Date(document.generated_at).toLocaleString("es-ES")} · Moneda {document.currency ?? "—"}
          </p>
        </div>
        <button
          type="button"
          onClick={download}
          className="rounded-lg bg-teal-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-teal-700"
        >
          Descargar (.md)
        </button>
      </div>
      <ul className="mt-4 flex flex-wrap gap-2">
        {document.approvals.map((approval) => (
          <li
            key={approval.department_id}
            className="rounded-full border border-teal-300 bg-teal-50 px-2.5 py-0.5 font-mono text-xs text-teal-800"
          >
            ✓ {DEPARTMENTS[approval.department_id].name}: {approval.approver}
          </li>
        ))}
      </ul>
      <div className="mt-5 max-h-[40rem] overflow-y-auto whitespace-pre-wrap rounded-lg border border-stone-200 bg-stone-50 p-4 text-sm leading-relaxed text-stone-800">
        {document.content_markdown}
      </div>
    </section>
  );
}
