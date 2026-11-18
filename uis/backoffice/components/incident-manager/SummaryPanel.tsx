"use client";

import { useEffect, useState } from "react";
import {
  BRANCHES,
  BRANCH_LABELS,
  CATEGORIES,
  CATEGORY_LABELS,
  ORIGINS,
  ORIGIN_LABELS,
  STATUSES,
  STATUS_LABELS,
} from "@/lib/incident-manager-domain";
import {
  CONNECTION_ERROR,
  IncidentManagerError,
  fetchSummary,
  type Counts,
  type IncidentSummary,
} from "@/lib/incident-manager";

interface Result {
  attempt: number;
  summary: IncidentSummary | null;
  error: string | null;
}

function Block({ title, keys, labels, counts, total }: {
  title: string;
  keys: readonly string[];
  labels: Record<string, string>;
  counts: Counts;
  total: number;
}) {
  return (
    <section className="rounded-2xl border border-stone-200 bg-white p-5 shadow-sm">
      <h2 className="text-sm font-medium uppercase tracking-wide text-stone-500">{title}</h2>
      <ul className="mt-4 space-y-3">
        {keys.map((key) => {
          const count = counts[key] ?? 0;
          const pct = total > 0 ? Math.round((count / total) * 100) : 0;
          return (
            <li key={key}>
              <div className="flex items-baseline justify-between text-sm">
                <span className="text-stone-700">{labels[key] ?? key}</span>
                <span className="font-mono text-stone-900">{count}</span>
              </div>
              <div className="mt-1 h-2 overflow-hidden rounded-full bg-stone-100" aria-hidden="true">
                <div className="h-full rounded-full bg-teal-600" style={{ width: `${pct}%` }} />
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** Métricas del endpoint `/summary`. Tiene su propio estado de carga/error: si falla, no rompe la página. */
export default function SummaryPanel() {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<Result | null>(null);
  const isLoading = result?.attempt !== attempt;

  useEffect(() => {
    let cancelled = false;
    fetchSummary()
      .then((summary) => {
        if (!cancelled) setResult({ attempt, summary, error: null });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setResult({
            attempt,
            summary: null,
            error: err instanceof IncidentManagerError ? err.message : CONNECTION_ERROR,
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  if (isLoading || !result) {
    return (
      <p role="status" className="rounded-lg border border-stone-200 bg-white px-4 py-6 text-sm text-stone-600">
        Cargando métricas…
      </p>
    );
  }

  if (result.error || !result.summary) {
    return (
      <div role="alert" className="rounded-lg border border-red-200 bg-red-50 px-4 py-4 text-sm text-red-700">
        <p>{result.error}</p>
        <button
          type="button"
          onClick={() => setAttempt((n) => n + 1)}
          className="mt-3 rounded-full border border-red-300 bg-white px-4 py-1.5 font-medium text-red-700 transition hover:bg-red-100"
        >
          Reintentar
        </button>
      </div>
    );
  }

  const { summary } = result;
  return (
    <div>
      <p className="mb-6 text-sm text-stone-600">
        Total de incidencias: <span className="font-mono text-lg text-stone-900">{summary.total}</span>
        {summary.total === 0 && " — todavía no hay incidencias registradas."}
      </p>
      <div className="grid gap-6 md:grid-cols-2">
        <Block title="Por estado" keys={STATUSES} labels={STATUS_LABELS} counts={summary.by_status} total={summary.total} />
        <Block title="Por origen" keys={ORIGINS} labels={ORIGIN_LABELS} counts={summary.by_origin} total={summary.total} />
        <Block title="Por categoría" keys={CATEGORIES} labels={CATEGORY_LABELS} counts={summary.by_category} total={summary.total} />
        <Block title="Por sede" keys={BRANCHES} labels={BRANCH_LABELS} counts={summary.by_branch} total={summary.total} />
      </div>
    </div>
  );
}
