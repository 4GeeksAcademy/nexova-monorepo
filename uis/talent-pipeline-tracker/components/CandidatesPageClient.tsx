"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { createRecord, getAllRecords } from "@/lib/api";
import { useAuth } from "@/lib/AuthContext";
import {
  getStageLabel,
  getStatusLabel,
  STAGE_OPTIONS,
  STATUS_OPTIONS,
  type AsyncUiState,
  type CandidatePayload,
  type CandidateRecord,
  type CandidateStage,
  type CandidateStatus,
} from "@/types";

function isValidStatus(value: string | null): value is CandidateStatus {
  return STATUS_OPTIONS.some((option) => option.value === value);
}

function isValidStage(value: string | null): value is CandidateStage {
  return STAGE_OPTIONS.some((option) => option.value === value);
}

const initialCreateForm: CandidatePayload = {
  full_name: "",
  email: "",
  phone: "",
  position: "",
  linkedin_url: "",
  cv_url: "",
  experience_years: 0,
  status: "in_progress",
  stage: "review",
  applied_at: new Date().toISOString().slice(0, 10),
  english_level: "C1",
};

export default function CandidatesPageClient() {
  const { user, logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const searchParamsString = searchParams.toString();

  const [records, setRecords] = useState<CandidateRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>("");
  const [attempt, setAttempt] = useState(0);

  const [searchInput, setSearchInput] = useState("");
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [createForm, setCreateForm] = useState<CandidatePayload>(initialCreateForm);
  const [createState, setCreateState] = useState<AsyncUiState>({ status: "idle" });

  const selectedStatus = useMemo(() => {
    const value = searchParams.get("status");
    return isValidStatus(value) ? value : "";
  }, [searchParams]);

  const selectedStage = useMemo(() => {
    const value = searchParams.get("stage");
    return isValidStage(value) ? value : "";
  }, [searchParams]);

  const filteredRecords = useMemo(() => {
    const term = searchInput.trim().toLowerCase();

    if (!term) {
      return records;
    }

    return records.filter((record) => {
      const fullName = record.full_name.toLowerCase();
      const email = record.email.toLowerCase();
      return fullName.includes(term) || email.includes(term);
    });
  }, [records, searchInput]);

  useEffect(() => {
    async function loadRecords() {
      setLoading(true);
      setError("");

      try {
        const filters = {
          ...(selectedStatus ? { status: selectedStatus } : {}),
          ...(selectedStage ? { stage: selectedStage } : {}),
        };

        const allRecords = await getAllRecords(filters);
        setRecords(allRecords);
      } catch (loadError) {
        const message =
          loadError instanceof Error
            ? loadError.message
            : "No se pudieron cargar las candidaturas";
        setError(message);
      } finally {
        setLoading(false);
      }
    }

    loadRecords();
  }, [selectedStage, selectedStatus, attempt]);

  function updateFilter(name: "status" | "stage", value: string) {
    const params = new URLSearchParams(searchParamsString);

    if (value) {
      params.set(name, value);
    } else {
      params.delete(name);
    }

    const nextQuery = params.toString();

    if (nextQuery === searchParamsString) {
      return;
    }

    router.replace(nextQuery ? `${pathname}?${nextQuery}` : pathname, {
      scroll: false,
    });
  }

  async function handleCreateCandidate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setCreateState({ status: "loading", message: "Guardando candidatura..." });

    try {
      const created = await createRecord({
        ...createForm,
        experience_years: Number(createForm.experience_years),
        applied_at: new Date(createForm.applied_at).toISOString(),
      });

      const matchesStatus = !selectedStatus || created.status === selectedStatus;
      const matchesStage = !selectedStage || created.stage === selectedStage;

      if (matchesStatus && matchesStage) {
        setRecords((current) => {
          const withoutCreated = current.filter((record) => record.id !== created.id);
          return [created, ...withoutCreated];
        });
        setCreateState({ status: "success", message: "Candidatura creada con exito" });
      } else {
        setCreateState({
          status: "success",
          message: "Candidatura creada, pero los filtros actuales la ocultan",
        });
      }

      setCreateForm(initialCreateForm);
      setShowCreateForm(false);
    } catch (createError) {
      const message =
        createError instanceof Error
          ? createError.message
          : "No se pudo crear la candidatura";
      setCreateState({ status: "error", message });
    }
  }

  return (
    <main className="min-h-screen bg-slate-50 p-6 text-slate-800 md:p-10">
      <div className="mx-auto max-w-6xl space-y-6">
        <header className="rounded-2xl bg-white p-6 shadow-sm ring-1 ring-slate-200">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-sm font-medium uppercase tracking-wide text-teal-700">
                Nexova Solutions
              </p>
              <h1 className="mt-2 text-3xl font-semibold">Talent Pipeline Tracker</h1>
              <p className="mt-2 text-sm text-slate-600">
                Operaciones de Seleccion · Lider: Javier Almeida
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-3 text-sm">
              {user && <span className="text-slate-600">{user.email}</span>}
              <Link
                href="/account/profile"
                className="rounded-lg border border-slate-300 px-3 py-1.5 font-medium text-slate-700 transition hover:border-slate-400"
              >
                Mi cuenta
              </Link>
              <button
                type="button"
                onClick={logout}
                className="rounded-lg border border-slate-300 px-3 py-1.5 font-medium text-slate-700 transition hover:border-slate-400"
              >
                Cerrar sesion
              </button>
            </div>
          </div>
        </header>

        <section className="rounded-2xl bg-white p-6 shadow-sm ring-1 ring-slate-200">
          <div className="grid gap-4 md:grid-cols-4">
            <label className="space-y-1">
              <span className="text-sm font-medium text-slate-700">Buscar</span>
              <input
                value={searchInput}
                onChange={(event) => setSearchInput(event.target.value)}
                placeholder="Nombre o email"
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none transition focus:border-teal-500"
              />
            </label>

            <label className="space-y-1">
              <span className="text-sm font-medium text-slate-700">Estado</span>
              <select
                value={selectedStatus}
                onChange={(event) => updateFilter("status", event.target.value)}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none transition focus:border-teal-500"
              >
                <option value="">Todos</option>
                {STATUS_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="space-y-1">
              <span className="text-sm font-medium text-slate-700">Etapa</span>
              <select
                value={selectedStage}
                onChange={(event) => updateFilter("stage", event.target.value)}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none transition focus:border-teal-500"
              >
                <option value="">Todas</option>
                {STAGE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>

            <button
              type="button"
              onClick={() => setShowCreateForm((value) => !value)}
              className="mt-auto rounded-lg bg-teal-700 px-4 py-2 text-sm font-medium text-white transition hover:bg-teal-800"
            >
              {showCreateForm ? "Cerrar formulario" : "Nueva candidatura"}
            </button>
          </div>

          {createState.status === "loading" && (
            <p className="mt-4 text-sm text-slate-600">Cargando: guardando candidatura...</p>
          )}
          {createState.status === "success" && (
            <p className="mt-4 text-sm text-emerald-700">Exito: {createState.message}</p>
          )}
          {createState.status === "error" && (
            <p className="mt-4 text-sm text-rose-700">Error: {createState.message}</p>
          )}

          {showCreateForm && (
            <form onSubmit={handleCreateCandidate} className="mt-6 grid gap-3 md:grid-cols-2">
              <input
                required
                value={createForm.full_name}
                onChange={(event) =>
                  setCreateForm((current) => ({ ...current, full_name: event.target.value }))
                }
                placeholder="Nombre completo"
                className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
              <input
                required
                type="email"
                value={createForm.email}
                onChange={(event) =>
                  setCreateForm((current) => ({ ...current, email: event.target.value }))
                }
                placeholder="Email"
                className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
              <input
                required
                value={createForm.phone}
                onChange={(event) =>
                  setCreateForm((current) => ({ ...current, phone: event.target.value }))
                }
                placeholder="Telefono"
                className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
              <input
                required
                value={createForm.position}
                onChange={(event) =>
                  setCreateForm((current) => ({ ...current, position: event.target.value }))
                }
                placeholder="Puesto"
                className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
              <input
                required
                value={createForm.linkedin_url}
                onChange={(event) =>
                  setCreateForm((current) => ({ ...current, linkedin_url: event.target.value }))
                }
                placeholder="LinkedIn URL"
                className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
              <input
                required
                value={createForm.cv_url}
                onChange={(event) =>
                  setCreateForm((current) => ({ ...current, cv_url: event.target.value }))
                }
                placeholder="URL del CV"
                className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
              <input
                required
                type="number"
                min={0}
                step={1}
                value={createForm.experience_years}
                onChange={(event) =>
                  setCreateForm((current) => ({
                    ...current,
                    experience_years: Number(event.target.value),
                  }))
                }
                placeholder="Anios de experiencia"
                className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
              <input
                required
                type="date"
                value={createForm.applied_at}
                onChange={(event) =>
                  setCreateForm((current) => ({ ...current, applied_at: event.target.value }))
                }
                className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
              <input
                value={createForm.english_level ?? ""}
                onChange={(event) =>
                  setCreateForm((current) => ({ ...current, english_level: event.target.value }))
                }
                placeholder="Nivel de Ingles (ej. C1)"
                className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
              <div className="grid grid-cols-2 gap-3">
                <select
                  value={createForm.status}
                  onChange={(event) =>
                    setCreateForm((current) => ({
                      ...current,
                      status: event.target.value as CandidateStatus,
                    }))
                  }
                  className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
                >
                  {STATUS_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <select
                  value={createForm.stage}
                  onChange={(event) =>
                    setCreateForm((current) => ({
                      ...current,
                      stage: event.target.value as CandidateStage,
                    }))
                  }
                  className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
                >
                  {STAGE_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </div>

              <button
                type="submit"
                className="md:col-span-2 rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white"
              >
                Guardar candidatura
              </button>
            </form>
          )}
        </section>

        <section className="rounded-2xl bg-white p-6 shadow-sm ring-1 ring-slate-200">
          {loading && <p className="text-sm text-slate-600">Cargando candidaturas...</p>}

          {!loading && error && (
            <div role="alert" className="text-sm text-rose-700">
              <p className="font-medium">No se han podido cargar las candidaturas</p>
              <p className="mt-1">{error}</p>
              <div className="mt-3 flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  onClick={() => setAttempt((n) => n + 1)}
                  className="rounded-full border border-rose-300 bg-white px-4 py-1.5 font-medium text-rose-700 transition hover:bg-rose-50"
                >
                  Reintentar
                </button>
                <Link href="/" className="underline-offset-2 hover:underline">
                  Volver al inicio
                </Link>
              </div>
              <p className="mt-3 text-xs text-rose-700/80">
                Si el problema continúa, contacta con el equipo de soporte.
              </p>
            </div>
          )}

          {!loading && !error && filteredRecords.length === 0 && (
            <p className="text-sm text-slate-600">No hay candidaturas para los filtros actuales.</p>
          )}

          {!loading && !error && filteredRecords.length > 0 && (
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
                <thead>
                  <tr className="text-slate-500">
                    <th className="px-3 py-2 font-medium">Nombre</th>
                    <th className="px-3 py-2 font-medium">Puesto</th>
                    <th className="px-3 py-2 font-medium">Estado</th>
                    <th className="px-3 py-2 font-medium">Etapa</th>
                    <th className="px-3 py-2 font-medium">Accion</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {filteredRecords.map((record) => (
                    <tr key={record.id}>
                      <td className="px-3 py-3">{record.full_name}</td>
                      <td className="px-3 py-3">{record.position}</td>
                      <td className="px-3 py-3">{getStatusLabel(record.status)}</td>
                      <td className="px-3 py-3">{getStageLabel(record.stage)}</td>
                      <td className="px-3 py-3">
                        <Link
                          href={`/candidates/${record.id}`}
                          className="font-medium text-teal-700 hover:text-teal-800"
                        >
                          Ver detalle
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
