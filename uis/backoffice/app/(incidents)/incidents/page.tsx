"use client";

import dynamic from "next/dynamic";
import { useState } from "react";
import FileUploader from "@/components/incidents/FileUploader";
import { analyzeIncidentsFile, ApiError } from "@/lib/api";
import type { AnalysisSummary } from "@/lib/types";

// Solo se renderizan tras analizar un CSV subido por el usuario — nunca en
// la carga inicial de la ruta. Se difiere su JS con next/dynamic para no
// pagarlo hasta que hay un resultado que mostrar (ver CACHING_REPORT.md).
const SummaryHeader = dynamic(() => import("@/components/incidents/SummaryHeader"));
const InvalidBreakdown = dynamic(() => import("@/components/incidents/InvalidBreakdown"));
const SatisfactionIndex = dynamic(() => import("@/components/incidents/SatisfactionIndex"));
const CategoryBreakdown = dynamic(() => import("@/components/incidents/CategoryBreakdown"));
const StatusBreakdown = dynamic(() => import("@/components/incidents/StatusBreakdown"));
const ExportButton = dynamic(() => import("@/components/incidents/ExportButton"));

export default function IncidentsPage() {
  const [summary, setSummary] = useState<AnalysisSummary | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleFileSelected = async (file: File) => {
    setIsLoading(true);
    setError(null);
    setFileName(file.name);
    try {
      const result = await analyzeIncidentsFile(file);
      setSummary(result);
    } catch (err) {
      setSummary(null);
      setError(
        err instanceof ApiError
          ? err.message
          : "No se pudo conectar con la API. Comprueba que el backend esté en marcha."
      );
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <section className="px-6 py-12">
      <div className="mx-auto w-full max-w-4xl">
          <header className="mb-8">
            <p className="font-mono text-xs uppercase tracking-widest text-teal-700">
              Nexova · Atención postventa
            </p>
            <h1 className="mt-2 text-4xl font-medium tracking-tight text-stone-900">
              Analizador de incidencias
            </h1>
            <p className="mt-3 max-w-2xl text-base leading-7 text-stone-600">
              Sube el CSV exportado del helpdesk para obtener un resumen operativo,
              detectar registros inválidos y descargar los resultados.
            </p>
          </header>

          <FileUploader
            onFileSelected={handleFileSelected}
            isLoading={isLoading}
            fileName={fileName}
          />

          {error && (
            <div className="mt-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {error}
            </div>
          )}

          {summary && (
            <div className="mt-8 space-y-6">
              <SummaryHeader summary={summary} />

              <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
                <InvalidBreakdown summary={summary} />
                <SatisfactionIndex summary={summary} />
                <CategoryBreakdown summary={summary} />
                <StatusBreakdown summary={summary} />
              </div>

              <div className="flex justify-end pt-2">
                <ExportButton />
              </div>
            </div>
          )}
      </div>
    </section>
  );
}