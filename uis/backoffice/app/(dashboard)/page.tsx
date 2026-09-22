import type { Candidate } from "@hito2-logic/types/models";
import {
  averageTicketResolutionTime,
  countCandidatesByStatus,
} from "@hito2-logic/utils/transformations";
import SectionViewTracker from "@/components/SectionViewTracker";

export default function Home() {
  const mockCandidates: Candidate[] = [
    {
      id: 1,
      fullName: "Lucia Serrano",
      email: "lucia.serrano@nexova.com",
      phone: "+34 600 111 222",
      skills: ["Excel", "Power BI", "Contabilidad"],
      yearsOfExperience: 5,
      currentPosition: "Analista Financiera",
      status: "enProceso",
      cvScore: 88,
      appliedDate: new Date("2026-06-01T09:00:00.000Z"),
    },
    {
      id: 2,
      fullName: "Carlos Vega",
      email: "carlos.vega@nexova.com",
      phone: "+34 600 333 444",
      skills: ["Forecast", "SQL", "Control de Gestión"],
      yearsOfExperience: 7,
      currentPosition: "Controller",
      status: "entrevistado",
      cvScore: 92,
      appliedDate: new Date("2026-06-03T11:30:00.000Z"),
    },
    {
      id: 3,
      fullName: "Ana Morales",
      email: "ana.morales@nexova.com",
      phone: "+34 600 555 666",
      skills: ["Tesorería", "Finanzas Corporativas"],
      yearsOfExperience: 4,
      currentPosition: "Especialista de Tesorería",
      status: "enProceso",
      cvScore: 84,
      appliedDate: new Date("2026-06-05T08:15:00.000Z"),
    },
  ];

  const mockTickets = [
    {
      id: 10,
      clientId: 201,
      agentName: "Sergio Diaz",
      channel: "email" as const,
      priority: "alta" as const,
      status: "resuelto" as const,
      createdAt: new Date("2026-06-08T10:00:00.000Z"),
      resolvedAt: new Date("2026-06-08T14:00:00.000Z"),
      resolutionTimeHours: 4,
      slaTarget: 8,
    },
    {
      id: 11,
      clientId: 202,
      agentName: "Marta Reyes",
      channel: "chat" as const,
      priority: "media" as const,
      status: "resuelto" as const,
      createdAt: new Date("2026-06-09T09:00:00.000Z"),
      resolvedAt: new Date("2026-06-09T12:00:00.000Z"),
      resolutionTimeHours: 3,
      slaTarget: 6,
    },
    {
      id: 12,
      clientId: 203,
      agentName: "Javier Leon",
      channel: "teléfono" as const,
      priority: "crítica" as const,
      status: "enProgreso" as const,
      createdAt: new Date("2026-06-10T07:00:00.000Z"),
      resolvedAt: null,
      resolutionTimeHours: null,
      slaTarget: 4,
    },
  ];

  const candidatesByStatus = countCandidatesByStatus(mockCandidates);
  const avgResolutionHours = averageTicketResolutionTime(mockTickets);

  const statusRows = Object.entries(candidatesByStatus);

  return (
    <section className="mx-auto flex w-full max-w-6xl flex-col gap-6">
      <SectionViewTracker section="dashboard" />
      <header className="relative overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/60 p-6 shadow-[0_12px_40px_-30px_rgba(14,165,233,0.45)] backdrop-blur-xl">
        <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-cyan-300/60 to-transparent" />
        <p className="text-xs uppercase tracking-[0.2em] text-slate-500">Overview</p>
        <h2 className="mt-2 text-3xl font-semibold text-slate-100 sm:text-4xl">
          Dashboard Financiero
        </h2>
        <p className="mt-3 max-w-2xl text-sm text-slate-300">
          Vista inicial del backoffice con resultados de transformación ejecutados en
          servidor usando utilidades compartidas del monorepo.
        </p>
      </header>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <article className="rounded-xl border border-slate-800 bg-slate-900 p-4 shadow-[0_24px_40px_-36px_rgba(0,0,0,0.8)]">
          <div className="flex items-start justify-between">
            <p className="text-xs uppercase tracking-wide text-slate-400">Candidatos</p>
            <span className="rounded-lg border border-slate-700 bg-slate-950 p-1.5 text-slate-400">
              <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8">
                <path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
                <circle cx="8.5" cy="7" r="4" />
                <path d="M20 8v6" />
                <path d="M23 11h-6" />
              </svg>
            </span>
          </div>
          <p className="mt-3 text-3xl font-semibold text-slate-100">{mockCandidates.length}</p>
        </article>
        <article className="rounded-xl border border-slate-800 bg-slate-900 p-4 shadow-[0_24px_40px_-36px_rgba(0,0,0,0.8)]">
          <div className="flex items-start justify-between">
            <p className="text-xs uppercase tracking-wide text-slate-400">Estados Detectados</p>
            <span className="rounded-lg border border-slate-700 bg-slate-950 p-1.5 text-slate-400">
              <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8">
                <path d="M3 3v18h18" />
                <rect x="7" y="11" width="3" height="7" rx="1" />
                <rect x="12" y="7" width="3" height="11" rx="1" />
                <rect x="17" y="4" width="3" height="14" rx="1" />
              </svg>
            </span>
          </div>
          <p className="mt-3 text-3xl font-semibold text-slate-100">{statusRows.length}</p>
        </article>
        <article className="rounded-xl border border-slate-800 bg-slate-900 p-4 shadow-[0_24px_40px_-36px_rgba(0,0,0,0.8)]">
          <div className="flex items-start justify-between">
            <p className="text-xs uppercase tracking-wide text-slate-400">Tickets Soporte</p>
            <span className="rounded-lg border border-slate-700 bg-slate-950 p-1.5 text-slate-400">
              <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8">
                <path d="m9 11 3 3L22 4" />
                <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
              </svg>
            </span>
          </div>
          <p className="mt-3 text-3xl font-semibold text-slate-100">{mockTickets.length}</p>
        </article>
        <article className="rounded-xl border border-slate-800 bg-slate-900 p-4 shadow-[0_24px_40px_-36px_rgba(0,0,0,0.8)]">
          <div className="flex items-start justify-between">
            <p className="text-xs uppercase tracking-wide text-slate-400">Resolución Media</p>
            <span className="rounded-lg border border-slate-700 bg-slate-950 p-1.5 text-slate-400">
              <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8">
                <circle cx="12" cy="12" r="9" />
                <path d="M12 7v5l3 3" />
              </svg>
            </span>
          </div>
          <p className="mt-3 text-3xl font-semibold text-slate-100">
            {avgResolutionHours.toFixed(1)}h
          </p>
        </article>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <article className="rounded-xl border border-slate-800 bg-slate-900 p-4 shadow-[0_24px_40px_-36px_rgba(0,0,0,0.8)]">
          <h3 className="text-sm font-semibold text-slate-100">Conteo por estado de candidato</h3>
          <div className="mt-4 space-y-2">
            {statusRows.map(([status, count]) => (
              <div
                key={status}
                className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-950/80 px-3 py-2"
              >
                <span className="text-sm text-slate-300">{status}</span>
                <span className="text-sm font-semibold text-slate-100">{count}</span>
              </div>
            ))}
          </div>
        </article>

        <article className="overflow-hidden rounded-xl border border-slate-800 bg-black shadow-[0_24px_40px_-36px_rgba(0,0,0,0.9)]">
          <div className="flex items-center justify-between border-b border-slate-800 bg-slate-950/70 px-4 py-2.5">
            <div className="flex items-center gap-2">
              <span className="h-2.5 w-2.5 rounded-full bg-rose-400" />
              <span className="h-2.5 w-2.5 rounded-full bg-amber-300" />
              <span className="h-2.5 w-2.5 rounded-full bg-emerald-400" />
            </div>
            <p className="text-[11px] uppercase tracking-[0.18em] text-slate-500">Serialized Output</p>
          </div>
          <h3 className="px-4 pt-4 text-sm font-semibold text-slate-200">
            Salida serializada de la transformación
          </h3>
          <pre className="mt-2 overflow-x-auto px-4 pb-4 font-mono text-xs leading-6 text-emerald-400">
            {JSON.stringify(
              {
                inputCandidates: mockCandidates.length,
                resultCountCandidatesByStatus: candidatesByStatus,
                resultAverageTicketResolutionTime: avgResolutionHours,
              },
              null,
              2
            )}
          </pre>
        </article>
      </div>
    </section>
  );
}