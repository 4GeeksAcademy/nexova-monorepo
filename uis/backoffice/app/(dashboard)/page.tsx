import type { Candidate } from "@hito2-logic/types/models";
import {
  averageTicketResolutionTime,
  countCandidatesByStatus,
} from "@hito2-logic/utils/transformations";
import Link from "next/link";
import SectionViewTracker from "@/components/SectionViewTracker";
import { DASHBOARD_SECTIONS, UPCOMING_SECTIONS } from "@/lib/dashboard-sections";

const CARD = "rounded-2xl border border-stone-300 bg-white/85 p-5 shadow-sm";

interface KpiCardProps {
  label: string;
  value: string;
  /** Contenido SVG (hijos de `<svg>`) del icono del indicador. */
  icon: React.ReactNode;
}

function KpiCard({ label, value, icon }: KpiCardProps) {
  return (
    <article className={CARD}>
      <div className="flex items-start justify-between gap-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-stone-500">{label}</p>
        <span className="rounded-lg border border-stone-200 bg-stone-50 p-1.5 text-teal-700">
          <svg
            viewBox="0 0 24 24"
            className="h-4 w-4"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            aria-hidden="true"
          >
            {icon}
          </svg>
        </span>
      </div>
      <p className="mt-3 text-3xl font-medium tracking-tight text-stone-900">{value}</p>
    </article>
  );
}

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
    <section className="px-6 py-12">
      <SectionViewTracker section="dashboard" />
      <div className="mx-auto w-full max-w-6xl">
        <header className="mb-8">
          <p className="text-xs font-semibold uppercase tracking-widest text-teal-700">Overview</p>
          <h1 className="mt-2 text-3xl font-medium tracking-tight text-stone-900 sm:text-4xl">
            Dashboard Financiero
          </h1>
          <p className="mt-3 max-w-2xl text-base leading-7 text-stone-600">
            Vista inicial del backoffice con resultados de transformación ejecutados en
            servidor usando utilidades compartidas del monorepo.
          </p>
        </header>

        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <KpiCard
            label="Candidatos"
            value={String(mockCandidates.length)}
            icon={
              <>
                <path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
                <circle cx="8.5" cy="7" r="4" />
                <path d="M20 8v6" />
                <path d="M23 11h-6" />
              </>
            }
          />
          <KpiCard
            label="Estados detectados"
            value={String(statusRows.length)}
            icon={
              <>
                <path d="M3 3v18h18" />
                <rect x="7" y="11" width="3" height="7" rx="1" />
                <rect x="12" y="7" width="3" height="11" rx="1" />
                <rect x="17" y="4" width="3" height="14" rx="1" />
              </>
            }
          />
          <KpiCard
            label="Tickets soporte"
            value={String(mockTickets.length)}
            icon={
              <>
                <path d="m9 11 3 3L22 4" />
                <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
              </>
            }
          />
          <KpiCard
            label="Resolución media"
            value={`${avgResolutionHours.toFixed(1)}h`}
            icon={
              <>
                <circle cx="12" cy="12" r="9" />
                <path d="M12 7v5l3 3" />
              </>
            }
          />
        </div>

        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <article className={CARD}>
            <h2 className="text-base font-medium text-stone-900">Conteo por estado de candidato</h2>
            <ul className="mt-4 space-y-2">
              {statusRows.map(([status, count]) => (
                <li
                  key={status}
                  className="flex items-center justify-between rounded-xl border border-stone-200 bg-stone-50 px-3 py-2"
                >
                  <span className="text-sm text-stone-700">{status}</span>
                  <span className="text-sm font-semibold text-stone-900">{count}</span>
                </li>
              ))}
            </ul>
          </article>

          <article className="overflow-hidden rounded-2xl border border-stone-300 bg-stone-900 shadow-sm">
            <div className="flex items-center justify-between border-b border-stone-700 px-5 py-3">
              <h2 className="text-base font-medium text-stone-100">
                Salida serializada de la transformación
              </h2>
              <p className="font-mono text-[11px] uppercase tracking-widest text-stone-400">JSON</p>
            </div>
            <pre className="overflow-x-auto px-5 py-4 font-mono text-xs leading-6 text-teal-300">
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

        <section aria-labelledby="dashboard-sections" className="mt-10">
          <p className="text-xs font-semibold uppercase tracking-widest text-teal-700">Accesos</p>
          <h2
            id="dashboard-sections"
            className="mt-2 text-2xl font-medium tracking-tight text-stone-900"
          >
            Áreas del backoffice
          </h2>
          <ul className="mt-5 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {DASHBOARD_SECTIONS.map((section) => (
              <li key={section.href}>
                <Link
                  href={section.href}
                  prefetch={false}
                  className={`${CARD} group block h-full transition hover:border-teal-600`}
                >
                  <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-teal-700">
                    {section.area}
                  </p>
                  <p className="mt-2 flex items-center justify-between gap-2 text-base font-medium text-stone-900">
                    {section.title}
                    <span
                      aria-hidden="true"
                      className="text-stone-400 transition group-hover:translate-x-0.5 group-hover:text-teal-700"
                    >
                      →
                    </span>
                  </p>
                  <p className="mt-1 text-sm leading-6 text-stone-600">{section.description}</p>
                </Link>
              </li>
            ))}
          </ul>
          <p className="mt-4 text-sm text-stone-500">
            Próximamente: {UPCOMING_SECTIONS.join(" · ")}.
          </p>
        </section>
      </div>
    </section>
  );
}
