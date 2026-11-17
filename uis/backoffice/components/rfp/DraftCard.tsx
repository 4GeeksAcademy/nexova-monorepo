import {
  DEPARTMENTS,
  SECTION_STATUS_LABELS,
  type DepartmentSection,
  type EvaluationResult,
} from "@/lib/rfp";

/**
 * Borrador de la propuesta de un departamento (Parte 2) con su `EvaluationResult`:
 * una insignia por evaluador, violaciones con su regla del BRIEFING y, si agotó
 * el límite de iteraciones, el aviso de borrador provisional.
 */

function CheckBadge({ label, pass }: { label: string; pass: boolean }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 font-mono text-xs ${
        pass ? "border-teal-300 bg-teal-50 text-teal-800" : "border-red-300 bg-red-50 text-red-700"
      }`}
    >
      {pass ? "✓" : "✗"} {label}
    </span>
  );
}

function Problems({ evaluation }: { evaluation: EvaluationResult }) {
  const items = [
    ...evaluation.compliance.violations.map((v) => ({ key: `c-${v.rule_id}-${v.message}`, tag: v.rule_id, text: v.message })),
    ...evaluation.relevance.missing_aspects.map((a) => ({ key: `r-${a}`, tag: "pertinencia", text: `No responde a: ${a}` })),
    ...evaluation.readability.problems.map((p) => ({ key: `l-${p}`, tag: "legibilidad", text: p })),
  ];
  if (items.length === 0) return null;
  return (
    <ul className="mt-3 space-y-1 text-sm text-red-800">
      {items.map((item) => (
        <li key={item.key}>
          <span className="font-mono text-xs text-red-600">[{item.tag}]</span> {item.text}
        </li>
      ))}
    </ul>
  );
}

export default function DraftCard({ section, maxIterations }: { section: DepartmentSection; maxIterations: number | null }) {
  const department = DEPARTMENTS[section.department_id];
  const evaluation = section.evaluation;
  const needsReview = section.section_status === "needs_human_review";
  const working = section.section_status === "generando" || section.section_status === "evaluando";

  return (
    <article
      className={`rounded-xl border bg-white p-6 ${needsReview ? "border-amber-400" : "border-stone-200"}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="font-mono text-xs uppercase tracking-wider text-stone-500">{section.department_id}</p>
          <h3 className="mt-1 text-lg font-medium text-stone-900">{department.name}</h3>
          <p className="text-sm text-teal-700">Responsable: {section.owner_name}</p>
        </div>
        <div className="text-right">
          {section.section_status && (
            <p className={`font-mono text-xs ${needsReview ? "text-amber-700" : working ? "text-sky-700" : "text-teal-700"}`}>
              {SECTION_STATUS_LABELS[section.section_status]}
            </p>
          )}
          <p className="mt-0.5 font-mono text-xs text-stone-500">
            Iteración {section.iteration_count}
            {maxIterations ? ` de ${maxIterations}` : ""}
          </p>
        </div>
      </div>

      {needsReview && (
        <p className="mt-4 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <span className="font-medium">Borrador provisional: requiere revisión humana.</span> Agotó el límite de
          iteraciones sin pasar la evaluación. Se muestra el último borrador con lo que falta corregir.
        </p>
      )}

      {evaluation && (
        <div className="mt-4">
          <div className="flex flex-wrap gap-2">
            <CheckBadge label="Legibilidad" pass={evaluation.readability.pass} />
            <CheckBadge label="Pertinencia" pass={evaluation.relevance.pass} />
            <CheckBadge label="Cumplimiento" pass={evaluation.compliance.pass} />
          </div>
          <p className="mt-2 text-xs text-stone-500">
            Reglas verificadas: {evaluation.compliance.rule_ids.join(", ")}
          </p>
          <Problems evaluation={evaluation} />
        </div>
      )}

      {section.draft_content ? (
        <div className="mt-5 max-h-[32rem] overflow-y-auto whitespace-pre-wrap rounded-lg border border-stone-200 bg-stone-50 p-4 text-sm leading-relaxed text-stone-800">
          {section.draft_content}
        </div>
      ) : (
        <p className="mt-5 text-sm text-stone-400">{working ? "Redactando el primer borrador…" : "Sin borrador."}</p>
      )}
    </article>
  );
}
